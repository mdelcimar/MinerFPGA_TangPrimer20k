#!/bin/bash
# Testes do firmware v2 (protocolo, preempcao, recuperacao). Requer iverilog no PATH.
set -e; cd "$(dirname "$0")/.."; mkdir -p build
python3 sim/vectors.py genesis 4 > /dev/null                # job.hex = genesis (N=4)
[ -f sim/job_fire.hex ] || { echo "falta sim/job_fire.hex"; exit 1; }
iverilog -g2012 -DSIM -o build/tb2.vvp sim/tb_top2.v rtl/*.v
vvp build/tb2.vvp | grep -E "^(MSG|SCEN|SENT)" > build/tb2.log
python3 sim/check_tb2.py
