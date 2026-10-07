#!/usr/bin/env python3
"""Reproduz exatamente o job do teste 'verify' do usuario (seed=1234, N=8, zbits=8,
range=262144) e gera job.hex/expected.txt para rodar em simulacao RTL."""
import random, struct, sys
sys.path.insert(0, "host")
from miner_test import midstate, dsha256, hash_top_zero, M32

N = 8
rng = random.Random(1234)
hdr = bytes(rng.getrandbits(8) for _ in range(76)) + b"\0\0\0\0"
nonce0 = rng.getrandbits(32)
eff = nonce0 & ~(N - 1) & M32           # alinhamento POW2 igual ao Miner.aligned_start
nb = 262144 // N
zb = 8

print(f"hdr={hdr.hex()}")
print(f"nonce0={nonce0:#010x}  eff(alinhado)={eff:#010x}  nb={nb}  zbits={zb}")

mid = midstate(hdr)
w = struct.unpack(">3I", hdr[64:76])
with open("sim/job.hex", "w") as f:
    for v in mid: f.write(f"{v:08x}\n")
    for v in w:   f.write(f"{v:08x}\n")
    f.write(f"{eff:08x}\n")
    f.write(f"{nb:08x}\n")
    f.write(f"{zb:08x}\n")

exp = [(eff + k) & M32 for k in range(nb * N)
       if hash_top_zero(dsha256(hdr[:76] + struct.pack("<I", (eff + k) & M32)), zb)]
with open("sim/expected.txt", "w") as f:
    for n in exp: f.write(f"{n:08x}\n")
print(f"esperado: {len(exp)} hits")
