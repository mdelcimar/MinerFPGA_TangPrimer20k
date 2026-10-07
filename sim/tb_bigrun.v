`timescale 1ns/1ps
module tb_bigrun;
    parameter N = 8;
    reg clk = 0; always #5 clk = ~clk;
    reg rst = 1, start = 0;
    reg [31:0] jm [0:15];
    wire fv; wire [31:0] fn; reg pop = 0;
    wire idle; wire [31:0] batches, cycles;
    miner_engine #(.N(N)) dut (
        .clk(clk), .rst(rst), .start(start), .stop(1'b0),
        .mid({jm[7],jm[6],jm[5],jm[4],jm[3],jm[2],jm[1],jm[0]}),
        .wtail({jm[10],jm[9],jm[8]}), .nonce0(jm[11]), .nbatches(jm[12]),
        .zbits(jm[13][5:0]),
        .found_valid(fv), .found_nonce(fn), .found_pop(pop),
        .idle(idle), .batches(batches), .cycles(cycles));
    always @(posedge clk) begin
        pop <= 0;
        if (fv && !pop) begin $display("FOUND %08x", fn); pop <= 1; end
    end
    initial begin
        $readmemh("sim/job.hex", jm);
        repeat (5) @(negedge clk); rst = 0; repeat (3) @(negedge clk);
        start = 1; @(negedge clk); start = 0;
        repeat (4) @(negedge clk);
        wait (idle);
        repeat (50) @(posedge clk);
        $display("DONE batches=%0d cycles=%0d", batches, cycles);
        $finish;
    end
    initial begin #100000000000; $display("TIMEOUT"); $finish; end
endmodule
