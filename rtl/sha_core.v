// =============================================================================
//  sha_core.v  --  datapath SHA-256d de UM nonce (1 rodada por ciclo)
//
//  Todos os cores do miner_engine rodam em LOCKSTEP: controle, ROM de K e
//  barramento de alimentacao da janela sao COMPARTILHADOS; o core so' tem datapath.
//
//  Fluxo por lote (137 ciclos):
//    LOAD : 1 ciclo de teste (hit <= teste(E61+IV7) do lote anterior) e 4 ciclos
//           que empurram o midstate para as cadeias a-d / e-h via barramentos
//           compartilhados bus_a/bus_e (sem mux paralelo por core)
//    P1   : 64 rodadas (hash 1, bloco 2 do header)
//    MS   : 8 ciclos: H1[m] = mid[m] + estado[m] entra EM SERIE no tail do
//           segmento baixo da janela (1 somador/core em vez de 8); no fim
//           estado <= IV e hkw <= IV7+K0+H1[0]
//    P2   : 64 rodadas completas (hash 2) -- digest de 256 bits inteiro (H0..H7),
//           sem custo extra de pipeline (ver comentario no teste de dificuldade,
//           mais abaixo). Nas ultimas 16 rodadas o tail da janela esta' ocioso e
//           recebe EM SERIE as 16 palavras do PROXIMO lote (feed).
//
//  Otimizacoes: hkw = h+K[t]+W[t] pre-somado (caminho critico curto);
//  constantes de carga via set/reset sincrono dos FF (0 LUT); controles one-hot;
//  cargas seriais reaproveitam barramentos compartilhados (area/core -35%).
// =============================================================================
module sha_core #(
    parameter [31:0] ID   = 32'd0,          // nonce = nbase + ID
    parameter        POW2 = 1               // 1: N potencia de 2 -> nonce = nbase | ID
) (
    input  wire         clk,
    input  wire         c_ld,               // carga serial do midstate (cadeias a-d / e-h)
    input  wire         c_chk,              // teste de dificuldade do lote anterior
    input  wire         c_st,               // rodada (estado + hkw)
    input  wire         c_wsh,              // desloca janela
    input  wire         c_feed,             // tail da janela = palavra de feed
    input  wire         c_fid,              // palavra de feed atual e' o nonce
    input  wire         c_ms,               // MS: insere H1[m] no tail do segmento baixo
    input  wire         c_md,               // ultimo ciclo do MS: estado<=IV, hkw<=c2+a
    input  wire [2:0]   msel,               // qual palavra de estado (a..h) entra no MS
    input  wire [31:0]  bus_a, bus_e, bus_m,// mid[3-j], mid[7-j] (LOAD) ; mid[m] (MS)
    input  wire [31:0]  feed_w,
    input  wire [31:0]  nbase,              // so' usado se POW2==0
    input  wire [31:0]  hkw0,               // mid[7] + K0 + w0
    input  wire [31:0]  c2,                 // IV7 + K0 + mid[0]
    input  wire [31:0]  k_next,             // K[t+1]
    input  wire [255:0] mask,
    output reg          hit = 1'b0
);
    localparam [31:0] IV0 = 32'h6a09e667, IV1 = 32'hbb67ae85, IV2 = 32'h3c6ef372,
                      IV3 = 32'ha54ff53a, IV4 = 32'h510e527f, IV5 = 32'h9b05688c,
                      IV6 = 32'h1f83d9ab, IV7 = 32'h5be0cd19;

    reg [31:0]  a, b, c, d, e, f, g, h;
    reg [31:0]  hkw;                        // h + K[t] + W[t]
    reg [511:0] w;                          // janela: w[32*i+:32] = W[t+i]

    wire [31:0] w0  = w[ 31:  0];
    wire [31:0] w1  = w[ 63: 32];
    wire [31:0] w9  = w[319:288];
    wire [31:0] w14 = w[479:448];

    // ---- rodada ----
    wire [31:0] S1 = {e[5:0],  e[31:6]}  ^ {e[10:0], e[31:11]} ^ {e[24:0], e[31:25]};
    wire [31:0] CH = (e & f) ^ (~e & g);
    wire [31:0] S0 = {a[1:0],  a[31:2]}  ^ {a[12:0], a[31:13]} ^ {a[21:0], a[31:22]};
    wire [31:0] MJ = (a & b) ^ (a & c) ^ (b & c);
    wire [31:0] X   = S1 + CH;
    wire [31:0] dh  = d  + hkw;
    wire [31:0] Z   = S0 + MJ;
    wire [31:0] hz  = hkw + Z;
    wire [31:0] e_n = dh + X;               // e' = d + T1
    wire [31:0] a_n = hz + X;               // a' = T1 + S0 + Maj

    // ---- agenda de mensagem ----
    wire [31:0] sg0 = {w1[6:0],   w1[31:7]}   ^ {w1[17:0],  w1[31:18]}  ^ (w1  >> 3);
    wire [31:0] sg1 = {w14[16:0], w14[31:17]} ^ {w14[18:0], w14[31:19]} ^ (w14 >> 10);
    wire [31:0] w_n   = (sg1 + w9) + (sg0 + w0);       // W[t+16]
    wire [31:0] hkw_n = g + k_next + w1;               // h' + K[t+1] + W[t+1]

    // ---- palavra de feed (o nonce e' a unica palavra por-core) ----
    wire [31:0] id_bs = {ID[7:0], ID[15:8], ID[23:16], ID[31:24]};
    wire [31:0] feed_core;
    generate
        if (POW2) begin : g_pow2
            assign feed_core = c_fid ? (feed_w | id_bs) : feed_w;
        end else begin : g_gen
            wire [31:0] nn = nbase + ID;
            assign feed_core = c_fid ? {nn[7:0], nn[15:8], nn[23:16], nn[31:24]} : feed_w;
        end
    endgenerate
    wire [31:0] tail_in = c_feed ? feed_core : w_n;

    // ---- fase 2 (serial): H1[m] = mid[m] + estado[m] ----
    reg [31:0] ssel;
    always @(*) begin
        case (msel)
            3'd0: ssel = a;  3'd1: ssel = b;  3'd2: ssel = c;  3'd3: ssel = d;
            3'd4: ssel = e;  3'd5: ssel = f;  3'd6: ssel = g;  default: ssel = h;
        endcase
    end
    wire [31:0] h1s = bus_m + ssel;
    // w15..w8 = padding do 2o hash (constantes -> set/reset dos FF) ; w7 = H1[m] ; w6..w0 deslocam
    wire [511:0] w_ms = {32'h00000100, 192'd0, 32'h80000000, h1s, w[255:32]};

    // ---- teste de dificuldade: digest INTEIRO de 256 bits, de graca ----
    // P2 agora roda as 64 rodadas completas (nao mais so' 61): no fim, o shift-chain
    // a/b/c/d e e/f/g/h JA guarda exatamente os 4 ultimos valores de cada cadeia
    // (a64,a63,a62,a61 e e64,e63,e62,e61), que sao precisamente H0..H3 e H4..H7 uma
    // vez somados ao IV -- entao o digest de 256 bits sai so' com 8 somadores de 32
    // bits (4 novos: H0..H3 ; H4..H7 ja existiam como h7 antes). Nao precisa de
    // nenhum estagio extra de pipeline, so' 3 ciclos mais de rodada por lote.
    wire [31:0] dg0 = a + IV0, dg1 = b + IV1, dg2 = c + IV2, dg3 = d + IV3;
    wire [31:0] dg4 = e + IV4, dg5 = f + IV5, dg6 = g + IV6, dg7 = h + IV7;
    function [31:0] bswap32;
        input [31:0] x;
        bswap32 = {x[7:0], x[15:8], x[23:16], x[31:24]};
    endfunction
    // topo do numero-hash (convencao Bitcoin: hash exibido = digest com bytes invertidos)
    wire [255:0] hashnum = {bswap32(dg7), bswap32(dg6), bswap32(dg5), bswap32(dg4),
                            bswap32(dg3), bswap32(dg2), bswap32(dg1), bswap32(dg0)};

    always @(posedge clk) begin
        if (c_md) begin
            a <= IV0; b <= IV1; c <= IV2; d <= IV3;
            e <= IV4; f <= IV5; g <= IV6; h <= IV7;
        end else if (c_ld || c_st) begin
            a <= c_ld ? bus_a : a_n;  b <= a; c <= b; d <= c;
            e <= c_ld ? bus_e : e_n;  f <= e; g <= f; h <= g;
        end

        if      (c_md) hkw <= c2 + a;               // IV7 + K0 + H1[0]  (a = a64 ainda intacto)
        else if (c_ld) hkw <= hkw0;
        else if (c_st) hkw <= hkw_n;

        if      (c_ms)  w <= w_ms;
        else if (c_wsh) w <= {tail_in, w[511:32]};

        if (c_chk) hit <= ((hashnum & mask) == 256'd0);
    end
endmodule
