module uart_rx #(
    parameter CLKS_PER_BIT = 234
) (
    input clk,
    input reset,
    input rx,
    output reg [7:0] data,
    output reg valid
);
    localparam S_IDLE = 3'd0;
    localparam S_START = 3'd1;
    localparam S_DATA = 3'd2;
    localparam S_STOP = 3'd3;

    reg [2:0] state;
    reg [15:0] clk_count;
    reg [2:0] bit_index;
    reg [7:0] rx_shift;
    reg rx_meta;
    reg rx_sync;

    always @(posedge clk) begin
        if (reset) begin
            state <= S_IDLE;
            clk_count <= 16'd0;
            bit_index <= 3'd0;
            rx_shift <= 8'd0;
            rx_meta <= 1'b1;
            rx_sync <= 1'b1;
            data <= 8'd0;
            valid <= 1'b0;
        end else begin
            rx_meta <= rx;
            rx_sync <= rx_meta;
            valid <= 1'b0;

            case (state)
                S_IDLE: begin
                    clk_count <= 16'd0;
                    bit_index <= 3'd0;
                    if (!rx_sync) begin
                        state <= S_START;
                    end
                end

                S_START: begin
                    if (clk_count == (CLKS_PER_BIT / 2)) begin
                        clk_count <= 16'd0;
                        state <= rx_sync ? S_IDLE : S_DATA;
                    end else begin
                        clk_count <= clk_count + 16'd1;
                    end
                end

                S_DATA: begin
                    if (clk_count == CLKS_PER_BIT - 1) begin
                        clk_count <= 16'd0;
                        rx_shift[bit_index] <= rx_sync;
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
                    if (clk_count == CLKS_PER_BIT - 1) begin
                        data <= rx_shift;
                        valid <= rx_sync;
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
