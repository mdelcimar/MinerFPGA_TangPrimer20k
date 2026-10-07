#!/usr/bin/env python3
"""Confere build/tb2.log (saida de sim/tb_top2.v) contra o comportamento esperado do protocolo v2."""
import re, sys
TRUE = "7c2bac1d"
ev = []; scen = {}; sent = {}
for l in open("build/tb2.log"):
    p = l.split()
    if p[0] == "SCEN": scen[p[2]] = int(p[1])
    elif p[0] == "SENT": sent[p[2] + (p[3] if len(p) > 3 else "")] = int(p[1])
    elif p[0] == "MSG": ev.append((int(p[1]), p[2], p[3:]))
def span(a, b): return [e for e in ev if scen[a] <= e[0] < scen[b]]
ok_all = True
def chk(cond, msg):
    global ok_all; ok_all &= bool(cond); print(("  [PASS] " if cond else "  [FAIL] ") + msg)
def kinds(evs, k): return [e for e in evs if e[1] == k]

print("S1 comandos com FPGA ociosa")
s = span("S1", "S2")
chk(any(e[1]=="PING" and e[2]==["5a"] for e in s), "PING 5a respondido")
chk(any(e[1]=="INFO" and e[2][0]=="4" and e[2][3]=="2" for e in s), f"INFO (N=4, versao 2): {[e[2] for e in kinds(s,'INFO')]}")
chk(any(e[1]=="STAT" and e[2][1]=="0" for e in s), "STATUS ocioso (ativo=0)")

print("S2 job genesis id=1")
s = span("S2", "S3"); seq = [(e[1], e[2][0]) for e in s]
chk(seq == [("ACK","01"),("FOUND","01"),("DONE","01")], f"sequencia ACK,FOUND,DONE = {seq}")
chk(kinds(s,"FOUND")[0][2][1] == TRUE, f"nonce = {kinds(s,'FOUND')[0][2][1]}")
chk(kinds(s,"DONE")[0][2][1] == "12", f"12 lotes: {kinds(s,'DONE')[0][2]}")

print("S3 metralhadora (id=2) + PING no meio: FPGA NUNCA fica muda")
s = span("S3", "S4"); nf = len(kinds(s,"FOUND"))
chk(nf > 30, f"{nf} FOUND durante a metralhadora")
pr = [e for e in s if e[1]=="PING" and e[2]==["77"]]
chk(pr, "PING 77 RESPONDIDO durante a metralhadora (v1 nunca respondia)")
if pr: chk(pr[0][0] - sent["PING77"] < 1500, f"latencia do PING = {pr[0][0]-sent['PING77']} ciclos (< 1500)")

print("S4 PREEMPCAO: job genesis id=3 interrompe a metralhadora id=2")
s = span("S4", "S5"); ia = [i for i,e in enumerate(s) if e[1]=="ACK" and e[2]==["03"]]
chk(len(ia)==1, "ACK do job 3")
if ia:
    depois = s[ia[0]+1:]
    chk(all(e[2][0]=="03" for e in depois if e[1] in ("FOUND","DONE")), "nenhuma mensagem do job 2 depois do ACK 03")
    chk([e[2][1] for e in kinds(depois,"FOUND")] == [TRUE], f"job 3 achou exatamente {TRUE}: {[e[2][1] for e in kinds(depois,'FOUND')]}")
    chk(len(kinds(depois,"DONE"))==1 and kinds(depois,"DONE")[0][2][0]=="03", "DONE do job 3")
    chk(len(kinds(s[:ia[0]],"FOUND"))>5, "job 2 ainda minerava enquanto o frame novo chegava (sem tempo ocioso)")

print("S5 frame com checksum ruim NAO derruba o job; STOP responde DONE e descarta hits")
s = span("S5", "S6")
chk(any(e[1]=="ACK" and e[2]==["04"] for e in s), "ACK job 4")
chk(any(e[1]=="NAK" and e[2]==["01"] for e in s), "NAK(1) para checksum ruim")
chk(not any(e[1]=="ACK" and e[2]==["05"] for e in s), "job 5 (invalido) NAO foi aceito")
st = kinds(s,"STAT"); chk(st and st[0][2][0]=="04" and st[0][2][1]=="1", f"STATUS: job 4 ainda ativo apos frame ruim: {st[0][2] if st else None}")
dn = kinds(s,"DONE"); chk(len(dn)==1 and dn[0][2][0]=="04", f"DONE(4) apos STOP: {dn[0][2] if dn else None}")
if dn:
    chk(not [e for e in s if e[0] > dn[0][0] and e[1]=="FOUND"], "nenhum FOUND depois do DONE (FIFO descartada, engine parado)")
    chk(dn[0][0] > sent["STOP"], "DONE veio depois do STOP")

print("S6 frame incompleto -> timeout -> parser volta ao IDLE")
s = span("S6", "S7")
chk(any(e[1]=="NAK" and e[2]==["02"] for e in s), "NAK(2) de timeout")
chk(any(e[1]=="PING" and e[2]==["66"] for e in s), "PING 66 respondido depois")

print("S7 frame incompleto + flood de 0x00 (resync deterministico)")
s = span("S7", "S8")
chk(any(e[1]=="PING" and e[2]==["99"] for e in s), "PING 99 respondido apos o flood")
chk(not any(e[1]=="ACK" for e in s), "nenhum job fantasma iniciado")

print("S8 reinicios rapidos em cascata (6->7->8->9)")
s = span("S8", "END"); acks = [e[2][0] for e in kinds(s,"ACK")]
chk(acks == ["06","07","08","09"], f"ACKs = {acks}")
i9 = [i for i,e in enumerate(s) if e[1]=="ACK" and e[2]==["09"]][0]
dep = s[i9+1:]
chk(all(e[2][0]=="09" for e in dep if e[1] in ("FOUND","DONE")), "apos ACK 09 so' existem mensagens do job 9")
chk([e[2][1] for e in kinds(dep,"FOUND")] == [TRUE] and len(kinds(dep,"DONE"))==1, "job 9 = genesis: 1 FOUND correto + 1 DONE")
print("\n" + ("TODOS OS TESTES PASSARAM" if ok_all else "HA FALHAS")); sys.exit(0 if ok_all else 1)
