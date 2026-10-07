#!/usr/bin/env python3
"""Gera vetores de teste (job.hex + expected.txt) para o testbench do engine."""
import hashlib, struct, sys, random

M32 = 0xFFFFFFFF
def primes(n):
    p, x = [], 2
    while len(p) < n:
        if all(x % q for q in p): p.append(x)
        x += 1
    return p
def iroot(n, k):
    lo, hi = 0, 1 << ((n.bit_length() // k) + 2)
    while lo < hi:
        m = (lo + hi + 1) // 2
        if m ** k <= n: lo = m
        else: hi = m - 1
    return lo
P = primes(64)
K  = [iroot(p << 96, 3) & M32 for p in P]
IV = [iroot(p << 64, 2) & M32 for p in P[:8]]
rotr = lambda x, n: ((x >> n) | (x << (32 - n))) & M32
def compress(st, blk):
    w = list(struct.unpack(">16I", blk))
    for t in range(16, 64):
        s0 = rotr(w[t-15],7) ^ rotr(w[t-15],18) ^ (w[t-15] >> 3)
        s1 = rotr(w[t-2],17) ^ rotr(w[t-2],19) ^ (w[t-2] >> 10)
        w.append((w[t-16] + s0 + w[t-7] + s1) & M32)
    a,b,c,d,e,f,g,h = st
    for t in range(64):
        S1 = rotr(e,6) ^ rotr(e,11) ^ rotr(e,25); ch = (e & f) ^ (~e & M32 & g)
        t1 = (h + S1 + ch + K[t] + w[t]) & M32
        S0 = rotr(a,2) ^ rotr(a,13) ^ rotr(a,22); mj = (a & b) ^ (a & c) ^ (b & c)
        h,g,f,e,d,c,b,a = g,f,e,(d+(t1))&M32,c,b,a,(t1+S0+mj)&M32
    return [(x+y) & M32 for x,y in zip(st,[a,b,c,d,e,f,g,h])]
def midstate(hdr64): return compress(IV, hdr64)
def dsha(hdr80): return hashlib.sha256(hashlib.sha256(hdr80).digest()).digest()
def top32(hash_bytes):            # 32 bits mais significativos do hash como numero (bswap(H7))
    return int.from_bytes(hash_bytes[28:32], 'little')

GENESIS = bytes.fromhex(
 "0100000000000000000000000000000000000000000000000000000000000000"
 "000000003ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa"
 "4b1e5e4a29ab5f49ffff001d1dc32b7c")   # ultimo campo nonce = 0x7c2bac1d LE
GENESIS = GENESIS[:76] + struct.pack("<I", 2083236893)

def job_words(hdr80, nonce0, nbatches, zbits):
    mid = midstate(hdr80[:64])
    tail = list(struct.unpack(">3I", hdr80[64:76]))
    return mid + tail + [nonce0, nbatches], zbits

if __name__ == "__main__":
    mode, N = sys.argv[1], int(sys.argv[2])
    if mode == "genesis":
        nonce_true = 2083236893
        nonce0, nb, zb = nonce_true - 5*N - 3, 12, 32
        hdr = GENESIS
    elif mode == "all":   # zbits=0: TODO nonce e' hit (estressa FIFO/backpressure)
        random.seed(99)
        hdr = bytes(random.getrandbits(8) for _ in range(76)) + b"\0\0\0\0"
        nonce0, nb, zb = 1008, 10, 0
    else:   # aleatorio, zbits=6
        random.seed(1234)
        hdr = bytes(random.getrandbits(8) for _ in range(76)) + b"\0\0\0\0"
        nonce0, nb, zb = 0xFFFFFF00, 20, 6           # cruza o overflow 2^32 de proposito
    words, zbits = job_words(hdr, nonce0, nb, zb)
    with open("sim/job.hex", "w") as f:
        for wd in words: f.write(f"{wd:08x}\n")
        f.write(f"{zbits:08x}\n")
    exp = []
    for k in range(nb * N):
        n = (nonce0 + k) & M32
        h = dsha(hdr[:76] + struct.pack("<I", n))
        if top32(h) >> (32 - zbits) == 0 if zbits else True:
            exp.append(n)
    with open("sim/expected.txt", "w") as f:
        for n in exp: f.write(f"{n:08x}\n")
    print(f"[{mode}] N={N} nonce0={nonce0:#x} lotes={nb} zbits={zb} -> esperados: {[hex(x) for x in exp]}")
