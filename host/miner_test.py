#!/usr/bin/env python3
"""
miner_test.py -- biblioteca + testes UART do minerador SHA-256d (Bitcoin) para Tang Primer 20K.
PROTOCOLO v3 (firmware build/v3_*.fs): preempcao de job, ID de job, checksum, STATUS,
zbits de 8 bits (0..255, compara o digest SHA-256d completo, nao so' os 32 bits do topo).

Requisitos:  pip install pyserial          (nao precisa de pyserial com --soft)

Testes:
  python miner_test.py --port COM5 ping
  python miner_test.py --port COM5 info
  python miner_test.py --port COM5 status           # STATUS ao vivo (job, lotes, ciclos)
  python miner_test.py --port COM5 genesis          # acha o nonce REAL do bloco genesis
  python miner_test.py --port COM5 verify           # FPGA x hashlib (conjunto exato)
  python miner_test.py --port COM5 preempt          # novo job interrompe o job em andamento
  python miner_test.py --port COM5 recover          # cancelou/travou e reiniciou: volta sozinha?
  python miner_test.py --port COM5 bench -t 10      # MH/s medido pelo relogio da FPGA
  python miner_test.py --port COM5 mine --zbits 28
  python miner_test.py --soft all                   # testa este script contra um modelo em software

USO COMO BIBLIOTECA (loop de mineracao com preempcao):
  m = Miner(serial.Serial(porta, 115200, timeout=0.05)); m.connect()   # resync + versao
  m.start_job(header80, 0, 0, 32)        # roda "para sempre"
  while True:
      kind, v = m.poll(0.2)              # so' mensagens do job ATUAL (velhas sao descartadas)
      if kind == "found": enviar_share(v)
      if chegou_bloco_novo:              # interrompe o job atual e comeca outro, na hora
          m.start_job(novo_header80, 0, 0, 32)

Protocolo (8N1, big-endian) -- veja rtl/miner_top.v:
  PC->FPGA  0x01 JOB(id,mid,w,nonce0,lotes,zbits,chk) | 0x02 INFO | 0x03 STOP | 0x05 PING+1B | 0x06 STATUS
  FPGA->PC  0x80 FOUND id nonce | 0x81 DONE id lotes ciclos | 0x82 INFO | 0x83 STATUS
            0x85 PING | 0x86 NAK cod | 0x87 ACK id
"""
import argparse, hashlib, os, random, struct, sys, time

M32 = 0xFFFFFFFF
CPB_DEFAULT = 148          # ciclos por lote (LOAD 4 + P1 64 + MS 8 + P2 64 + CHK 8) -- so' fallback, o INFO real sobrescreve

# ----------------------------------------------------------------------------
#  SHA-256 em Python puro (so' para calcular o MIDSTATE; hashlib nao o expoe)
# ----------------------------------------------------------------------------
_K = [
 0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
 0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
 0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
 0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
 0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
 0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
 0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
 0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]
_IV = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]

def _rotr(x, n): return ((x >> n) | (x << (32 - n))) & M32

def sha256_compress(state, block64):
    w = list(struct.unpack(">16I", block64))
    for t in range(16, 64):
        s0 = _rotr(w[t-15], 7) ^ _rotr(w[t-15], 18) ^ (w[t-15] >> 3)
        s1 = _rotr(w[t-2], 17) ^ _rotr(w[t-2], 19) ^ (w[t-2] >> 10)
        w.append((w[t-16] + s0 + w[t-7] + s1) & M32)
    a, b, c, d, e, f, g, h = state
    for t in range(64):
        S1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25)
        ch = (e & f) ^ (~e & M32 & g)
        t1 = (h + S1 + ch + _K[t] + w[t]) & M32
        S0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22)
        mj = (a & b) ^ (a & c) ^ (b & c)
        h, g, f, e, d, c, b, a = g, f, e, (d + t1) & M32, c, b, a, (t1 + S0 + mj) & M32
    return [(x + y) & M32 for x, y in zip(state, [a, b, c, d, e, f, g, h])]

def midstate(header80):
    return sha256_compress(_IV, header80[:64])

def dsha256(b): return hashlib.sha256(hashlib.sha256(b).digest()).digest()

def hash_top_zero(hash_bytes, zbits):
    """True se os 'zbits' bits mais significativos do hash (como numero) sao zero."""
    return zbits == 0 or (int.from_bytes(hash_bytes, "little") >> (256 - zbits)) == 0

# Bloco genesis do Bitcoin (nonce = 2083236893 = 0x7c2bac1d)
GENESIS_HDR = (bytes.fromhex("01000000" + "00" * 32 +
               "3ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a"
               "29ab5f49" "ffff001d") + struct.pack("<I", 2083236893))
GENESIS_HASH = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"


# ----------------------------------------------------------------------------
#  Protocolo v2
# ----------------------------------------------------------------------------
PROTO_VERSION = 3
BODY = {0x80: 5, 0x81: 9, 0x82: 4, 0x83: 10, 0x85: 1, 0x86: 1, 0x87: 1}   # bytes apos o tipo

def build_job_frame(job_id, header80, nonce0, nbatches, zbits):
    """Frame JOB de 56 bytes: 0x01 | id | mid(32) | w0,w1,w2(12) | nonce0(4) | lotes(4) | zbits | chk."""
    mid = midstate(header80)
    w = struct.unpack(">3I", header80[64:76])
    payload = (bytes([job_id]) + struct.pack(">8I", *mid) + struct.pack(">3I", *w)
               + struct.pack(">II", nonce0 & M32, nbatches) + bytes([zbits]))
    assert len(payload) == 54
    chk = 0xA5
    for b in payload: chk ^= b
    return b"\x01" + payload + bytes([chk])

class FpgaError(RuntimeError): pass

class Miner:
    def __init__(self, ser):
        self.ser = ser
        self.n_cores = None; self.clk_mhz = None; self.cpb = CPB_DEFAULT; self.version = None
        self.job_id = 0          # id do job ATUAL (mensagens de outros ids sao descartadas)
        self.stale = 0           # quantas mensagens de jobs antigos foram descartadas
        self.last_ack_ms = 0.0   # latencia do ultimo start_job (ate' o ACK da FPGA)

    # ---------------- baixo nivel ----------------
    def _read(self, n, timeout):
        end = time.time() + timeout; buf = b""
        while len(buf) < n and time.time() < end:
            buf += self.ser.read(n - len(buf))
        if len(buf) < n: raise TimeoutError(f"timeout lendo {n} bytes (recebi {len(buf)}: {buf.hex()})")
        return buf

    def read_msg(self, timeout=2.0):
        t = self._read(1, timeout)[0]
        n = BODY.get(t)
        if n is None: return ("?", t)
        p = self._read(n, 0.5)                     # corpo chega colado (~1 ms); nao dessincroniza
        if t == 0x80: return ("found", p[0], struct.unpack(">I", p[1:5])[0])
        if t == 0x81: return ("done", p[0], *struct.unpack(">II", p[1:9]))
        if t == 0x82: return ("info", *p)
        if t == 0x83: return ("status", p[0], p[1], *struct.unpack(">II", p[2:10]))
        if t == 0x85: return ("ping", p[0])
        if t == 0x86: return ("nak", p[0])
        return ("ack", p[0])

    def drain(self, quiet=0.25, max_time=2.5):
        """Descarta tudo que a FPGA estiver mandando ate' ficar 'quiet' s em silencio.
        LIMITADO por max_time (o flush antigo podia girar para sempre com a FPGA metralhando)."""
        end = time.time() + max_time; last = time.time(); n = 0
        while time.time() < end and time.time() - last < quiet:
            d = self.ser.read(4096)
            if d: last = time.time(); n += len(d)
        return n

    def _wait(self, match, timeout):
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0: raise TimeoutError("timeout esperando resposta da FPGA")
            try: msg = self.read_msg(left)
            except TimeoutError: raise TimeoutError("timeout esperando resposta da FPGA")
            r = match(msg)
            if r is not None: return r

    # ---------------- comandos ----------------
    def ping(self, b=0x5A, timeout=1.0):
        self.ser.write(bytes([0x05, b]))
        try: self._wait(lambda m: True if (m[0] == "ping" and m[1] == b) else None, timeout); return True
        except TimeoutError: return False

    def info(self):
        self.ser.write(b"\x02")
        m = self._wait(lambda m: m if m[0] == "info" else None, 1.0)
        _, self.n_cores, self.clk_mhz, self.cpb, self.version = m
        return dict(cores=self.n_cores, clk_mhz=self.clk_mhz, cycles_per_batch=self.cpb, version=self.version)

    def status(self):
        """Estado AO VIVO (sem parar o job): {job_id, active, batches, cycles}."""
        self.ser.write(b"\x06")
        m = self._wait(lambda m: m if m[0] == "status" else None, 1.0)
        return dict(job_id=m[1], active=bool(m[2]), batches=m[3], cycles=m[4])

    def request_status(self):
        self.ser.write(b"\x06")            # resposta chega via poll() como ('status', dict)

    def connect(self, retries=3):
        """Leva PC e FPGA a um estado conhecido, de QUALQUER situacao anterior: job infinito ainda
        rodando, FPGA 'metralhando' FOUND, frame de JOB pela metade. Sequencia deterministica:
        64x 0x00 (completa/descarta frame parcial) -> STOP (aborta job, descarta hits) -> drena -> PING."""
        for _ in range(retries):
            self.ser.write(b"\x00" * 64); time.sleep(0.06)
            self.ser.write(b"\x03")
            self.drain(quiet=0.3, max_time=3.0)
            if not self.ping(0xA5, 1.0): continue
            info = self.info()
            if info["version"] != PROTO_VERSION:
                raise FpgaError(f"firmware com protocolo v{info['version']} (este script e' v{PROTO_VERSION}). "
                                f"Grave um bitstream v3_*.fs (zbits e' 0..255, digest completo de 256 bits).")
            self.job_id = self.status()["job_id"]      # continua a numeracao de ids do firmware
            return info
        raise FpgaError("FPGA nao responde apos 3 tentativas de resync. Confira porta/baud, o DIP-switch "
                        "do Dock e se a bitstream esta gravada; se persistir, regrave a bitstream.")

    def stop(self, timeout=2.0):
        """Aborta o job atual. Retorna (found_list, (lotes, ciclos)). A FPGA SEMPRE responde DONE."""
        self.ser.write(b"\x03"); found = []
        def match(m):
            if m[0] == "found" and m[1] == self.job_id: found.append(m[2])
            elif m[0] == "done" and m[1] == self.job_id: return (m[2], m[3])
            elif m[0] in ("found", "done"): self.stale += 1
        return found, self._wait(match, timeout)

    def aligned_start(self, nonce0):
        n = self.n_cores
        return nonce0 & ~(n - 1) & M32 if n & (n - 1) == 0 else nonce0 & M32

    def start_job(self, header80, nonce0, nbatches, zbits, timeout=1.5):
        """Inicia um job, INTERROMPENDO o que estiver rodando (troca atomica na FPGA: o job antigo
        continua ate' o frame novo chegar inteiro e valido). Espera o ACK. Retorna o nonce inicial
        efetivo (alinhado a N se N potencia de 2)."""
        jid = (self.job_id % 255) + 1
        frame = build_job_frame(jid, header80, nonce0, nbatches, zbits)
        for attempt in range(3):
            self.ser.write(frame); t0 = time.time()
            def match(m):
                if m[0] == "ack" and m[1] == jid: return "ack"
                if m[0] == "nak" and m[1] == 1: return "nak"       # checksum ruim -> reenvia
                if m[0] in ("found", "done"): self.stale += 1        # job velho ainda escoando
            if self._wait(match, timeout) == "ack":
                self.job_id = jid; self.last_ack_ms = (time.time() - t0) * 1000
                return self.aligned_start(nonce0)
        raise FpgaError("FPGA rejeitou o frame JOB 3x (checksum). Cabo/baud ruim?")

    def poll(self, timeout=0.15):
        """Proxima mensagem do job ATUAL: ('found', nonce) | ('done', (lotes, ciclos)) | ('status', dict).
        Mensagens de jobs antigos sao descartadas (self.stale). TimeoutError se nada chegar."""
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0: raise TimeoutError("poll")
            m = self.read_msg(left)
            if m[0] == "found":
                if m[1] == self.job_id: return ("found", m[2])
                self.stale += 1
            elif m[0] == "done":
                if m[1] == self.job_id: return ("done", (m[2], m[3]))
                self.stale += 1
            elif m[0] == "status" and m[1] == self.job_id:
                return ("status", dict(active=bool(m[2]), batches=m[3], cycles=m[4]))

    def collect(self, timeout):
        """Le FOUND ate' DONE do job atual. Retorna (lista_nonces, (lotes, ciclos))."""
        found = []; end = time.time() + timeout
        while time.time() < end:
            try: kind, v = self.poll(max(0.05, end - time.time()))
            except TimeoutError: break
            if kind == "found": found.append(v)
            elif kind == "done": return found, v
        raise TimeoutError("DONE nao recebido")

# ----------------------------------------------------------------------------
#  Modelo em software do protocolo v2 (para testar este script sem hardware): --soft
#  Emula o parser byte a byte como o RTL (checksum, STOP sempre responde DONE, ids, preempcao)
#  e minera "aos poucos" a cada leitura, entao o job fica mesmo em andamento entre chamadas.
# ----------------------------------------------------------------------------
class SoftFPGA:
    header_hook = None
    def __init__(self, n=8, clk_mhz=67, cpb=CPB_DEFAULT, version=3):
        self.n, self.clk, self.cpb, self.version = n, clk_mhz, cpb, version
        self.tx = bytearray(); self.timeout = 0.002
        self.cs = 0; self.cnt = 0; self.acc = 0; self.frame = bytearray()
        self.job = None; self.job_id = 0; self.active = False
        self.last_batches = 0; self.last_cycles = 0
    def close(self): pass
    def open(self): pass
    def write(self, data):
        for b in data: self._rx(b)
    def _rx(self, b):
        if self.cs == 0:
            if b == 0x01: self.cs, self.cnt, self.acc, self.frame = 1, 0, 0xA5, bytearray()
            elif b == 0x02: self.tx += bytes([0x82, self.n, self.clk, self.cpb, self.version])
            elif b == 0x03: self._stop()
            elif b == 0x05: self.cs = 2
            elif b == 0x06: self._status()
        elif self.cs == 1:
            if self.cnt < 54: self.acc ^= b; self.frame.append(b); self.cnt += 1
            else:
                self.cs = 0
                if self.acc == b: self._new_job(bytes(self.frame))
                else: self.tx += bytes([0x86, 1])
        elif self.cs == 2:
            self.tx += bytes([0x85, b]); self.cs = 0
    def _cur_batches(self): return self.job["done"] // self.n if self.job else 0
    def _new_job(self, f):
        hdr = SoftFPGA.header_hook
        nonce0, nb, zb = struct.unpack(">IIB", f[45:54])
        assert struct.unpack(">8I", f[1:33]) == tuple(midstate(hdr)), "midstate enviado difere!"
        if self.n & (self.n - 1) == 0: nonce0 &= ~(self.n - 1) & M32
        self.job_id = f[0]
        self.job = dict(hdr76=hdr[:76], base=nonce0, zb=zb, done=0, total=None if nb == 0 else nb * self.n)
        self.active = True; self.tx += bytes([0x87, self.job_id])
    def _stop(self):
        if self.active:
            self.active = False; self.last_batches = self._cur_batches()
            self.last_cycles = self.last_batches * self.cpb + 16
        self.tx += bytes([0x81, self.job_id]) + struct.pack(">II", self.last_batches, self.last_cycles)
    def _status(self):
        b = self._cur_batches() if self.active else self.last_batches
        self.tx += bytes([0x83, self.job_id, int(self.active)]) + struct.pack(">II", b, b * self.cpb + 16)
    def _advance(self):
        j = self.job
        if not self.active or j is None: return
        budget = 4096
        while budget > 0 and len(self.tx) < 64:
            if j["total"] is not None and j["done"] >= j["total"]:
                self.active = False; self.last_batches = j["done"] // self.n
                self.last_cycles = self.last_batches * self.cpb + 16
                self.tx += bytes([0x81, self.job_id]) + struct.pack(">II", self.last_batches, self.last_cycles)
                return
            nn = (j["base"] + j["done"]) & M32
            j["done"] += 1; budget -= 1
            if hash_top_zero(dsha256(j["hdr76"] + struct.pack("<I", nn)), j["zb"]):
                self.tx += bytes([0x80, self.job_id]) + struct.pack(">I", nn)
    def read(self, n):
        self._advance()
        out = bytes(self.tx[:n]); del self.tx[:n]
        if not out: time.sleep(self.timeout)
        return out

# ----------------------------------------------------------------------------
#  Testes
# ----------------------------------------------------------------------------
TRUE_NONCE = 2083236893

def ok(cond, msg):
    print(("  [PASS] " if cond else "  [FAIL] ") + msg); return cond

def _rand_hdr(seed):
    rng = random.Random(seed)
    return bytes(rng.getrandbits(8) for _ in range(76)) + b"\0\0\0\0"

def t_ping(m, a):
    print("== ping (enlace UART)")
    return ok(all(m.ping(b) for b in (0x00, 0x5A, 0xA5, 0xFF)), "eco de 4 bytes (0x00 0x5A 0xA5 0xFF)")

def t_info(m, a):
    print("== info"); i = m.info(); print("  ", i)
    hr = i["cores"] * i["clk_mhz"] / i["cycles_per_batch"]
    print(f"   taxa teorica = {i['cores']} cores x {i['clk_mhz']} MHz / {i['cycles_per_batch']} = {hr:.2f} MH/s")
    return ok(i["version"] == PROTO_VERSION, f"firmware protocolo v{i['version']}")

def t_status(m, a):
    print("== status (ao vivo)")
    if not m.n_cores: m.info()
    s0 = m.status(); print("   ocioso:", s0)
    hdr = _rand_hdr(3); SoftFPGA.header_hook = hdr
    m.start_job(hdr, 0, 0, 32); time.sleep(0.3)
    s1 = m.status(); time.sleep(0.3); s2 = m.status(); m.stop()
    print("   rodando:", s1, "->", s2)
    return ok(s1["active"] and s2["active"] and s2["batches"] > s1["batches"] and s2["job_id"] == m.job_id,
              "job ativo, lotes crescendo, id correto")

def t_genesis(m, a):
    print("== genesis (bloco #0 real do Bitcoin, nonce 0x7c2bac1d)")
    if not m.n_cores: m.info()
    assert dsha256(GENESIS_HDR)[::-1].hex() == GENESIS_HASH
    start = TRUE_NONCE - 3 * m.n_cores - 5; nb = 8
    zb = a.zbits if a.zbits else 32
    SoftFPGA.header_hook = GENESIS_HDR
    eff = m.start_job(GENESIS_HDR, start, nb, zb)
    found, (batches, cycles) = m.collect(20)
    total = nb * m.n_cores
    exp = [(eff + k) & M32 for k in range(total)
           if hash_top_zero(dsha256(GENESIS_HDR[:76] + struct.pack("<I", (eff + k) & M32)), zb)]
    print(f"   varredura {eff:#010x} .. {(eff + total - 1) & M32:#010x}  zbits={zb}")
    print(f"   FPGA achou : {[hex(x) for x in found]}")
    print(f"   esperado   : {[hex(x) for x in exp]}")
    r = ok(sorted(found) == sorted(exp), "FPGA achou exatamente o conjunto esperado")
    r &= ok(batches == nb, f"lotes completos = {batches}/{nb}")
    for n in found:
        h = dsha256(GENESIS_HDR[:76] + struct.pack("<I", n)); good = hash_top_zero(h, zb)
        r &= ok(good, f"nonce {n:#x}: hash real = {h[::-1].hex()}  (bate com hashlib: {good})")
    return r

def t_verify(m, a):
    print("== verify (FPGA x hashlib, conjunto exato de hits)")
    if not m.n_cores: m.info()
    rng = random.Random(a.seed)
    hdr = bytes(rng.getrandbits(8) for _ in range(76)) + b"\0\0\0\0"
    nb = max(1, (a.range // m.n_cores)); zb = a.zbits if a.zbits else 8
    nonce0 = rng.getrandbits(32); SoftFPGA.header_hook = hdr
    eff = m.start_job(hdr, nonce0, nb, zb)
    t0 = time.time(); found, (batches, cycles) = m.collect(120); dt = time.time() - t0
    total = nb * m.n_cores
    print(f"   {total} nonces a partir de {eff:#010x}, zbits={zb}, FPGA: {len(found)} hits em {dt:.2f}s")
    t0 = time.time()
    exp = [(eff + k) & M32 for k in range(total)
           if hash_top_zero(dsha256(hdr[:76] + struct.pack("<I", (eff + k) & M32)), zb)]
    print(f"   hashlib: {len(exp)} hits ({time.time()-t0:.1f}s)")
    fset, eset = set(found), set(exp)
    faltando = sorted(eset - fset); sobrando = sorted(fset - eset)
    if faltando: print(f"   faltando (hashlib achou, FPGA nao): {[hex(x) for x in faltando][:10]}")
    if sobrando:
        print(f"   sobrando (FPGA achou, hashlib nao):  {[hex(x) for x in sobrando][:10]}")
        for n in sobrando[:3]:
            h = dsha256(hdr[:76] + struct.pack("<I", n))
            print(f"     nonce {n:#x} -> hash real (hashlib) = {h[::-1].hex()}  (nao bate com o alvo)")
    r = ok(sorted(found) == sorted(exp), "conjunto de nonces IDENTICO")
    r &= ok(len(set(found)) == len(found), "sem duplicados")
    r &= ok(batches == nb, f"lotes = {batches}/{nb}")
    return r

def t_preempt(m, a):
    print(f"== preempt ({a.rounds} rodadas: um job novo interrompe o job em andamento)")
    if not m.n_cores: m.info()
    r = True
    for i in range(a.rounds):
        hdr = _rand_hdr(100 + i); SoftFPGA.header_hook = hdr
        m.start_job(hdr, 0, 0, 16)                       # infinito, alvo facil (hits frequentes)
        time.sleep(0.3); s0 = m.status(); time.sleep(0.2); s1 = m.status()
        rodando = s1["active"] and s1["batches"] > s0["batches"]
        SoftFPGA.header_hook = GENESIS_HDR; stale0 = m.stale
        m.start_job(GENESIS_HDR, TRUE_NONCE - 3 * m.n_cores - 5, 8, 32)   # PREEMPTA
        lat = m.last_ack_ms
        found, (b, c) = m.collect(10)
        good = rodando and found == [TRUE_NONCE] and b == 8
        r &= ok(good, f"rodada {i+1}: antigo rodando={rodando}; ACK em {lat:.1f} ms; achou={[hex(x) for x in found]}; "
                      f"lotes={b}; msgs velhas descartadas={m.stale - stale0}")
    return r

def _reopen(ser):
    try: ser.close(); time.sleep(0.5); ser.open(); time.sleep(0.2)
    except Exception as e: print(f"   (nao consegui fechar/abrir a porta: {e})")

def t_recover(m, a):
    print("== recover (programa cancelado -> reiniciado: a FPGA tem que voltar sozinha)")
    if not m.n_cores: m.info()
    r = True
    # cenario 1: job infinito com TODO nonce sendo hit (FPGA 'metralhando'), programa some sem STOP
    hdr = _rand_hdr(9); SoftFPGA.header_hook = hdr
    m.start_job(hdr, 0, 0, 0); time.sleep(0.3)
    _reopen(m.ser); m2 = Miner(m.ser); t0 = time.time()
    try: m2.connect(); r &= ok(True, f"reconectou com a FPGA metralhando em {time.time()-t0:.2f}s")
    except FpgaError as e: r &= ok(False, f"reconexao apos metralhadora: {e}"); return r
    # cenario 2: programa morre no meio do envio de um JOB (frame pela metade)
    m2.ser.write(build_job_frame(200, hdr, 0, 0, 0)[:23]); time.sleep(0.05)
    _reopen(m2.ser); m3 = Miner(m2.ser); t0 = time.time()
    try: m3.connect(); r &= ok(True, f"reconectou com frame pela metade em {time.time()-t0:.2f}s")
    except FpgaError as e: r &= ok(False, f"reconexao apos frame parcial: {e}"); return r
    # cenario 3: depois de tudo isso a mineracao funciona
    SoftFPGA.header_hook = GENESIS_HDR
    m3.start_job(GENESIS_HDR, TRUE_NONCE - 3 * m3.n_cores - 5, 8, 32)
    found, _ = m3.collect(10)
    r &= ok(found == [TRUE_NONCE], f"genesis apos a recuperacao: achou {[hex(x) for x in found]}")
    m.job_id = m3.job_id
    return r

def t_bench(m, a):
    print(f"== bench ({a.time:.0f}s, medido pelo STATUS da FPGA)")
    if not m.n_cores: m.info()
    if isinstance(m.ser, SoftFPGA): print("  (modelo em software: sem medida de desempenho)"); return True
    hdr = _rand_hdr(7)
    m.start_job(hdr, 0, 0, 32)
    time.sleep(0.5); s0 = m.status(); t0 = time.time()
    time.sleep(a.time); s1 = m.status(); dt = time.time() - t0
    m.stop()
    db, dc = s1["batches"] - s0["batches"], s1["cycles"] - s0["cycles"]
    hashes = db * m.n_cores; f_hz = m.clk_mhz * 1e6
    print(f"   lotes={db}  nonces={hashes}  ciclos(FPGA)={dc}")
    print(f"   taxa (relogio FPGA) = {hashes / (dc / f_hz) / 1e6:.3f} MH/s   |  pelo relogio do PC = {hashes / dt / 1e6:.3f} MH/s")
    print(f"   eficiencia = {db * m.cpb / dc * 100:.1f}% dos ciclos em lotes uteis")
    return True

def t_mine(m, a):
    print(f"== mine (header aleatorio, zbits={a.zbits or 24})")
    if not m.n_cores: m.info()
    zb = a.zbits or 24
    hdr = bytes(random.SystemRandom().getrandbits(8) for _ in range(76)) + b"\0\0\0\0"
    SoftFPGA.header_hook = hdr
    m.start_job(hdr, 0, 0, zb); t0 = time.time()
    while time.time() - t0 < a.time * 6:
        try: kind, v = m.poll(0.5)
        except TimeoutError: continue
        if kind == "found":
            h = dsha256(hdr[:76] + struct.pack("<I", v)); good = hash_top_zero(h, zb)
            m.stop()
            print(f"   nonce {v:#010x} em {time.time()-t0:.2f}s  hash={h[::-1].hex()}")
            return ok(good, f"verificado com hashlib ({zb} bits zero no topo)")
    m.stop(); return ok(False, "nenhum resultado no tempo limite")

TESTS = {"ping": t_ping, "info": t_info, "status": t_status, "genesis": t_genesis, "verify": t_verify,
         "preempt": t_preempt, "recover": t_recover, "bench": t_bench, "mine": t_mine}

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("test", choices=list(TESTS) + ["all"], help="teste a executar")
    ap.add_argument("--port", "-p", help="porta serial (COM5, /dev/ttyUSB1 ...)")
    ap.add_argument("--baud", "-b", type=int, default=115200)
    ap.add_argument("--soft", action="store_true", help="usa modelo em software (sem placa)")
    ap.add_argument("--time", "-t", type=float, default=5.0, help="duracao do bench (s)")
    ap.add_argument("--zbits", "-z", type=int, default=0, help="bits zero exigidos no topo do hash")
    ap.add_argument("--range", type=int, default=1 << 18, help="nonces do teste verify")
    ap.add_argument("--rounds", type=int, default=5, help="rodadas do teste preempt")
    ap.add_argument("--seed", type=int, default=1234)
    a = ap.parse_args()

    if a.soft:
        a.range = min(a.range, 1 << 12); ser = SoftFPGA(); print("[modo --soft: modelo em software do protocolo v3]")
    else:
        if not a.port: ap.error("--port e' obrigatorio (ou use --soft)")
        import serial
        ser = serial.Serial(a.port, a.baud, timeout=0.05)
    m = Miner(ser)
    try:
        info = m.connect()
        print(f"[conectado] {info}")
    except FpgaError as e:
        print(f"[ERRO] {e}"); sys.exit(2)
    names = ["ping", "info", "status", "genesis", "verify", "preempt", "recover", "bench"] if a.test == "all" else [a.test]
    res = {}
    try:
        for n in names:
            try: res[n] = TESTS[n](m, a)
            except FpgaError as e: print(f"  [ERRO] {e}"); res[n] = False
            except Exception as e: print(f"  [ERRO] {type(e).__name__}: {e}"); res[n] = False
    finally:
        try: m.stop(timeout=1.0)              # nunca deixa um job rodando ao sair (nem com Ctrl+C)
        except Exception: pass
    print("\nResumo:", ", ".join(f"{k}={'OK' if v else 'FALHOU'}" for k, v in res.items()))
    sys.exit(0 if all(res.values()) else 1)

if __name__ == "__main__":
    main()
