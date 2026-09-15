module uart_tx #(
    parameter CLKS_PER_BIT = 234
) (
    input clk,
    input reset,
    input start,
    input [7:0] data,
    output reg tx,
    output reg busy
);
    localparam S_IDLE = 3'd0;
    localparam S_START = 3'd1;
    localparam S_DATA = 3'd2;
    localparam S_STOP = 3'd3;

    reg [2:0] state;
    reg [15:0] clk_count;
    reg [2:0] bit_index;
    reg [7:0] tx_shift;

    always @(posedge clk) begin
        if (reset) begin
            state <= S_IDLE;
            clk_count <= 16'd0;
            bit_index <= 3'd0;
            tx_shift <= 8'd0;
            tx <= 1'b1;
            busy <= 1'b0;
        end else begin
            case (state)
                S_IDLE: begin
                    tx <= 1'b1;
                    busy <= 1'b0;
                    clk_count <= 16'd0;
                    bit_index <= 3'd0;
                    if (start) begin
                        tx_shift <= data;
                        busy <= 1'b1;
                        state <= S_START;
                    end
                end

                S_START: begin
                    tx <= 1'b0;
                    if (clk_count == CLKS_PER_BIT - 1) begin
                        clk_count <= 16'd0;
                        state <= S_DATA;
                    end else begin
                        clk_count <= clk_count + 16'd1;
                    end
                end

                S_DATA: begin
                    tx <= tx_shift[bit_index];
                    if (clk_count == CLKS_PER_BIT - 1) begin
                        clk_count <= 16'd0;
                        if (bit_index == 3'd7) begin
                            bit_index <= 3'd0;
                            state <= S_STOP;
                        end else begin
                            bit_index <= bit_index + 3'd1;
                        end
                    end else begin
                        clk_count <= clk_count + 16'd1;
                    end
                end

                S_STOP: begin
                    tx <= 1'b1;
                    if (clk_count == CLKS_PER_BIT - 1) begin
                        clk_count <= 16'd0;
                        state <= S_IDLE;
                    end else begin
                        clk_count <= clk_count + 16'd1;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
