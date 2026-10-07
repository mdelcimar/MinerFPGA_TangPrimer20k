#!/bin/bash
# Simula o engine contra hashlib (bloco genesis + varredura aleatoria)
set -e; cd "$(dirname "$0")/.."; mkdir -p build
PASS=1
for mode in genesis random; do for N in ${NLIST:-4 16}; do
  python3 sim/vectors.py $mode $N
  iverilog -g2012 -o build/tb_$N.vvp -P tb_engine.N=$N sim/tb_engine.v rtl/miner_engine.v rtl/sha_core.v rtl/sha256_kn.v
  vvp build/tb_$N.vvp | grep -E "FOUND|DONE|TIMEOUT" | sed 's/FOUND //' > build/got.txt
  grep -E "DONE|TIMEOUT" build/got.txt
  grep -v -E "DONE|TIMEOUT" build/got.txt | sort > build/got_sorted.txt || true
  sort sim/expected.txt > build/exp_sorted.txt
  if diff -q build/got_sorted.txt build/exp_sorted.txt >/dev/null; then
     echo "  ==> $mode N=$N: PASS ($(wc -l < build/exp_sorted.txt) hit(s) identicos ao hashlib)"
  else echo "  ==> $mode N=$N: FAIL"; diff build/got_sorted.txt build/exp_sorted.txt | head; PASS=0; fi
done; done
[ $PASS = 1 ] && echo "TODOS OS TESTES PASSARAM" || { echo "HA FALHAS"; exit 1; }
