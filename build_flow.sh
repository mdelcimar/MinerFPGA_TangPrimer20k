#!/bin/bash
# uso: ./build_flow.sh N [FBDIV] [ODIV] [SEED]   -> sintese + P&R + bitstream (toolchain open-source)
set -e
N=${1:-14}; FB=${2:-3}; OD=${3:-8}; SEED=${4:-1}
cd "$(dirname "$0")"; mkdir -p build
FREQ=$(( 27 * (FB + 1) / (${ID:-0} + 1) ))
T=build/v2_n${N}_f${FREQ}
echo "== N=$N clk=${FREQ}MHz seed=$SEED"
yosys -q -l $T.ys.log -p "read_verilog -sv rtl/sha256_kn.v rtl/sha_core.v rtl/miner_engine.v rtl/uart.v rtl/miner_top.v; \
   chparam -set N $N miner_top; chparam -set IDIV ${ID:-0} miner_top; chparam -set FBDIV $FB miner_top; chparam -set ODIV $OD miner_top; \
   synth_gowin -top miner_top -nowidelut; bufnorm -conn; opt_clean -purge; write_json $T.json"
nextpnr-himbaechel --json $T.json --write $T.pnr.json --device GW2A-LV18PG256C8/I7 \
   --vopt family=GW2A-18C --vopt cst=constraints/tang_primer_20k.cst \
   --freq $FREQ --seed $SEED --timing-allow-fail -l $T.pnr.log > /dev/null 2>&1 || { echo "P&R FALHOU"; tail -5 $T.pnr.log; exit 1; }
gowin_pack -d GW2A-18C -o $T.fs $T.pnr.json
grep -E "Max frequency|LUT4|DFF|ALU|MUX2_LUT" $T.pnr.log | tail -8
ls -la $T.fs
