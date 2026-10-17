# Minerador v3 -- protocolo com preempcao de job (sem travar)

## O que mudou desde o v1
O protocolo antigo (v1,v2) exigia STOP + esperar o engine ficar ocioso + so' entao
enviar um novo JOB. Isso causava a sensacao de "travar" se o host reiniciasse
no meio do caminho, ou simplesmente nao tinha como trocar de job instantaneamente
assim que um bloco fosse encontrado.

O protocolo v3 (rtl/miner_top.v, rtl/miner_engine.v) implementa PREEMPCAO
ATOMICA: um novo JOB pode ser enviado a QUALQUER momento, mesmo com o engine
minerando a todo vapor. O job antigo continua rodando ENQUANTO o frame novo
chega pela UART (zero tempo ocioso); so' quando o frame chega INTEIRO e com
checksum valido e' que o engine e' abortado e o job novo comeca -- a troca em
si leva 2 ciclos de clock. Mensagens de controle (PING/INFO/STATUS/ACK/NAK)
tem prioridade sobre FOUND, entao a FPGA nunca fica muda/sem responder, mesmo
com um job que acha dezenas de hits por segundo.

Resumo do protocolo (ver cabecalho de rtl/miner_top.v para detalhes):
  PC->FPGA  0x01 JOB(id,mid,w,nonce0,lotes,zbits,checksum) | 0x02 INFO |
            0x03 STOP | 0x05 PING+1B | 0x06 STATUS
  FPGA->PC  0x80 FOUND(id,nonce) | 0x81 DONE(id,lotes,ciclos) | 0x82 INFO |
            0x83 STATUS(id,ativo,lotes,ciclos) | 0x85 PING | 0x86 NAK(codigo) |
            0x87 ACK(id)

## Testado
Simulacao (sim/run_top_tests.sh, 8 cenarios, TODOS PASSAM):
  S1: comandos com FPGA ociosa (PING/INFO/STATUS)
  S2: job completo do genesis real (ACK->FOUND->DONE, nonce correto)
  S3: "metralhadora" de FOUNDs + PING no meio -- FPGA nunca fica muda
  S4: PREEMPCAO -- job novo interrompe a mineracao em andamento SEM esperar
      (exatamente o cenario pedido: achar um bloco e trocar de job na hora)
  S5: checksum invalido NAO derruba o job em andamento; STOP sempre responde
      DONE e descarta hits pendentes
  S6: frame incompleto -> timeout -> parser recupera sozinho
  S7: frame incompleto + flood de 0x00 -> resync deterministico
  S8: reinicios rapidos em cascata (job 6->7->8->9) -- so' sobra o ultimo

host/miner_test.py foi reescrito para esse protocolo novo (job_id, checksum,
STATUS). genesis_scan.py e block_scan.py foram re-testados contra ele via
--soft e continuam funcionando sem alteracao (mesma API publica da classe
Miner: info(), start_job(), stop(), collect() etc).

## Bitstream
build/v3_n8_f64.fs -- N=8 nucleos, clock 64 MHz (IDIV=0,FBDIV=0,ODIV=32).
Fmax pos-roteamento (nextpnr): 3.540 MHz -- folga confortavel.
Essa e' a MESMA frequencia que ja validamos como estavel no datapath (v2);
ainda NAO testamos na placa se a logica de CONTROLE nova tambem fecha timing
real a frequencias mais altas (64MHz) -- comece por esta (27MHz seguro)
antes de tentar subir o clock com o protocolo v3.

## Proximo passo
1. Grave build/v3_n8_f64.fs na placa.
2. python host/miner_test.py --port COMx ping
3. python host/miner_test.py --port COMx info     (deve mostrar versao=2)
4. python host/genesis_scan.py --port COMx --nonce0 0x7c2bac00 --range 256
5. Teste o cenario real: inicie um job longo (ex: genesis_scan.py sem --range,
   full 2^32) e, enquanto ele roda, ABRA OUTRO TERMINAL e rode genesis_scan.py
   de novo (ou qualquer start_job) -- o job novo deve assumir na hora, sem
   travar. (Cuidado: dois processos Python acessando a mesma porta serial ao
   mesmo tempo vao brigar pelo acesso ao /dev/ttyUSBx -- para testar
   preempcao de verdade, o ideal e' ter um unico processo/host orquestrando
   troca de jobs, nao dois scripts concorrentes. O teste de simulacao S4 ja
   comprova que o firmware aceita a troca corretamente.)
