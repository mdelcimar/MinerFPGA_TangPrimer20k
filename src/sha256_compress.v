module sha256_compress (
    input clk,
    input reset,
    input start,
    input [255:0] state_in,
    input [511:0] block,
    output reg busy,
    output reg done,
    output reg [255:0] state_out
);
    reg [31:0] a;
    reg [31:0] b;
    reg [31:0] c;
    reg [31:0] d;
    reg [31:0] e;
    reg [31:0] f;
    reg [31:0] g;
    reg [31:0] h;

    reg [31:0] h0;
    reg [31:0] h1;
    reg [31:0] h2;
    reg [31:0] h3;
    reg [31:0] h4;
    reg [31:0] h5;
    reg [31:0] h6;
    reg [31:0] h7;

    reg [31:0] w0;
    reg [31:0] w1;
    reg [31:0] w2;
    reg [31:0] w3;
    reg [31:0] w4;
    reg [31:0] w5;
    reg [31:0] w6;
    reg [31:0] w7;
    reg [31:0] w8;
    reg [31:0] w9;
    reg [31:0] w10;
    reg [31:0] w11;
    reg [31:0] w12;
    reg [31:0] w13;
    reg [31:0] w14;
    reg [31:0] w15;
    reg [6:0] round;

    wire [31:0] w_round = w0;
    wire [31:0] w_next = s1(w14) + w9 + s0(w1) + w0;
    wire [31:0] t1 = h + bsig1(e) + ch(e, f, g) + k(round) + w_round;
    wire [31:0] t2 = bsig0(a) + maj(a, b, c);

    function [31:0] rotr;
        input [31:0] x;
        input [4:0] n;
        begin
            rotr = (x >> n) | (x << (32 - n));
        end
    endfunction

    function [31:0] ch;
        input [31:0] x;
        input [31:0] y;
        input [31:0] z;
        begin
            ch = (x & y) ^ (~x & z);
        end
    endfunction

    function [31:0] maj;
        input [31:0] x;
        input [31:0] y;
        input [31:0] z;
        begin
            maj = (x & y) ^ (x & z) ^ (y & z);
        end
    endfunction

    function [31:0] bsig0;
        input [31:0] x;
        begin
            bsig0 = rotr(x, 5'd2) ^ rotr(x, 5'd13) ^ rotr(x, 5'd22);
        end
    endfunction

    function [31:0] bsig1;
        input [31:0] x;
        begin
            bsig1 = rotr(x, 5'd6) ^ rotr(x, 5'd11) ^ rotr(x, 5'd25);
        end
    endfunction

    function [31:0] s0;
        input [31:0] x;
        begin
            s0 = rotr(x, 5'd7) ^ rotr(x, 5'd18) ^ (x >> 3);
        end
    endfunction

    function [31:0] s1;
        input [31:0] x;
        begin
            s1 = rotr(x, 5'd17) ^ rotr(x, 5'd19) ^ (x >> 10);
        end
    endfunction

    function [31:0] k;
        input [6:0] i;
        begin
            case (i)
                7'd0: k = 32'h428a2f98; 7'd1: k = 32'h71374491;
                7'd2: k = 32'hb5c0fbcf; 7'd3: k = 32'he9b5dba5;
                7'd4: k = 32'h3956c25b; 7'd5: k = 32'h59f111f1;
                7'd6: k = 32'h923f82a4; 7'd7: k = 32'hab1c5ed5;
                7'd8: k = 32'hd807aa98; 7'd9: k = 32'h12835b01;
                7'd10: k = 32'h243185be; 7'd11: k = 32'h550c7dc3;
                7'd12: k = 32'h72be5d74; 7'd13: k = 32'h80deb1fe;
                7'd14: k = 32'h9bdc06a7; 7'd15: k = 32'hc19bf174;
                7'd16: k = 32'he49b69c1; 7'd17: k = 32'hefbe4786;
                7'd18: k = 32'h0fc19dc6; 7'd19: k = 32'h240ca1cc;
                7'd20: k = 32'h2de92c6f; 7'd21: k = 32'h4a7484aa;
                7'd22: k = 32'h5cb0a9dc; 7'd23: k = 32'h76f988da;
                7'd24: k = 32'h983e5152; 7'd25: k = 32'ha831c66d;
                7'd26: k = 32'hb00327c8; 7'd27: k = 32'hbf597fc7;
                7'd28: k = 32'hc6e00bf3; 7'd29: k = 32'hd5a79147;
                7'd30: k = 32'h06ca6351; 7'd31: k = 32'h14292967;
                7'd32: k = 32'h27b70a85; 7'd33: k = 32'h2e1b2138;
                7'd34: k = 32'h4d2c6dfc; 7'd35: k = 32'h53380d13;
                7'd36: k = 32'h650a7354; 7'd37: k = 32'h766a0abb;
                7'd38: k = 32'h81c2c92e; 7'd39: k = 32'h92722c85;
                7'd40: k = 32'ha2bfe8a1; 7'd41: k = 32'ha81a664b;
                7'd42: k = 32'hc24b8b70; 7'd43: k = 32'hc76c51a3;
                7'd44: k = 32'hd192e819; 7'd45: k = 32'hd6990624;
                7'd46: k = 32'hf40e3585; 7'd47: k = 32'h106aa070;
                7'd48: k = 32'h19a4c116; 7'd49: k = 32'h1e376c08;
                7'd50: k = 32'h2748774c; 7'd51: k = 32'h34b0bcb5;
                7'd52: k = 32'h391c0cb3; 7'd53: k = 32'h4ed8aa4a;
                7'd54: k = 32'h5b9cca4f; 7'd55: k = 32'h682e6ff3;
                7'd56: k = 32'h748f82ee; 7'd57: k = 32'h78a5636f;
                7'd58: k = 32'h84c87814; 7'd59: k = 32'h8cc70208;
                7'd60: k = 32'h90befffa; 7'd61: k = 32'ha4506ceb;
                7'd62: k = 32'hbef9a3f7; 7'd63: k = 32'hc67178f2;
                default: k = 32'h00000000;
            endcase
        end
    endfunction

    always @(posedge clk) begin
        if (reset) begin
            busy <= 1'b0;
            done <= 1'b0;
            state_out <= 256'd0;
            round <= 7'd0;
            w0 <= 32'd0;
            w1 <= 32'd0;
            w2 <= 32'd0;
            w3 <= 32'd0;
            w4 <= 32'd0;
            w5 <= 32'd0;
            w6 <= 32'd0;
            w7 <= 32'd0;
            w8 <= 32'd0;
            w9 <= 32'd0;
            w10 <= 32'd0;
            w11 <= 32'd0;
            w12 <= 32'd0;
            w13 <= 32'd0;
            w14 <= 32'd0;
            w15 <= 32'd0;
        end else begin
            done <= 1'b0;

            if (start && !busy) begin
                h0 <= state_in[255:224];
                h1 <= state_in[223:192];
                h2 <= state_in[191:160];
                h3 <= state_in[159:128];
                h4 <= state_in[127:96];
                h5 <= state_in[95:64];
                h6 <= state_in[63:32];
                h7 <= state_in[31:0];

                a <= state_in[255:224];
                b <= state_in[223:192];
                c <= state_in[191:160];
                d <= state_in[159:128];
                e <= state_in[127:96];
                f <= state_in[95:64];
                g <= state_in[63:32];
                h <= state_in[31:0];

                w0 <= block[511:480];
                w1 <= block[479:448];
                w2 <= block[447:416];
                w3 <= block[415:384];
                w4 <= block[383:352];
                w5 <= block[351:320];
                w6 <= block[319:288];
                w7 <= block[287:256];
                w8 <= block[255:224];
                w9 <= block[223:192];
                w10 <= block[191:160];
                w11 <= block[159:128];
                w12 <= block[127:96];
                w13 <= block[95:64];
                w14 <= block[63:32];
                w15 <= block[31:0];

                round <= 7'd0;
                busy <= 1'b1;
            end else if (busy) begin
                w0 <= w1;
                w1 <= w2;
                w2 <= w3;
                w3 <= w4;
                w4 <= w5;
                w5 <= w6;
                w6 <= w7;
                w7 <= w8;
                w8 <= w9;
                w9 <= w10;
                w10 <= w11;
                w11 <= w12;
                w12 <= w13;
                w13 <= w14;
                w14 <= w15;
                w15 <= w_next;

                h <= g;
                g <= f;
                f <= e;
                e <= d + t1;
                d <= c;
                c <= b;
                b <= a;
                a <= t1 + t2;

                if (round == 7'd63) begin
                    state_out <= {
                        h0 + t1 + t2,
                        h1 + a,
                        h2 + b,
                        h3 + c,
                        h4 + d + t1,
                        h5 + e,
                        h6 + f,
                        h7 + g
                    };
                    busy <= 1'b0;
                    done <= 1'b1;
                end else begin
                    round <= round + 7'd1;
                end
            end
        end
    end
endmodule
