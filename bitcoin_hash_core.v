module bitcoin_hash_core #(
    parameter NONCE_OFFSET = 32'd0,
    parameter NONCE_STRIDE = 32'd1
) (
    input clk,
    input reset,
    input start,
    input stop,
    input [255:0] midstate,
    input [95:0] tail,
    input [255:0] target,
    output reg running,
    output reg found,
    output reg [31:0] found_nonce,
    output reg [255:0] found_hash,
    output reg [31:0] current_nonce
);
    localparam [255:0] SHA256_IV = {
        32'h6a09e667, 32'hbb67ae85, 32'h3c6ef372, 32'ha54ff53a,
        32'h510e527f, 32'h9b05688c, 32'h1f83d9ab, 32'h5be0cd19
    };

    localparam S_IDLE = 3'd0;
    localparam S_FIRST_START = 3'd1;
    localparam S_FIRST_WAIT = 3'd2;
    localparam S_SECOND_START = 3'd3;
    localparam S_SECOND_WAIT = 3'd4;
    localparam S_REPORT = 3'd5;

    reg [2:0] state;
    reg sha_start;
    reg [255:0] sha_state_in;
    reg [511:0] sha_block;
    wire sha_busy;
    wire sha_done;
    wire [255:0] sha_state_out;

    reg [255:0] first_digest;

    sha256_compress sha (
        .clk(clk),
        .reset(reset),
        .start(sha_start),
        .state_in(sha_state_in),
        .block(sha_block),
        .busy(sha_busy),
        .done(sha_done),
        .state_out(sha_state_out)
    );

    wire [511:0] first_block = {
        tail[95:64], tail[63:32], tail[31:0], current_nonce,
        32'h80000000, 32'h00000000, 32'h00000000, 32'h00000000,
        32'h00000000, 32'h00000000, 32'h00000000, 32'h00000000,
        32'h00000000, 32'h00000000, 32'h00000000, 32'h00000280
    };

    wire [511:0] second_block = {
        first_digest,
        32'h80000000, 32'h00000000, 32'h00000000, 32'h00000000,
        32'h00000000, 32'h00000000, 32'h00000000, 32'h00000100
    };

    function [255:0] reverse_words_256;
        input [255:0] value;
        begin
            reverse_words_256 = {
                value[31:0],
                value[63:32],
                value[95:64],
                value[127:96],
                value[159:128],
                value[191:160],
                value[223:192],
                value[255:224]
            };
        end
    endfunction

    always @(posedge clk) begin
        if (reset) begin
            state <= S_IDLE;
            sha_start <= 1'b0;
            sha_state_in <= 256'd0;
            sha_block <= 512'd0;
            first_digest <= 256'd0;
            running <= 1'b0;
            found <= 1'b0;
            found_nonce <= 32'd0;
            found_hash <= 256'd0;
            current_nonce <= 32'd0;
        end else begin
            sha_start <= 1'b0;

            if (stop) begin
                state <= S_IDLE;
                running <= 1'b0;
            end else begin
                case (state)
                    S_IDLE: begin
                        found <= 1'b0;
                        running <= 1'b0;
                        if (start) begin
                            current_nonce <= NONCE_OFFSET;
                            running <= 1'b1;
                            state <= S_FIRST_START;
                        end
                    end

                    S_FIRST_START: begin
                        if (!sha_busy) begin
                            sha_state_in <= midstate;
                            sha_block <= first_block;
                            sha_start <= 1'b1;
                            state <= S_FIRST_WAIT;
                        end
                    end

                    S_FIRST_WAIT: begin
                        if (sha_done) begin
                            first_digest <= sha_state_out;
                            state <= S_SECOND_START;
                        end
                    end

                    S_SECOND_START: begin
                        if (!sha_busy) begin
                            sha_state_in <= SHA256_IV;
                            sha_block <= second_block;
                            sha_start <= 1'b1;
                            state <= S_SECOND_WAIT;
                        end
                    end

                    S_SECOND_WAIT: begin
                        if (sha_done) begin
                            if (reverse_words_256(sha_state_out) <= target) begin
                                found <= 1'b1;
                                found_nonce <= current_nonce;
                                found_hash <= sha_state_out;
                                state <= S_REPORT;
                            end else begin
                                current_nonce <= current_nonce + NONCE_STRIDE;
                                state <= S_FIRST_START;
                            end
                        end
                    end

                    S_REPORT: begin
                        running <= 1'b0;
                        if (start) begin
                            found <= 1'b0;
                            current_nonce <= NONCE_OFFSET;
                            running <= 1'b1;
                            state <= S_FIRST_START;
                        end
                    end

                    default: begin
                        state <= S_IDLE;
                    end
                endcase
            end
        end
    end
endmodule
