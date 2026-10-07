#!/usr/bin/env python3
"""btc_miner.py -- minerador solo de Bitcoin: RPC (login/senha) + FPGA.

Fluxo:
  1. getblocktemplate (RPC) -> bloco candidato
  2. reordena as txs (dependencias + taxa de ancestrais, montador embutido)
  3. monta coinbase p/ o SEU endereco + commitment segwit + merkle + header
  4. manda o header p/ a FPGA (mesma API Miner do genesis_scan.py)
  5. vigila o RPC: se a ponta da cadeia mudar (bloco novo descoberto) cancela
     o job atual e recomeca com um candidato novo; tambem renova o template
     a cada --refresh s para pegar taxas novas
  6. quando a FPGA acha um nonce, confere o hash contra o alvo COMPLETO em
     software e envia o bloco com submitblock

Uso:
    pip install pyserial
    export BTC_RPC_USER=meuuser BTC_RPC_PASS=minhasenha
    python btc_miner.py --port /dev/ttyUSB1 --address bc1q... \
        --rpc-url http://127.0.0.1:8332

Requer na mesma pasta: miner_test.py (host da FPGA). O montador de bloco
esta embutido neste arquivo.
O nó precisa estar sincronizado e com rpc habilitado (bitcoin.conf: server=1).
"""
import argparse, base64, hashlib, heapq, json, os, random, struct, sys, threading, time
import urllib.request

# =============== MONTADOR DE BLOCO (ex-block_assembler.py) ===============
MAX_WEIGHT = 4_000_000
RESERVED_WEIGHT = 4_000  # folga p/ header, contador de txs e coinbase


def sha256d(b):
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def varint(n):
    if n < 0xfd: return bytes([n])
    if n <= 0xffff: return b"\xfd" + struct.pack("<H", n)
    if n <= 0xffffffff: return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def select_transactions(pool, max_weight=MAX_WEIGHT - RESERVED_WEIGHT):
    """Retorna (lista de txids em ordem valida, taxa total, peso total)."""
    # ignora txs cujos pais nao estao no mempool nem sao conhecidos
    children = {t: [] for t in pool}
    parents = {}
    for t, e in pool.items():
        parents[t] = [p for p in e["depends"] if p in pool]
        for p in parents[t]:
            children[p].append(t)

    def ancestors(t, memo={}):
        seen, stack = set(), [t]
        while stack:
            x = stack.pop()
            for p in parents[x]:
                if p not in seen:
                    seen.add(p); stack.append(p)
        return seen

    anc = {t: ancestors(t) for t in pool}
    chosen, order = set(), []
    total_w = total_f = 0
    version = {t: 0 for t in pool}

    def stats(t):
        s = [x for x in anc[t] if x not in chosen] + [t]
        return sum(pool[x]["fee"] for x in s), sum(pool[x]["weight"] for x in s), s

    heap = []
    for t in pool:
        f, w, _ = stats(t)
        heapq.heappush(heap, (-f / w, t, 0))

    while heap:
        negrate, t, ver = heapq.heappop(heap)
        if t in chosen or ver != version[t]:
            continue
        f, w, pkg = stats(t)
        if total_w + w > max_weight:
            continue  # pacote nao cabe; tenta o proximo
        # ordem topologica do pacote: pais antes dos filhos
        pkg_set = set(pkg)
        done, topo = set(), []

        def visit(x):
            if x in done: return
            done.add(x)
            for p in parents[x]:
                if p in pkg_set: visit(p)
            topo.append(x)
        for x in sorted(pkg, key=lambda z: len(anc[z])):
            visit(x)
        for x in topo:
            chosen.add(x); order.append(x)
        total_w += w; total_f += f
        # descendentes tem estatisticas novas
        stack = list(children[t])
        seen = set()
        while stack:
            d = stack.pop()
            if d in seen or d in chosen: continue
            seen.add(d)
            stack.extend(children[d])
            version[d] += 1
            df, dw, _ = stats(d)
            heapq.heappush(heap, (-df / dw, d, version[d]))
    return order, total_f, total_w


def check_order(order, pool):
    pos = {t: i for i, t in enumerate(order)}
    for t in order:
        for p in pool[t]["depends"]:
            if p in pool and (p not in pos or pos[p] > pos[t]):
                return False
    return True


# ----------------------------- coinbase / merkle -----------------------------
def build_coinbase(height, extranonce, value, payout_script, wit_commit=None):
    h = height.to_bytes((height.bit_length() + 8) // 8, "little")
    script_sig = bytes([len(h)]) + h + extranonce
    outs = [(value, payout_script)]
    if wit_commit is not None:
        outs.append((0, b"\x6a\x24\xaa\x21\xa9\xed" + wit_commit))
    body_in = (b"\x00" * 32 + b"\xff\xff\xff\xff" + varint(len(script_sig))
               + script_sig + b"\xff\xff\xff\xff")
    body_out = varint(len(outs)) + b"".join(
        struct.pack("<q", v) + varint(len(s)) + s for v, s in outs)
    base = struct.pack("<i", 2) + b"\x01" + body_in + body_out
    legacy = base + struct.pack("<I", 0)
    if wit_commit is None:
        return legacy, sha256d(legacy)
    # forma segwit (com testemunha reservada de 32 zeros); txid = hash da forma legada
    full = (struct.pack("<i", 2) + b"\x00\x01" + b"\x01" + body_in
            + body_out + b"\x01\x20" + b"\x00" * 32 + struct.pack("<I", 0))
    return full, sha256d(legacy)


def merkle_root_from(leaves):
    lvl = list(leaves)
    while len(lvl) > 1:
        if len(lvl) % 2: lvl.append(lvl[-1])
        lvl = [sha256d(lvl[i] + lvl[i + 1]) for i in range(0, len(lvl), 2)]
    return lvl[0]


def witness_commitment(wtxids_le):
    root = merkle_root_from([b"\x00" * 32] + wtxids_le)
    return sha256d(root + b"\x00" * 32)


def merkle_branch(leaves_after_coinbase):
    """Ramo de merkle da coinbase (posicao 0): so ele muda com o extranonce."""
    branch, lvl = [], [b"\x00" * 32] + list(leaves_after_coinbase)
    while len(lvl) > 1:
        if len(lvl) % 2: lvl.append(lvl[-1])
        branch.append(lvl[1])
        lvl = [sha256d(lvl[i] + lvl[i + 1]) for i in range(0, len(lvl), 2)]
    return branch


def root_with_coinbase(cb_txid, branch):
    h = cb_txid
    for s in branch:
        h = sha256d(h + s)
    return h


def bits_to_target(bits):
    exp, mant = bits >> 24, bits & 0xffffff
    return mant << (8 * (exp - 3))



# ------------------------------- RPC -------------------------------
class Rpc:
    def __init__(self, url, user, password, timeout=30):
        self.url, self.timeout = url, timeout
        tok = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Content-Type": "application/json",
                        "Authorization": "Basic " + tok}
        self._id = 0

    def call(self, method, *params):
        self._id += 1
        body = json.dumps({"jsonrpc": "1.0", "id": self._id,
                           "method": method, "params": list(params)}).encode()
        req = urllib.request.Request(self.url, body, self.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:  # bitcoind devolve erro RPC com HTTP 500
            resp = json.loads(e.read() or b"{}")
            if not resp.get("error"):
                raise
        if resp.get("error"):
            raise RuntimeError(f"RPC {method}: {resp['error']}")
        return resp["result"]



def preflight(rpc, url):
    """Confere conexao/credencial/sincronizacao do no e explica a causa se falhar."""
    import urllib.error
    try:
        info = rpc.call("getblockchaininfo")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("[RPC] 401 nao autorizado: usuario/senha nao conferem com o "
                     "bitcoin.conf (rpcuser/rpcpassword ou cookie .cookie).")
        if e.code == 403:
            sys.exit("[RPC] 403: o no recusou este IP. Ajuste rpcallowip/rpcbind no "
                     "bitcoin.conf.")
        sys.exit(f"[RPC] HTTP {e.code} em {url}")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        sys.exit(
            f"[RPC] nao consegui conectar em {url} ({e}).\n"
            "  1) O bitcoind esta rodando? (bitcoin-cli getblockchaininfo no no)\n"
            "  2) bitcoin.conf precisa de:  server=1  rpcuser=...  rpcpassword=...\n"
            "  3) Porta: mainnet 8332, testnet 18332, signet 38332, regtest 18443.\n"
            "  4) No em OUTRA maquina? use --rpc-url http://IP:8332 e no bitcoin.conf:\n"
            "     rpcbind=0.0.0.0  rpcallowip=IP_DESTA_MAQUINA  (so em rede confiavel)\n"
            "  5) Rodou com 'sudo'/Docker/WSL? 127.0.0.1 pode ser outro host.")
    except RuntimeError as e:
        sys.exit(f"[RPC] {e}")
    print(f"[RPC] conectado: chain={info['chain']} altura={info['blocks']} "
          f"cabecalhos={info['headers']}")
    if info.get("initialblockdownload") or info["blocks"] < info["headers"] - 1:
        sys.exit("[RPC] o no ainda esta sincronizando (IBD); minerar agora geraria "
                 "blocos sobre uma ponta velha. Espere terminar.")


# ------------------------------- candidato -------------------------------
class Job:
    """Candidato montado a partir de um getblocktemplate."""

    def __init__(self, tmpl, payout_script):
        self.tmpl = tmpl
        self.height = tmpl["height"]
        self.prev = bytes.fromhex(tmpl["previousblockhash"])[::-1]
        self.version = tmpl["version"]
        self.bits = int(tmpl["bits"], 16)
        self.target = int(tmpl["target"], 16)
        self.time = max(int(tmpl.get("mintime", 0)), int(time.time()))
        self.value = tmpl["coinbasevalue"]
        self.payout = payout_script

        txs = tmpl["transactions"]
        by_idx = [t["txid"] for t in txs]
        pool, self.data = {}, {}
        for t in txs:
            pool[t["txid"]] = {
                "fee": t["fee"], "weight": t["weight"], "wtxid": t["hash"],
                "depends": [by_idx[d - 1] for d in t.get("depends", [])]}
            self.data[t["txid"]] = bytes.fromhex(t["data"])
        # reordena TODAS as txs do template (conjunto igual => mesmos sigops/peso)
        self.order, self.fees, self.weight = select_transactions(pool, max_weight=10 ** 12)
        assert len(self.order) == len(pool) and check_order(self.order, pool), \
            "ordenacao invalida"
        txids_le = [bytes.fromhex(t)[::-1] for t in self.order]
        self.wit = witness_commitment(
            [bytes.fromhex(pool[t]["wtxid"])[::-1] for t in self.order])
        self.branch = merkle_branch(txids_le)

    def build(self, extranonce):
        """Retorna (header80 com nonce 0, coinbase serializada com witness)."""
        cb, cb_txid = build_coinbase(self.height, struct.pack("<Q", extranonce),
                                     self.value, self.payout, self.wit)
        root = root_with_coinbase(cb_txid, self.branch)
        hdr = (struct.pack("<i", self.version) + self.prev + root
               + struct.pack("<III", self.time, self.bits, 0))
        return hdr, cb

    def valid_nonce(self, hdr, nonce):
        h = sha256d(hdr[:76] + struct.pack("<I", nonce))
        return int.from_bytes(h, "little") <= self.target, h

    def block_hex(self, hdr, cb, nonce):
        raw = hdr[:76] + struct.pack("<I", nonce)
        raw += varint(1 + len(self.order)) + cb
        raw += b"".join(self.data[t] for t in self.order)
        return raw.hex()


# ------------------------------- vigia do RPC -------------------------------
class Watcher(threading.Thread):
    """Consulta o RPC; sinaliza SOMENTE quando a ponta da cadeia muda, ou seja,
    quando o bloco que estamos minerando ja foi descoberto por outro minerador."""

    def __init__(self, rpc, tip, poll):
        super().__init__(daemon=True)
        self.rpc, self.tip, self.poll = rpc, tip, poll
        self.new_tip = threading.Event()
        self.stale = self.new_tip        # compat: unico motivo de cancelar
        self.stop_evt = threading.Event()

    def run(self):
        while not self.stop_evt.wait(self.poll):
            try:
                tip = self.rpc.call("getbestblockhash")
            except Exception as e:
                print(f"\n[RPC] falha ao consultar ponta: {e} (continuo minerando)")
                continue
            if tip != self.tip:
                self.new_tip.set(); return


def fmt_rate(h):
    for u, d in (("TH/s", 1e12), ("GH/s", 1e9), ("MH/s", 1e6), ("kH/s", 1e3)):
        if h >= d:
            return f"{h / d:.3f} {u}"
    return f"{h:.1f} H/s"



def fmt_hms(sec):
    sec = max(0, int(sec))
    d, r = divmod(sec, 86400); h, r = divmod(r, 3600); m, s = divmod(r, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def fmt_long_time(sec):
    if sec == float("inf"): return "--"
    if sec < 86400 * 2: return fmt_hms(sec)
    days = sec / 86400
    if days < 730: return f"{days:,.1f} dias"
    return f"{days / 365.25:,.3g} anos"


def fmt_hashes(x):
    for u, d in (("E", 1e18), ("P", 1e15), ("T", 1e12), ("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if x >= d: return f"{x / d:.3f} {u}H"
    return f"{x:.0f} H"


def target_info(target):
    """zeros no topo do hash, dificuldade e hashes esperados por bloco."""
    zbits = 256 - target.bit_length()
    diff = (0xFFFF << (8 * (0x1D - 3))) / target
    return zbits, diff, (1 << 256) / (target + 1)


class Progress:
    """Barra de progresso ao vivo (uma linha): passada do nonce, taxa, tempos."""
    def __init__(self, width=30):
        self.width, self.last_len = width, 0

    def draw(self, done_pass, total_pass, hashes, rate, t_job, t_session, expected_s, height):
        frac = min(1.0, done_pass / total_pass) if total_pass else 0
        f = int(self.width * frac)
        bar = "#" * f + "-" * (self.width - f)
        pass_eta = (total_pass - done_pass) / rate if rate > 0 else float("inf")
        line = (f"\r[{bar}] {100 * frac:5.1f}%  h={height}  {fmt_hashes(hashes)}  "
                f"{fmt_rate(rate)}  job {fmt_hms(t_job)}  total {fmt_hms(t_session)}  "
                f"fim da passada {fmt_long_time(pass_eta)}  "
                f"bloco ~{fmt_long_time(expected_s)}")
        pad = max(0, self.last_len - len(line))
        sys.stdout.write(line + " " * pad); sys.stdout.flush()
        self.last_len = len(line)

    def msg(self, text):
        sys.stdout.write("\r" + " " * self.last_len + "\r"); self.last_len = 0
        print(text)


# ------------------------------- loop principal -------------------------------
def mine(rpc, miner, a, stop=lambda: False):
    preflight(rpc, rpc.url)
    info = miner.connect()
    n, clk, cpb = info["cores"], info["clk_mhz"], info["cycles_per_batch"]
    print(f"FPGA: {n} nucleos @ {clk} MHz -> teorico {fmt_rate(n * clk * 1e6 / cpb)}")
    pay = rpc.call("validateaddress", a.address)
    if not pay.get("isvalid"):
        sys.exit(f"endereco invalido: {a.address}")
    payout = bytes.fromhex(pay["scriptPubKey"])
    stats = {"blocks_found": 0, "templates": 0}
    t_session = time.time()

    while not stop():
        try:
            tmpl = rpc.call("getblocktemplate", {"rules": ["segwit"]})
            job = Job(tmpl, payout)
        except Exception as e:
            print(f"[RPC] sem candidato ({e}); tentando de novo em 5 s")
            time.sleep(5); continue
        stats["templates"] += 1
        tip = tmpl["previousblockhash"]
        tz, diff, exp_hashes = target_info(job.target)
        zb = min(a.max_zbits, tz)    # filtro da FPGA (ate 128 bits); software confere o alvo exato
        print(f"\nCandidato #{stats['templates']}: altura {job.height}, "
              f"{len(job.order)} txs, peso {job.weight}, taxas {job.fees} sats, "
              f"recompensa {job.value} sats")
        print(f"  ALVO   {job.target:064x}\n"
              f"  bits={job.bits:#010x}  zeros no topo={tz}  dificuldade={diff:,.0f}  "
              f"filtro FPGA={zb} bits  hashes esperados/bloco={fmt_hashes(exp_hashes)}")

        w = Watcher(rpc, tip, a.poll); w.start()
        nb = max(1, (1 << 32) // n)
        extranonce = random.getrandbits(32)
        hdr, cb = job.build(extranonce)
        miner.start_job(hdr, 0, nb, zb)
        t_job, done_total, last_log, last_req = time.time(), 0, 0.0, 0.0
        t_tmpl = time.time()
        last_cur = 0
        bar = Progress()
        submitted = False
        try:
            while not w.stale.is_set() and not stop() and not submitted:
                if time.time() - last_req > 0.5:
                    miner.request_status(); last_req = time.time()
                try:
                    kind, v = miner.poll(0.15)
                except TimeoutError:
                    kind, v = None, None
                if kind == "found":
                    ok, h = job.valid_nonce(hdr, v)
                    if not ok:        # hit do filtro de 32 bits, mas acima do alvo
                        continue
                    bar.msg(f"*** NONCE VALIDO {v:#010x} hash {h[::-1].hex()}")
                    res = rpc.call("submitblock", job.block_hex(hdr, cb, v))
                    bar.msg(f"submitblock -> {res!r}  (None = aceito)")
                    if res is None:
                        stats["blocks_found"] += 1
                    submitted = True
                elif kind == "status":
                    cur = done_total + v["batches"] * n
                    last_cur = cur
                    now = time.time()
                    if now - last_log > 0.4:
                        last_log = now
                        rate = cur / max(1e-9, now - t_job)
                        expected_s = exp_hashes / rate if rate > 0 else float("inf")
                        bar.draw(v["batches"] * n, nb * n, cur, rate, now - t_job,
                                 now - t_session, expected_s, job.height)
                elif kind == "done":     # passada completa -> novo extranonce
                    done_total += v[0] * n
                    extranonce = (extranonce + 1) & 0xFFFFFFFFFFFFFFFF
                    # Atualiza o template (taxas/txs novas, timestamp) so AQUI, entre
                    # passadas, sem interromper nada. A FPGA nao para por isso.
                    if time.time() - t_tmpl >= a.refresh:
                        try:
                            t2 = rpc.call("getblocktemplate", {"rules": ["segwit"]})
                            if t2["previousblockhash"] != tip:
                                w.new_tip.set()      # bloco atual ja foi descoberto
                                continue
                            job = Job(t2, payout); t_tmpl = time.time()
                            stats["templates"] += 1
                            bar.msg(f"[RPC] template atualizado sem interromper: "
                                    f"{len(job.order)} txs, taxas {job.fees} sats")
                        except Exception as e:
                            bar.msg(f"[RPC] nao atualizei o template ({e}); sigo com o atual")
                    hdr, cb = job.build(extranonce)
                    miner.start_job(hdr, 0, nb, zb)
        finally:
            w.stop_evt.set()
            try:
                miner.stop(3)           # nunca deixa a FPGA minerando um job velho
            except Exception:
                pass
        if w.new_tip.is_set():
            print("\n[RPC] o bloco que voce minerava ja foi descoberto na rede -> "
                  "cancelando e pegando novo candidato")
        elif submitted:
            print("[RPC] bloco enviado -> pegando novo candidato")
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", "-p", required=True)
    ap.add_argument("--baud", "-b", type=int, default=115200)
    ap.add_argument("--address", required=True, help="seu endereco de recompensa")
    ap.add_argument("--rpc-url", default="http://127.0.0.1:8332")
    ap.add_argument("--rpc-user", default=os.environ.get("BTC_RPC_USER"))
    ap.add_argument("--rpc-pass", default=os.environ.get("BTC_RPC_PASS"))
    ap.add_argument("--max-zbits", type=int, default=128,
                    help="max de bits zero que o filtro da FPGA aceita (padrao 128)")
    ap.add_argument("--poll", type=float, default=1.0, help="s entre checagens da ponta")
    ap.add_argument("--refresh", type=float, default=60.0, help="s minimos entre atualizacoes do template; a atualizacao ocorre so ao fim de cada passada de nonces, sem interromper a FPGA")
    a = ap.parse_args()
    if not a.rpc_user or a.rpc_pass is None:
        ap.error("defina BTC_RPC_USER/BTC_RPC_PASS ou --rpc-user/--rpc-pass")
    import serial
    from miner_test import Miner
    miner = Miner(serial.Serial(a.port, a.baud, timeout=0.05))
    rpc = Rpc(a.rpc_url, a.rpc_user, a.rpc_pass)
    try:
        mine(rpc, miner, a)
    except KeyboardInterrupt:
        print("\nparando FPGA...")
        try: miner.stop(3)
        except Exception: pass


if __name__ == "__main__":
    main()
