// =============================================================================
//  miner_top.v -- v3  Minerador SHA-256d para Sipeed Tang Primer 20K (GW2A-18C)
//
//  NOVIDADE v3 (sobre a v2): o teste de dificuldade agora compara o digest SHA-256d
//  COMPLETO de 256 bits (zbits = 0..255), nao so' os 32 bits mais significativos.
//  Custo: 3 ciclos extra por lote (P2 passa de 61 para as 64 rodadas completas) --
//  os outros 4 somadores de 32 bits (H0..H3) saem de graca do mesmo shift-chain
//  a/b/c/d que ja existia (ver sha_core.v). Protocolo incompativel com v2 no campo
//  zbits (so' usava 6 bits antes); por isso o VERSION subiu de 2 para 3.
//
//  NOVIDADES v2 (preempcao de job, sem travar):
//   * PREEMPCAO ATOMICA: um novo JOB pode chegar a QUALQUER momento. O job
//     antigo continua minerando enquanto o frame novo e' recebido (zero tempo
//     ocioso); so' quando o frame chega INTEIRO e com checksum valido o engine
//     e' abortado e o job novo comeca (troca em 2 ciclos de clock).
//   * Frame invalido / incompleto NAO derruba o job em andamento (-> NAK).
//   * ID de job (1 byte) em todas as respostas: o PC descarta resultados velhos.
//   * Respostas de controle (PING/INFO/STATUS/ACK/NAK) tem PRIORIDADE sobre
//     FOUND -> a FPGA nunca fica "muda", mesmo com job de milhares de hits/s.
//   * STOP aborta, descarta hits pendentes e SEMPRE responde DONE.
//   * Timeout entre bytes do frame (2^TMO_BITS clocks) -> parser volta ao IDLE.
//   * Resync garantido: >= 56 bytes 0x00 levam o parser ao IDLE de qualquer estado.
//
//  Protocolo UART (8N1, big-endian)      [VERSAO 2]
//   PC -> FPGA
//     0x01 JOB : id(1) mid(32) w0,w1,w2(12) nonce_ini(4) n_lotes(4) zbits(1) chk(1)
//                zbits = 0..255 bits de topo exigidos (digest COMPLETO de 256 bits,
//                a partir da v3 -- antes (v2) so' os 32 bits mais significativos)
//                chk = 0xA5 XOR (todos os 54 bytes anteriores, de id ate zbits)
//                n_lotes = 0 -> roda ate STOP/novo JOB ; cada lote testa N nonces
//     0x02 INFO   0x03 STOP   0x05 PING+1B   0x06 STATUS   0x00 ignorado
//   FPGA -> PC
//     0x80 FOUND id nonce(4)          0x81 DONE  id lotes(4) ciclos(4)
//     0x82 INFO  N clk_mhz ciclos/lote versao
//     0x83 STATUS id ativo(1) lotes(4) ciclos(4)     (ao vivo, sem parar o job)
//     0x85 PING eco(1)   0x86 NAK codigo(1: 1=checksum 2=timeout)   0x87 ACK id
// =============================================================================
module miner_top #(
    parameter N     = 8,
    parameter IDIV  = 1,          // clk = 27 MHz * (FBDIV+1)/(IDIV+1)
    parameter FBDIV = 4,          // 27*5/2 = 67.5 MHz
    parameter ODIV  = 8,          // VCO = clk*ODIV  (faixa valida ~400..1200 MHz)
    parameter BAUD  = 115200,
    parameter TMO_BITS = 21,      // timeout entre bytes = 2^TMO_BITS clocks (~31 ms a 67 MHz)
    parameter CLKS_OVERRIDE = 0   // so' para simulacao
) (
    input  wire       clk27,
    input  wire       uart_rx,
    output wire       uart_tx,
    output wire [3:0] led_n
);
    localparam CLK_HZ = (27000000 * (FBDIV + 1)) / (IDIV + 1);
    localparam CPB    = (CLKS_OVERRIDE != 0) ? CLKS_OVERRIDE : ((CLK_HZ + BAUD/2) / BAUD);
    localparam [7:0] VERSION = 8'd3;      // v3: zbits honra o byte inteiro (0..255) -> digest de 256 bits, nao so' 32
    localparam [7:0] CLK_MHZ = CLK_HZ / 1000000;
    localparam [7:0] CPB_BATCH = 8'd140;  // ciclos/lote: LOAD(4)+P1(64)+MS(8)+P2(64) -- era 137 (P2 tinha 61)

    // ---------------- clock ----------------
    wire clk, lock;
`ifdef SIM
    assign clk = clk27; assign lock = 1'b1;
`else
    rPLL #(
        .FCLKIN("27"), .DEVICE("GW2A-18C"),
        .IDIV_SEL(IDIV), .FBDIV_SEL(FBDIV), .ODIV_SEL(ODIV),
        .DYN_IDIV_SEL("false"), .DYN_FBDIV_SEL("false"), .DYN_ODIV_SEL("false"),
        .PSDA_SEL("0000"), .DYN_DA_EN("false"), .DUTYDA_SEL("1000"),
        .CLKOUT_FT_DIR(1'b1), .CLKOUTP_FT_DIR(1'b1), .CLKOUT_DLY_STEP(0), .CLKOUTP_DLY_STEP(0),
        .CLKFB_SEL("internal"), .CLKOUT_BYPASS("false"), .CLKOUTP_BYPASS("false"),
        .CLKOUTD_BYPASS("false"), .DYN_SDIV_SEL(2), .CLKOUTD_SRC("CLKOUT"), .CLKOUTD3_SRC("CLKOUT")
    ) u_pll (
        .CLKOUT(clk), .LOCK(lock), .CLKOUTP(), .CLKOUTD(), .CLKOUTD3(),
        .RESET(1'b0), .RESET_P(1'b0), .CLKIN(clk27), .CLKFB(1'b0),
        .FBDSEL(6'b0), .IDSEL(6'b0), .ODSEL(6'b0), .PSDA(4'b0), .DUTYDA(4'b0), .FDLY(4'b0)
    );
`endif
    reg [7:0] rcnt = 8'd0;
    wire rst = ~rcnt[7];
    always @(posedge clk) if (!lock) rcnt <= 8'd0; else if (!rcnt[7]) rcnt <= rcnt + 8'd1;

    // ---------------- UART ----------------
    wire [7:0] rx_data; wire rx_valid;
    uart_rx #(.CLKS_PER_BIT(CPB)) u_rx (.clk(clk), .rx(uart_rx), .data(rx_data), .valid(rx_valid));
    wire tx_ready; wire tx_go; wire [7:0] tx_byte;
    uart_tx #(.CLKS_PER_BIT(CPB)) u_tx (.clk(clk), .data(tx_byte), .valid(tx_go), .ready(tx_ready), .tx(uart_tx));

    // ---------------- job ativo (o que o engine enxerga) e job "sombra" ----------------
    reg [415:0] job   = 416'd0;   reg [7:0] zbits = 8'd32;  reg [7:0] job_id = 8'd0;
    reg [415:0] jobn  = 416'd0;   reg [7:0] zbn   = 8'd0;   reg [7:0] idn    = 8'd0;
    wire [255:0] mid      = job[255:0];
    wire [95:0]  wtail    = job[351:256];
    wire [31:0]  nonce0   = job[383:352];
    wire [31:0]  nbatches = job[415:384];

    // ---------------- engine ----------------
    reg  start = 1'b0, stop = 1'b0;
    wire found_valid; wire [31:0] found_nonce; reg found_pop = 1'b0;
    wire eng_idle; wire [31:0] eng_batches, eng_cycles;
    miner_engine #(.N(N)) u_eng (
        .clk(clk), .rst(rst), .start(start), .stop(stop),
        .mid(mid), .wtail(wtail), .nonce0(nonce0), .nbatches(nbatches), .zbits(zbits),
        .found_valid(found_valid), .found_nonce(found_nonce), .found_pop(found_pop),
        .idle(eng_idle), .batches(eng_batches), .cycles(eng_cycles));

    // ---------------- parser + arbitro de TX ----------------
    localparam [1:0] CS_IDLE = 2'd0, CS_JOB = 2'd1, CS_PING = 2'd2;
    reg [1:0]  cs = CS_IDLE;
    reg [5:0]  cnt = 6'd0;                    // indice do byte dentro do frame (apos o 0x01)
    reg [TMO_BITS-1:0] tmo = 0;
    reg [7:0]  acc = 8'd0;
    reg [1:0]  sw = 2'd0;                     // 1 = STOP emitido, proximo ciclo copia job+START
    reg        job_active = 1'b0, start_d = 1'b0;
    reg        info_req = 1'b0, ping_req = 1'b0, stat_req = 1'b0, ack_req = 1'b0, nak_req = 1'b0;
    reg [7:0]  ping_data = 8'd0, nak_code = 8'd0;
    reg [87:0] msg  = 88'd0;
    reg [3:0]  mlen = 4'd0;
    wire [5:0] jidx = cnt - 6'd1;             // 0..51 quando cnt = 1..52
    assign tx_byte = msg[87:80];
    assign tx_go   = (mlen != 4'd0) && tx_ready;

    always @(posedge clk) begin
        start <= 1'b0; stop <= 1'b0; start_d <= start; found_pop <= 1'b0;

        // ---------- arbitro de transmissao: controle > FOUND > DONE ----------
        if (mlen == 4'd0) begin
            if (ping_req) begin
                msg <= {8'h85, ping_data, 72'd0}; mlen <= 4'd2; ping_req <= 1'b0;
            end else if (nak_req) begin
                msg <= {8'h86, nak_code, 72'd0};  mlen <= 4'd2; nak_req <= 1'b0;
            end else if (ack_req) begin
                msg <= {8'h87, job_id, 72'd0};    mlen <= 4'd2; ack_req <= 1'b0;
            end else if (info_req) begin
                msg <= {8'h82, N[7:0], CLK_MHZ, CPB_BATCH, VERSION, 48'd0}; mlen <= 4'd5; info_req <= 1'b0;
            end else if (stat_req) begin
                msg <= {8'h83, job_id, 7'd0, job_active, eng_batches, eng_cycles}; mlen <= 4'd11; stat_req <= 1'b0;
            end else if (found_valid && !found_pop) begin
                msg <= {8'h80, job_id, found_nonce, 40'd0}; mlen <= 4'd6; found_pop <= 1'b1;
            end else if (job_active && eng_idle && !found_valid && !start && !start_d && sw == 2'd0) begin
                msg <= {8'h81, job_id, eng_batches, eng_cycles, 8'd0}; mlen <= 4'd10; job_active <= 1'b0;
            end
        end else if (tx_go) begin
            msg <= {msg[79:0], 8'd0}; mlen <= mlen - 4'd1;
        end

        // ---------- parser de comandos ----------
        if (rx_valid) begin
            tmo <= 0;
            case (cs)
                CS_IDLE: case (rx_data)
                    8'h01: begin cs <= CS_JOB; cnt <= 6'd0; acc <= 8'hA5; end
                    8'h02: info_req <= 1'b1;
                    8'h03: begin stop <= 1'b1; job_active <= 1'b1; end    // STOP sempre responde DONE
                    8'h05: cs <= CS_PING;
                    8'h06: stat_req <= 1'b1;
                    default: ;
                endcase
                CS_JOB: begin
                    cnt <= cnt + 6'd1;
                    if (cnt == 6'd0)        idn <= rx_data;
                    else if (cnt <= 6'd52)  jobn[32*jidx[5:2] + 8*(3 - jidx[1:0]) +: 8] <= rx_data;
                    else if (cnt == 6'd53)  zbn <= rx_data;        // byte inteiro: 0..255 bits zero (digest de 256 bits)
                    if (cnt < 6'd54) acc <= acc ^ rx_data;
                    else begin                                            // cnt == 54 : checksum
                        cs <= CS_IDLE;
                        if (acc == rx_data) begin sw <= 2'd1; stop <= 1'b1; end
                        else begin nak_req <= 1'b1; nak_code <= 8'd1; end
                    end
                end
                CS_PING: begin ping_data <= rx_data; ping_req <= 1'b1; cs <= CS_IDLE; end
                default: cs <= CS_IDLE;
            endcase
        end else if (cs != CS_IDLE) begin
            tmo <= tmo + 1'b1;
            if (&tmo) begin cs <= CS_IDLE; nak_req <= 1'b1; nak_code <= 8'd2; end
        end

        // ---------- troca atomica: ciclo 0 = STOP no engine, ciclo 1 = copia job + START ----------
        if (sw == 2'd1) begin
            job <= jobn; zbits <= zbn; job_id <= idn;
            start <= 1'b1; job_active <= 1'b1; ack_req <= 1'b1; sw <= 2'd0;
        end
    end

    // ---------------- LEDs (ativos em nivel baixo) ----------------
    reg [25:0] hb = 26'd0;  reg [22:0] hs = 23'd0;
    always @(posedge clk) begin
        hb <= hb + 26'd1;
        if (found_pop) hs <= {23{1'b1}}; else if (hs != 0) hs <= hs - 23'd1;
    end
    assign led_n = ~{lock, hb[25], (hs != 0), job_active};
endmodule
