`timescale 1ns/1ps
// Teste ponta-a-ponta do miner_top via UART (bit-banging serial no testbench)
module tb_starve;
    parameter N = 4;
    parameter CPB = 8;                       // clks por bit (rapido p/ simulacao)
    reg clk27 = 0; always #5 clk27 = ~clk27;
    reg rx = 1; wire tx; wire [3:0] led_n;
    miner_top #(.N(N), .CLKS_OVERRIDE(CPB)) dut (.clk27(clk27), .uart_rx(rx), .uart_tx(tx), .led_n(led_n));

    task send_byte(input [7:0] b);
        integer i;
        begin
            rx = 0; repeat (CPB) @(posedge clk27);
            for (i = 0; i < 8; i = i + 1) begin rx = b[i]; repeat (CPB) @(posedge clk27); end
            rx = 1; repeat (CPB) @(posedge clk27);
        end
    endtask

    // receptor
    reg [7:0] rb; integer k;
    always @(negedge tx) begin
        repeat (CPB + CPB/2) @(posedge clk27);
        for (k = 0; k < 8; k = k + 1) begin rb[k] = tx; repeat (CPB) @(posedge clk27); end
        $display("RX %02x", rb);
    end

    reg [31:0] jm [0:15]; integer q;
    initial begin
        $readmemh("sim/job_fire.hex", jm);
        repeat (300) @(posedge clk27);
        $display("--- JOB infinito zbits=0 (metralhadora)");
        send_byte(8'h01);
        for (q = 0; q < 13; q = q + 1) begin
            send_byte(jm[q][31:24]); send_byte(jm[q][23:16]); send_byte(jm[q][15:8]); send_byte(jm[q][7:0]);
        end
        send_byte(jm[13][7:0]);
        repeat (20000) @(posedge clk27);
        $display("--- PING 5a durante a metralhadora");
        send_byte(8'h05); send_byte(8'h5a);
        repeat (150000) @(posedge clk27);
        $display("--- FIM");
        $finish;
    end
endmodule
