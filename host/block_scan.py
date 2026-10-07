#!/usr/bin/env python3
"""
block_scan.py -- puxa o cabecalho de um bloco REAL do Bitcoin (por altura, 0 =
genesis ate' a altura atual da chain) da API publica do mempool.space, e varre
o espaco de nonce (0x00000000..0xFFFFFFFF, ou uma faixa escolhida) usando o
minerador na Tang Primer 20K, com barra de progresso ao vivo.

AVISO sobre dificuldade (a partir do firmware v3):
    O nucleo desta FPGA agora compara o digest SHA-256d COMPLETO (256 bits --
    registro 'zbits', 0..255; veja rtl/sha_core.v). A dificuldade REAL do
    Bitcoin hoje exige da ordem de 70-95 bits zero -- bem dentro dos 255
    suportados. Ou seja, para QUALQUER bloco (antigo ou recente), este script
    agora consegue testar o alvo de dificuldade REAL do bloco, sem relaxar
    nada. Achar um hit com zbits = dificuldade real = reproduzir de fato o
    proof-of-work do bloco (nao e' mais so' uma demonstracao).
    (Com firmware v2 antigo, o nucleo so' comparava os 32 bits mais altos --
    use --zbits para forcar um valor menor se quiser, por exemplo, comparar
    com o comportamento antigo ou acelerar um teste de pipeline.)

Uso:
    pip install pyserial
    python block_scan.py --height 0        --port COM5   # genesis (via rede)
    python block_scan.py --height 200000    --port COM5   # bloco real qualquer
    python block_scan.py --height 800000 --soft            # teste sem placa
    python block_scan.py --height 0 --header-hex <160 hex> # sem internet (offline)

    Ctrl+C a qualquer momento manda STOP e mostra o resumo parcial.

Requer host/miner_test.py na mesma pasta (reaproveita midstate(), dsha256() etc).
"""
import argparse, json, os, struct, sys, time, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from miner_test import Miner, SoftFPGA, dsha256, hash_top_zero, midstate, M32

UA = {"User-Agent": "tang-primer-20k-miner/1.0 (+https://github.com/)"}
NETWORKS = {
    "mainnet": "https://mempool.space/api",
    "testnet": "https://mempool.space/testnet/api",
    "testnet4": "https://mempool.space/testnet4/api",
    "signet": "https://mempool.space/signet/api",
}


# ----------------------------------------------------------------------------
#  API do mempool.space
# ----------------------------------------------------------------------------
def api_get(base, path, timeout=10):
    req = urllib.request.Request(f"{base}/{path}", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode().strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} em {path}: {e.read().decode(errors='replace')[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"falha de rede acessando mempool.space ({e.reason}). "
                            f"Sem internet? Use --header-hex para rodar offline.")


def fetch_block_header(base, height):
    tip = int(api_get(base, "blocks/tip/height"))
    if height < 0 or height > tip:
        raise ValueError(f"altura {height} invalida (chain vai de 0 a {tip})")
    block_hash = api_get(base, f"block-height/{height}")
    header_hex = api_get(base, f"block/{block_hash}/header")
    return block_hash, header_hex, tip


# ----------------------------------------------------------------------------
#  Dificuldade (formato "bits" compacto do Bitcoin)
# ----------------------------------------------------------------------------
def bits_to_target(bits):
    exponent = bits >> 24
    mantissa = bits & 0xFFFFFF
    return mantissa >> (8 * (3 - exponent)) if exponent <= 3 else mantissa << (8 * (exponent - 3))


def leading_zero_bits(target, total_bits=256):
    return total_bits if target == 0 else total_bits - target.bit_length()


# ----------------------------------------------------------------------------
#  Utilitarios de exibicao (iguais ao genesis_scan.py)
# ----------------------------------------------------------------------------
def fmt_hms(seconds):
    seconds = max(0, int(seconds))
    h, r = divmod(seconds, 3600); m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_rate(hps):
    for unit, div in (("TH/s", 1e12), ("GH/s", 1e9), ("MH/s", 1e6), ("kH/s", 1e3)):
        if hps >= div: return f"{hps/div:7.3f} {unit}"
    return f"{hps:7.3f} H/s"


class ProgressBar:
    def __init__(self, total, width=40):
        self.total = total; self.width = width; self.t0 = time.time(); self.last_len = 0

    def draw(self, done, extra=""):
        done = min(done, self.total)
        frac = (done / self.total) if self.total else 1.0
        filled = int(self.width * frac)
        bar = "#" * filled + "-" * (self.width - filled)
        elapsed = time.time() - self.t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (self.total - done) / rate if rate > 0 else float("inf")
        eta_s = fmt_hms(eta) if eta != float("inf") else "--:--:--"
        line = (f"\r[{bar}] {100*frac:5.1f}%  {done:>10,}/{self.total:,} nonces  "
                f"{fmt_rate(rate)}  decorrido {fmt_hms(elapsed)}  ETA {eta_s}{extra}")
        pad = max(0, self.last_len - len(line))
        sys.stdout.write(line + " " * pad); sys.stdout.flush(); self.last_len = len(line)

    def newline_msg(self, msg):
        sys.stdout.write("\r" + " " * self.last_len + "\r"); print(msg)


# ----------------------------------------------------------------------------
#  Nucleo
# ----------------------------------------------------------------------------
def save_found_nonce(outfile, height, block_hash, nonce, h_le, is_real, zbits):
    """Grava uma linha no .txt de achados assim que um nonce bate -- flush imediato,
    entao nada se perde mesmo se o script for interrompido (Ctrl+C) ou cair."""
    if not outfile:
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    linha = (f"{ts}  altura={height}  bloco_hash={block_hash}  "
             f"nonce=0x{nonce:08x}  hash_sha256d={h_le[::-1].hex()}  "
             f"zbits_testado={zbits}  historico_real={is_real}\n")
    with open(outfile, "a", encoding="utf-8") as f:
        f.write(linha)
        f.flush()


def run(m: Miner, a, header80, real_nonce, target_bits_zero, block_hash, height):
    info = m.connect()          # resync garantido (job antigo / frame parcial / FOUND em enxurrada)
    n, clk_mhz, cpb = info["cores"], info["clk_mhz"], info["cycles_per_batch"]
    theo_hashrate = n * clk_mhz * 1e6 / cpb
    print(f"FPGA: {n} nucleos @ {clk_mhz} MHz, {cpb} ciclos/lote "
          f"-> taxa teorica = {fmt_rate(theo_hashrate)}")
    if a.outfile:
        print(f"Nonces achados serao gravados em tempo real em: {os.path.abspath(a.outfile)} (append)")

    total = a.range
    nb = max(1, total // n); total = nb * n
    zb = a.zbits
    print(f"Varrendo {total:,} nonces (0x{a.nonce0:08x} .. "
          f"0x{(a.nonce0 + total - 1) & M32:08x}), zbits={zb} "
          f"(estimativa: {fmt_hms(total/theo_hashrate) if theo_hashrate else '?'})\n")

    header76 = header80[:76]
    SoftFPGA.header_hook = header76 + b"\0\0\0\0"
    eff = m.start_job(header76 + b"\0\0\0\0", a.nonce0, nb, zb)

    bar = ProgressBar(total)
    found = []
    t0 = time.time()
    stopped_early = False
    finished = False
    real_done = 0; last_req = 0.0
    try:
        while True:
            if time.time() - last_req > 0.4:          # progresso REAL: pergunta o STATUS a FPGA
                m.request_status(); last_req = time.time()
            try:
                kind, v = m.poll(0.15)
            except TimeoutError:
                kind, v = None, None
            if kind == "found":
                found.append(v)
                is_true = (v == real_nonce)
                tag = "  <-- BATE COM O NONCE HISTORICO REAL!" if is_true else ""
                bar.newline_msg(f"  [FOUND] nonce {v:#010x}{tag}")
                h = dsha256(header76 + struct.pack("<I", v))
                save_found_nonce(a.outfile, height, block_hash, v, h, is_true, zb)
            elif kind == "status":
                real_done = v["batches"] * n
            elif kind == "done":
                batches, cycles = v
                finished = True
                bar.draw(total)
                print()
                real_secs = cycles / (clk_mhz * 1e6) if clk_mhz else (time.time() - t0)
                real_hashes = batches * n
                real_rate = real_hashes / real_secs if real_secs > 0 else 0
                print(f"\nDONE: {batches:,} lotes completos ({real_hashes:,} nonces), "
                      f"{cycles:,} ciclos de FPGA ({real_secs:.2f}s a {clk_mhz} MHz)")
                print(f"Taxa real (medida pelo relogio da FPGA): {fmt_rate(real_rate)}")
                break
            bar.draw(min(total, real_done), extra=f"  hits={len(found)}")
    except KeyboardInterrupt:
        bar.newline_msg("\n[Ctrl+C] parando a FPGA...")
        stopped_early = True
        try:
            found2, (batches, cycles) = m.stop(3)
            finished = True
            novos = [x for x in found2 if x not in found]
            for v in novos:
                h = dsha256(header76 + struct.pack("<I", v))
                save_found_nonce(a.outfile, height, block_hash, v, h, v == real_nonce, zb)
            found += novos
            print(f"Parcial: {batches:,} lotes completos antes de parar (FPGA parada e pronta p/ novo job).")
        except TimeoutError:
            print("(nao recebeu confirmacao de DONE a tempo; na proxima execucao o connect() faz o resync)")
    finally:
        if not finished:
            try: m.stop(2)          # nunca deixa a FPGA minerando ao sair
            except Exception: pass

    print("\n" + "=" * 64)
    print(f"Bloco altura {height}  hash={block_hash}")
    print(f"Alvo real exige {target_bits_zero} bits zero no topo "
          f"(testado aqui: {zb} bits -- {'IGUAL ao real' if zb >= target_bits_zero else 'ALVO RELAXADO, demo'})")
    if not found:
        print("Nenhum hit encontrado" + (" (varredura parcial)." if stopped_early else "."))
    else:
        print(f"{len(found)} hit(s): {[hex(x) for x in found]}")
        for nonce in found:
            h = dsha256(header76 + struct.pack("<I", nonce))
            good = hash_top_zero(h, zb)
            is_real = (nonce == real_nonce)
            print(f"  nonce {nonce:#010x}  hash={h[::-1].hex()}  bate(hashlib)={good}  "
                  f"e_o_historico_real={is_real}")
        if real_nonce in found:
            print(f"\n>>> Nonce historico real ({real_nonce:#010x}) reproduzido com sucesso! <<<")
        elif zb < target_bits_zero:
            print(f"\n(esperado: o alvo real precisa de {target_bits_zero} bits zero, "
                  f"muito mais que os {zb} testados -- os hits acima sao de um alvo "
                  f"relaxado de demonstracao, nao o proof-of-work real do bloco.)")
    print("=" * 64)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--height", type=int, default=0,
                     help="altura do bloco (0 = genesis; padrao: 0)")
    ap.add_argument("--network", choices=list(NETWORKS), default="mainnet")
    ap.add_argument("--header-hex", help="cabecalho de 160 hex chars (80 bytes) "
                                          "fornecido manualmente -- pula a chamada de rede")
    ap.add_argument("--port", "-p", help="porta serial (COM5, /dev/ttyUSB1, ...)")
    ap.add_argument("--baud", "-b", type=int, default=115200)
    ap.add_argument("--soft", action="store_true", help="usa modelo em software (sem placa)")
    ap.add_argument("--nonce0", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--range", type=lambda x: int(x, 0), default=None,
                     help="qtd de nonces a varrer (padrao: espaco todo, 0x100000000; "
                          "em --soft o padrao e' bem menor)")
    ap.add_argument("--zbits", "-z", type=int, default=None,
                     help="bits zero exigidos, 0..255 (padrao: a dificuldade real do bloco, ate' 255)")
    ap.add_argument("--outfile", "-o", default="found_nonces.txt",
                     help="arquivo .txt onde cada nonce achado e' gravado em tempo real "
                          "(uma linha por hit, modo append -- nunca sobrescreve execucoes "
                          "anteriores). Use --outfile '' para desativar. Padrao: found_nonces.txt")
    a = ap.parse_args()
    if a.outfile == "":
        a.outfile = None

    # -------- obter o cabecalho --------
    if a.header_hex:
        header_hex, block_hash, height = a.header_hex.strip(), "(fornecido manualmente)", a.height
        print(f"[usando --header-hex fornecido, sem consultar a rede]")
    else:
        base = NETWORKS[a.network]
        print(f"Consultando {base} ...")
        block_hash, header_hex, tip = fetch_block_header(base, a.height)
        height = a.height
        print(f"Bloco {height:,} / {tip:,}  hash={block_hash}")

    header80 = bytes.fromhex(header_hex)
    if len(header80) != 80:
        raise SystemExit(f"cabecalho com tamanho errado: {len(header80)} bytes (esperado 80)")
    version, = struct.unpack_from("<I", header80, 0)
    time_, bits, real_nonce = struct.unpack_from("<III", header80, 68)
    target = bits_to_target(bits)
    tzeros = leading_zero_bits(target)
    print(f"version={version:#x}  time={time_} ({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(time_))})"
          f"  bits={bits:#010x}  nonce_historico={real_nonce:#010x} ({real_nonce})")
    print(f"Dificuldade real do bloco exige {tzeros} bits zero no topo do hash.")
    if tzeros > 255:
        print(f"AVISO: essa FPGA so' testa ate' 255 bits zero -- nao vai conseguir reproduzir")
        print(f"       o nonce historico real deste bloco (so' um teste com alvo relaxado).")

    if a.zbits is None:
        a.zbits = min(255, tzeros)       # com firmware v3 isso cobre qualquer dificuldade real do Bitcoin

    # -------- conexao com o minerador --------
    if a.soft:
        if a.range is None:
            a.range = 1 << 16
            print(f"[modo --soft: varredura reduzida para {a.range:,} nonces -- use --range para mudar]")
        ser = SoftFPGA()
    else:
        if not a.port: ap.error("--port e' obrigatorio (ou use --soft)")
        if a.range is None: a.range = 1 << 32
        import serial
        ser = serial.Serial(a.port, a.baud, timeout=0.05)

    m = Miner(ser)
    try:
        found = run(m, a, header80, real_nonce, tzeros, block_hash, height)
    except Exception as e:
        print(f"\n[ERRO] {type(e).__name__}: {e}"); sys.exit(1)
    sys.exit(0 if found or a.range < (1 << 32) else 1)


if __name__ == "__main__":
    main()
