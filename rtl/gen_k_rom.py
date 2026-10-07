#!/usr/bin/env python3
"""Gera rtl/sha256_kn.v (K[t+1]) a partir da definicao matematica do SHA-256
(raiz cubica dos 64 primeiros primos) e valida contra hashlib."""
import hashlib

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
K  = [iroot(p << 96, 3) & 0xFFFFFFFF for p in P]            # frac(cbrt(p)) * 2^32
IV = [iroot(p << 64, 2) & 0xFFFFFFFF for p in P[:8]]         # frac(sqrt(p)) * 2^32
assert K[0] == 0x428a2f98 and K[63] == 0xc67178f2 and IV[0] == 0x6a09e667 and IV[7] == 0x5be0cd19

M32 = 0xFFFFFFFF
rotr = lambda x, n: ((x >> n) | (x << (32 - n))) & M32
def compress(st, blk):
    w = [int.from_bytes(blk[4*i:4*i+4], 'big') for i in range(16)]
    for t in range(16, 64):
        s0 = rotr(w[t-15],7) ^ rotr(w[t-15],18) ^ (w[t-15] >> 3)
        s1 = rotr(w[t-2],17) ^ rotr(w[t-2],19) ^ (w[t-2] >> 10)
        w.append((w[t-16] + s0 + w[t-7] + s1) & M32)
    a,b,c,d,e,f,g,h = st
    for t in range(64):
        S1 = rotr(e,6) ^ rotr(e,11) ^ rotr(e,25)
        ch = (e & f) ^ (~e & M32 & g)
        t1 = (h + S1 + ch + K[t] + w[t]) & M32
        S0 = rotr(a,2) ^ rotr(a,13) ^ rotr(a,22)
        mj = (a & b) ^ (a & c) ^ (b & c)
        t2 = (S0 + mj) & M32
        h,g,f,e,d,c,b,a = g,f,e,(d+t1)&M32,c,b,a,(t1+t2)&M32
    return [(x+y) & M32 for x,y in zip(st,[a,b,c,d,e,f,g,h])]
blk = b"abc" + b"\x80" + b"\0"*52 + (24).to_bytes(8,'big')
assert b"".join(x.to_bytes(4,'big') for x in compress(IV, blk)) == hashlib.sha256(b"abc").digest()
print("K/IV OK (validado contra hashlib)")

lines = ["// AUTO-GERADO por sim/gen_k_rom.py -- nao edite.",
         "// sha256_kn: k = K[t+1] (constante da PROXIMA rodada); t=63 -> 0",
         "module sha256_kn (input wire [5:0] t, output wire [31:0] k);",
         "  function [31:0] rom;",
         "    input [5:0] i;",
         "    begin",
         "      case (i)"]
for i in range(64):
    v = K[i+1] if i < 63 else 0
    lines.append(f"        6'd{i}: rom = 32'h{v:08x};")
lines += ["        default: rom = 32'h0;", "      endcase", "    end", "  endfunction",
          "  assign k = rom(t);", "endmodule", ""]
open("rtl/sha256_kn.v", "w").write("\n".join(lines))
print("rtl/sha256_kn.v gerado")
