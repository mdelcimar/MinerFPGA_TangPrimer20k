`timescale 1ns/1ps
module tb_engine;
    parameter N = 4;
    parameter POPGAP = 0;   // >0: consumidor lento (testa backpressure da FIFO)
    reg clk = 0; always #5 clk = ~clk;
    reg rst = 1, start = 0, stop = 0;
    reg [31:0] jm [0:15]; initial begin : ini integer q; for (q=0;q<16;q=q+1) jm[q]=0; end
    wire fv; wire [31:0] fn; reg pop = 0;
    wire idle; wire [31:0] batches, cycles;
    wire [31:0] w0=jm[0],w1=jm[1];
    miner_engine #(.N(N)) dut (
        .clk(clk), .rst(rst), .start(start), .stop(stop),
        .mid({jm[7],jm[6],jm[5],jm[4],jm[3],jm[2],jm[1],jm[0]}),
        .wtail({jm[10],jm[9],jm[8]}), .nonce0(jm[11]), .nbatches(jm[12]),
        .zbits(jm[13][5:0]),
        .found_valid(fv), .found_nonce(fn), .found_pop(pop),
        .idle(idle), .batches(batches), .cycles(cycles));
    integer nfound = 0;
    integer gap = 0;
    always @(negedge clk) begin
        pop <= 0;
        if (gap > 0) gap = gap - 1;
        else if (fv && !pop) begin $display("FOUND %08x", fn); pop <= 1; nfound = nfound + 1; gap = POPGAP; end
    end
    initial begin
        $readmemh("sim/job.hex", jm);
        repeat (5) @(negedge clk); rst = 0; repeat (3) @(negedge clk);
        start = 1; @(negedge clk); start = 0;
        repeat (4) @(negedge clk);
        wait (idle);
        repeat (100) @(posedge clk);
        while (fv || pop || gap > 0) @(posedge clk);   // espera consumidor esvaziar a FIFO
        repeat (50) @(posedge clk);
        $display("DONE batches=%0d cycles=%0d found=%0d", batches, cycles, nfound);
        $finish;
    end
    initial begin #400000000; $display("TIMEOUT"); $finish; end
endmodule
