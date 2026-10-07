`timescale 1ns/1ps
// Teste ponta-a-ponta do protocolo v2 (preempcao, checksum, job id, resync)
module tb_top2;
    parameter N = 4;
    parameter CPB = 8;
    reg clk27 = 0; always #5 clk27 = ~clk27;
    reg rx = 1; wire tx; wire [3:0] led_n;
    miner_top #(.N(N), .CLKS_OVERRIDE(CPB), .TMO_BITS(10)) dut
        (.clk27(clk27), .uart_rx(rx), .uart_tx(tx), .led_n(led_n));

    task send_byte(input [7:0] b);
        integer i;
        begin
            rx = 0; repeat (CPB) @(posedge clk27);
            for (i = 0; i < 8; i = i + 1) begin rx = b[i]; repeat (CPB) @(posedge clk27); end
            rx = 1; repeat (CPB) @(posedge clk27);
        end
    endtask
    task wait_c(input integer n); begin repeat (n) @(posedge clk27); end endtask

    reg [7:0] x;
    task sx(input [7:0] b); begin x = x ^ b; send_byte(b); end endtask

    reg [31:0] ja [0:15];   // 0: metralhadora (infinito, zbits=0)
    reg [31:0] jb [0:15];   // 1: genesis (12 lotes, zbits=32)
    task send_job(input [7:0] id, input integer which, input [7:0] corrupt);
        integer q; reg [31:0] wv;
        begin
            x = 8'hA5; send_byte(8'h01); sx(id);
            for (q = 0; q < 13; q = q + 1) begin
                wv = which ? jb[q] : ja[q];
                sx(wv[31:24]); sx(wv[23:16]); sx(wv[15:8]); sx(wv[7:0]);
            end
            wv = which ? jb[13] : ja[13];
            sx(wv[7:0]);
            send_byte(x ^ corrupt);
        end
    endtask

    // ---------------- decodificador de mensagens FPGA->PC ----------------
    reg [7:0] rb; integer k;
    reg [7:0] mt; reg [7:0] mb [0:15]; integer mi = 0, mneed = 0;
    task emit;
        begin
            case (mt)
              8'h80: $display("MSG %0d FOUND %02x %02x%02x%02x%02x", $time/10, mb[0], mb[1],mb[2],mb[3],mb[4]);
              8'h81: $display("MSG %0d DONE %02x %0d %0d", $time/10, mb[0], {mb[1],mb[2],mb[3],mb[4]}, {mb[5],mb[6],mb[7],mb[8]});
              8'h82: $display("MSG %0d INFO %0d %0d %0d %0d", $time/10, mb[0], mb[1], mb[2], mb[3]);
              8'h83: $display("MSG %0d STAT %02x %0d %0d %0d", $time/10, mb[0], mb[1], {mb[2],mb[3],mb[4],mb[5]}, {mb[6],mb[7],mb[8],mb[9]});
              8'h85: $display("MSG %0d PING %02x", $time/10, mb[0]);
              8'h86: $display("MSG %0d NAK %02x", $time/10, mb[0]);
              8'h87: $display("MSG %0d ACK %02x", $time/10, mb[0]);
            endcase
        end
    endtask
    task handle(input [7:0] b);
        begin
            if (mneed == 0) begin
                mt = b; mi = 0;
                case (b)
                    8'h80: mneed = 5; 8'h81: mneed = 9; 8'h82: mneed = 4; 8'h83: mneed = 10;
                    8'h85: mneed = 1; 8'h86: mneed = 1; 8'h87: mneed = 1;
                    default: $display("MSG %0d BADTYPE %02x", $time/10, b);
                endcase
            end else begin
                mb[mi] = b; mi = mi + 1; mneed = mneed - 1;
                if (mneed == 0) emit;
            end
        end
    endtask
    always @(negedge tx) begin
        repeat (CPB + CPB/2) @(posedge clk27);
        for (k = 0; k < 8; k = k + 1) begin rb[k] = tx; repeat (CPB) @(posedge clk27); end
        handle(rb);
    end

    integer q2;
    initial begin
        $readmemh("sim/job_fire.hex", ja);
        $readmemh("sim/job.hex", jb);
        wait_c(300);
        $display("SCEN %0d S1", $time/10);
        send_byte(8'h05); send_byte(8'h5a); wait_c(600);
        send_byte(8'h02); wait_c(800);
        send_byte(8'h06); wait_c(1200);
        $display("SCEN %0d S2", $time/10);
        send_job(8'd1, 1, 0); wait_c(9000);
        $display("SCEN %0d S3", $time/10);
        send_job(8'd2, 0, 0); wait_c(20000);
        $display("SENT %0d PING 77", $time/10);
        send_byte(8'h05); send_byte(8'h77); wait_c(6000);
        $display("SCEN %0d S4", $time/10);
        send_job(8'd3, 1, 0); wait_c(15000);
        $display("SCEN %0d S5", $time/10);
        send_job(8'd4, 0, 0); wait_c(8000);
        send_job(8'd5, 1, 8'h5A); wait_c(3000);            // checksum ruim: NAK, job 4 continua
        send_byte(8'h06); wait_c(3000);
        $display("SENT %0d STOP", $time/10);
        send_byte(8'h03); wait_c(30000);
        $display("SCEN %0d S6", $time/10);
        send_byte(8'h01); for (q2 = 0; q2 < 20; q2 = q2 + 1) send_byte(8'h11);
        wait_c(3000);                                       // > timeout (1024 clks)
        send_byte(8'h05); send_byte(8'h66); wait_c(2000);
        $display("SCEN %0d S7", $time/10);
        send_byte(8'h01); for (q2 = 0; q2 < 10; q2 = q2 + 1) send_byte(8'h22);
        for (q2 = 0; q2 < 60; q2 = q2 + 1) send_byte(8'h00);  // flood de resync
        send_byte(8'h05); send_byte(8'h99); wait_c(3000);
        $display("SCEN %0d S8", $time/10);
        send_job(8'd6, 0, 0); wait_c(1500);
        send_job(8'd7, 0, 0); wait_c(1500);
        send_job(8'd8, 0, 0); wait_c(1500);
        send_job(8'd9, 1, 0); wait_c(20000);
        $display("SCEN %0d END", $time/10);
        $finish;
    end
endmodule
