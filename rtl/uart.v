// UART 8N1 minimo (rx com dupla sincronizacao, amostragem no meio do bit)
module uart_rx #(parameter CLKS_PER_BIT = 938) (
    input  wire       clk,
    input  wire       rx,
    output reg  [7:0] data  = 8'd0,
    output reg        valid = 1'b0
);
    localparam CW = $clog2(CLKS_PER_BIT + 1);
    reg [1:0]  sync = 2'b11;
    reg [CW-1:0] cnt = 0;
    reg [3:0]  bitn = 0;
    reg [7:0]  sh   = 0;
    reg        busy = 0;
    always @(posedge clk) begin
        sync  <= {sync[0], rx};
        valid <= 1'b0;
        if (!busy) begin
            if (!sync[1]) begin busy <= 1'b1; cnt <= CLKS_PER_BIT/2; bitn <= 0; end
        end else if (cnt != 0) begin
            cnt <= cnt - 1'b1;
        end else begin
            cnt <= CLKS_PER_BIT - 1;
            if (bitn == 0) begin
                if (sync[1]) busy <= 1'b0;             // start bit falso
                else bitn <= 4'd1;
            end else if (bitn <= 8) begin
                sh <= {sync[1], sh[7:1]}; bitn <= bitn + 1'b1;
            end else begin                              // stop bit
                busy <= 1'b0;
                if (sync[1]) begin data <= sh; valid <= 1'b1; end
            end
        end
    end
endmodule

module uart_tx #(parameter CLKS_PER_BIT = 938) (
    input  wire       clk,
    input  wire [7:0] data,
    input  wire       valid,
    output wire       ready,
    output reg        tx = 1'b1
);
    localparam CW = $clog2(CLKS_PER_BIT + 1);
    reg [CW-1:0] cnt = 0;
    reg [3:0] bitn = 0;
    reg [9:0] sh = 10'h3FF;
    reg busy = 0;
    assign ready = ~busy;
    always @(posedge clk) begin
        if (!busy) begin
            if (valid) begin sh <= {1'b1, data, 1'b0}; busy <= 1'b1; cnt <= CLKS_PER_BIT - 1; bitn <= 0; tx <= 1'b0; end
        end else if (cnt != 0) cnt <= cnt - 1'b1;
        else begin
            cnt <= CLKS_PER_BIT - 1;
            if (bitn == 9) begin busy <= 1'b0; tx <= 1'b1; end
            else begin bitn <= bitn + 1'b1; tx <= sh[bitn + 1]; end
        end
    end
endmodule
