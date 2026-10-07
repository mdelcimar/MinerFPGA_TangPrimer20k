// =============================================================================
//  miner_engine.v  --  N cores SHA-256d em lockstep + controlador compartilhado
//
//  Lote = N nonces consecutivos, 140 ciclos:  LOAD(4)+P1(64)+MS(8)+P2(64)
//  (P2 roda as 64 rodadas completas -> digest de 256 bits inteiro, nao so' 32)
//  (+16 ciclos de PRIME uma unica vez, no inicio do job)
//  Vazao = N / 137 hashes por ciclo.
//
//  POW2 (N potencia de 2): nonce_inicial e' arredondado p/ baixo a multiplo de N.
//  Resultados vao p/ uma FIFO; se encher, o engine espera (backpressure).
// =============================================================================
module miner_engine #(
    parameter N = 16
) (
    input  wire         clk,
    input  wire         rst,
    input  wire         start,
    input  wire         stop,
    input  wire [255:0] mid,
    input  wire [95:0]  wtail,          // {w2,w1,w0}
    input  wire [31:0]  nonce0,
    input  wire [31:0]  nbatches,       // 0 = infinito
    input  wire [7:0]   zbits,          // 0..255 bits de topo que devem ser zero (digest completo, 256 bits)
    output wire         found_valid,
    output wire [31:0]  found_nonce,
    input  wire         found_pop,
    output wire         idle,
    output reg  [31:0]  batches = 32'd0,
    output reg  [31:0]  cycles  = 32'd0
);
    localparam POW2 = ((N & (N-1)) == 0) ? 1 : 0;
    localparam [2:0] ST_IDLE = 3'd0, ST_PRIME = 3'd1, ST_LOAD = 3'd2, ST_LD = 3'd6,
                     ST_P1 = 3'd3, ST_MS = 3'd4, ST_P2 = 3'd5;
    localparam [31:0] K0  = 32'h428a2f98;
    localparam [31:0] IV7 = 32'h5be0cd19;

    // ---- constantes derivadas do job ----
    reg [31:0]  hkw0 = 32'd0, c2 = 32'd0;
    reg [255:0] mask = 256'd0;
    always @(posedge clk) begin
        hkw0 <= mid[255:224] + K0 + wtail[31:0];
        c2   <= IV7 + K0 + mid[31:0];
        // zbits e' 8 bits (0..255), sempre < 256 -> nao precisa de clamp de saturacao
        mask <= ~({256{1'b1}} >> zbits);
    end

    // ---- FSM ----
    reg [2:0]  st = ST_IDLE;
    reg [5:0]  t  = 6'd0;
    reg        c_ld = 0, c_chk = 0, c_st = 0, c_wsh = 0, c_feed = 0, c_fid = 0, c_ms = 0, c_md = 0;
    reg [2:0]  msel = 3'd0;
    reg [31:0] feed_r = 32'd0, k_r = 32'd0, bus_a = 32'd0, bus_e = 32'd0, bus_m = 32'd0;
    reg        run = 0, inf = 0, chk_pend = 0, cap = 0, chk_r = 0;
    reg [31:0] nbase = 32'd0, fbase = 32'd0, hit_base = 32'd0, remain = 32'd0;
    reg [N-1:0] pend = {N{1'b0}};
    wire [N-1:0] hit_c;

    wire [31:0] kn;
    sha256_kn u_k (.t(t), .k(kn));

    wire go_next = run && (inf || (remain != 32'd0));
    wire stall   = (pend != {N{1'b0}}) || cap;

    // palavra de feed k (0..15) do proximo lote
    wire [31:0] nb_bs = POW2 ? {nbase[7:0], nbase[15:8], nbase[23:16], nbase[31:24]} : 32'd0;
    function [31:0] fword;
        input [3:0]  k;
        input [95:0] wt;
        input [31:0] nbs;
        case (k)
            4'd0:  fword = wt[31:0];
            4'd1:  fword = wt[63:32];
            4'd2:  fword = wt[95:64];
            4'd3:  fword = nbs;
            4'd4:  fword = 32'h80000000;
            4'd15: fword = 32'h00000280;
            default: fword = 32'd0;
        endcase
    endfunction
    wire [5:0] ft = t - 6'd48;                       // indice de feed na fase 2 (P2 agora tem 64 ciclos)

    // ---- FIFO de hits ----
    reg [31:0] mem [0:15];
    reg [4:0]  wp = 5'd0, rp = 5'd0;
    wire       fifo_full  = ((wp - rp) == 5'd16);
    wire       fifo_empty = (wp == rp);
    assign found_valid = ~fifo_empty;
    assign found_nonce = mem[rp[3:0]];

    localparam IW = (N > 1) ? $clog2(N) : 1;
    function [IW-1:0] lowest;
        input [N-1:0] p;
        integer j;
        begin
            lowest = {IW{1'b0}};
            for (j = N-1; j >= 0; j = j - 1) if (p[j]) lowest = j[IW-1:0];
        end
    endfunction
    wire [IW-1:0] idx = lowest(pend);
    wire push = (pend != {N{1'b0}}) && !fifo_full;

    assign idle = (st == ST_IDLE) && !chk_r && !cap && (pend == {N{1'b0}});

    always @(posedge clk) if (push) mem[wp[3:0]] <= hit_base + idx;

    always @(posedge clk) begin
        // defaults: controles valem por 1 ciclo
        c_ld <= 0; c_chk <= 0; c_st <= 0; c_wsh <= 0; c_feed <= 0; c_fid <= 0; c_ms <= 0; c_md <= 0;
        chk_r <= 1'b0;
        cap   <= chk_r;
        k_r   <= kn;

        if (cap)       pend <= hit_c;
        else if (push) pend <= pend & ~({{(N-1){1'b0}}, 1'b1} << idx);
        if (found_pop && !fifo_empty) rp <= rp + 5'd1;
        if (push)                     wp <= wp + 5'd1;
        if (st != ST_IDLE) cycles <= cycles + 32'd1;

        if (rst || stop) begin
            st <= ST_IDLE; run <= 1'b0; chk_pend <= 1'b0; chk_r <= 1'b0; cap <= 1'b0;
            pend <= {N{1'b0}};
            c_ld <= 0; c_chk <= 0; c_st <= 0; c_wsh <= 0; c_feed <= 0; c_fid <= 0; c_ms <= 0; c_md <= 0;
            wp <= 5'd0; rp <= 5'd0;                 // STOP/RST descartam hits pendentes
        end else if (start) begin
            st <= ST_PRIME; t <= 6'd0; run <= 1'b1; chk_pend <= 1'b0; chk_r <= 1'b0; cap <= 1'b0;
            pend <= {N{1'b0}};
            nbase <= POW2 ? (nonce0 & ~(N - 1)) : nonce0;
            remain <= nbatches; inf <= (nbatches == 32'd0);
            batches <= 32'd0; cycles <= 32'd0;
            wp <= 5'd0; rp <= 5'd0;
        end else begin
            case (st)
                ST_IDLE: ;
                ST_PRIME: begin                       // enche a janela com o 1o lote (16 ciclos)
                    c_wsh <= 1; c_feed <= 1; c_fid <= (t[3:0] == 4'd3);
                    feed_r <= fword(t[3:0], wtail, nb_bs);
                    t <= t + 6'd1;
                    if (t == 6'd15) begin st <= ST_LOAD; chk_pend <= 1'b0; end
                end
                ST_LOAD: begin
                    if (!stall) begin
                        chk_r <= chk_pend;
                        c_chk <= chk_pend;                 // compara E61+IV7 do lote anterior
                        if (chk_pend) begin hit_base <= fbase; batches <= batches + 32'd1; end
                        if (go_next) begin
                            c_ld <= 1;                     // j=0 : mid[3] -> a , mid[7] -> e
                            bus_a <= mid[127:96]; bus_e <= mid[255:224];
                            fbase <= nbase; nbase <= nbase + N;
                            if (!inf) remain <= remain - 32'd1;
                            chk_pend <= 1'b0;
                            t <= 6'd1; st <= ST_LD;
                        end else begin
                            chk_pend <= 1'b0; run <= 1'b0; st <= ST_IDLE;
                        end
                    end
                end
                ST_LD: begin                               // j = t = 1..3
                    c_ld <= 1;
                    case (t[1:0])
                        2'd1: begin bus_a <= mid[95:64];  bus_e <= mid[223:192]; end
                        2'd2: begin bus_a <= mid[63:32];  bus_e <= mid[191:160]; end
                        default: begin bus_a <= mid[31:0]; bus_e <= mid[159:128]; end
                    endcase
                    t <= t + 6'd1;
                    if (t == 6'd3) begin t <= 6'd0; st <= ST_P1; end
                end
                ST_P1: begin
                    c_st <= 1; c_wsh <= 1; t <= t + 6'd1;
                    if (t == 6'd63) begin st <= ST_MS; t <= 6'd0; end
                end
                ST_MS: begin                               // m = t = 0..7
                    c_ms <= 1; msel <= t[2:0]; bus_m <= mid[32*t[2:0] +: 32];
                    t <= t + 6'd1;
                    if (t == 6'd7) begin c_md <= 1; t <= 6'd0; st <= ST_P2; end
                end
                ST_P2: begin                           // 64 rodadas completas -> digest de 256 bits
                    c_st <= 1; c_wsh <= 1; t <= t + 6'd1;
                    if (t >= 6'd48) begin                 // feed do proximo lote (ultimas 16 rodadas)
                        c_feed <= 1; c_fid <= (ft[3:0] == 4'd3);
                        feed_r <= fword(ft[3:0], wtail, nb_bs);
                    end
                    if (t == 6'd63) begin st <= ST_LOAD; chk_pend <= 1'b1; end
                end
                default: st <= ST_IDLE;
            endcase
        end
    end

    genvar gi;
    generate
        for (gi = 0; gi < N; gi = gi + 1) begin : g_core
            sha_core #(.ID(gi), .POW2(POW2)) u_core (
                .clk(clk), .c_ld(c_ld), .c_chk(c_chk), .c_st(c_st), .c_wsh(c_wsh),
                .c_feed(c_feed), .c_fid(c_fid), .c_ms(c_ms), .c_md(c_md), .msel(msel),
                .bus_a(bus_a), .bus_e(bus_e), .bus_m(bus_m),
                .feed_w(feed_r), .nbase(nbase),
                .hkw0(hkw0), .c2(c2), .k_next(k_r), .mask(mask), .hit(hit_c[gi])
            );
        end
    endgenerate
endmodule
