`timescale 1ns/1ps
// Testa exatamente o cenario relatado: job A rodando, cancelado no MEIO
// (nao no final natural), job B enviado logo em seguida -- via UART real,
// igual ao que o script Python faz.
module tb_stop_restart;
    parameter N = 4;
    parameter CPB = 8;
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

    task send_job(input [255:0] mid, input [95:0] wtail, input [31:0] nonce0,
                   input [31:0] nbatches, input [7:0] zbits);
        integer w;
        begin
            send_byte(8'h01);
            for (w = 7; w >= 0; w = w - 1) begin   // 8 palavras x 4 bytes = 32 bytes
                send_byte(mid[32*w+24 +: 8]); send_byte(mid[32*w+16 +: 8]);
                send_byte(mid[32*w+8  +: 8]); send_byte(mid[32*w    +: 8]);
            end
            for (w = 2; w >= 0; w = w - 1) begin   // 3 palavras x 4 bytes = 12 bytes
                send_byte(wtail[32*w+24 +: 8]); send_byte(wtail[32*w+16 +: 8]);
                send_byte(wtail[32*w+8  +: 8]); send_byte(wtail[32*w    +: 8]);
            end
            send_byte(nonce0[31:24]); send_byte(nonce0[23:16]); send_byte(nonce0[15:8]); send_byte(nonce0[7:0]);
            send_byte(nbatches[31:24]); send_byte(nbatches[23:16]); send_byte(nbatches[15:8]); send_byte(nbatches[7:0]);
            send_byte(zbits);
        end
    endtask

    reg [7:0] rb; integer k;
    integer n_found = 0, n_done = 0;
    reg [31:0] found_list [0:63];
    reg [31:0] done_batches, done_cycles;
    always @(negedge tx) begin
        repeat (CPB + CPB/2) @(posedge clk27);
        for (k = 0; k < 8; k = k + 1) begin rb[k] = tx; repeat (CPB) @(posedge clk27); end
        if (rb == 8'h80) begin
            reg [31:0] nn; integer j;
            for (j = 0; j < 4; j = j + 1) begin
                repeat (CPB+CPB/2) @(posedge clk27);
                for (k=0;k<8;k=k+1) begin nn[8*j+k]=tx; repeat(CPB) @(posedge clk27); end
            end
            found_list[n_found] = {nn[7:0],nn[15:8],nn[23:16],nn[31:24]};
            $display("  FOUND %08x", found_list[n_found]);
            n_found = n_found + 1;
        end else if (rb == 8'h81) begin
            reg [63:0] payload; integer j;
            for (j = 0; j < 8; j = j + 1) begin
                repeat (CPB+CPB/2) @(posedge clk27);
                for (k=0;k<8;k=k+1) begin payload[8*j+k]=tx; repeat(CPB) @(posedge clk27); end
            end
            done_batches = {payload[7:0],payload[15:8],payload[23:16],payload[31:24]};
            done_cycles  = {payload[39:32],payload[47:40],payload[55:48],payload[63:56]};
            $display("  DONE batches=%0d cycles=%0d", done_batches, done_cycles);
            n_done = n_done + 1;
        end
    end

    // job A: header aleatorio, zbits baixo (bastante hits, roda "por muito tempo" -> nunca termina antes de cancelarmos)
    // job B: bloco genesis, zbits=32 -> so' 1 hit esperado, DEVE achar exatamente 0x7c2bac1d
    reg [255:0] midA = 256'h1111111122222222333333334444444455555555666666667777777788888888;
    reg [95:0]  wtailA = 96'h99999999aaaaaaaabbbbbbbb;
    reg [255:0] midB, mid_genesis;
    reg [95:0]  wtailB;
    integer jj;
    reg [31:0] jm[0:15];

    initial begin
        // usa o mesmo job.hex do genesis (gerado por sim/vectors.py genesis N) p/ montar job B
        $readmemh("sim/job.hex", jm);
        midB = {jm[7],jm[6],jm[5],jm[4],jm[3],jm[2],jm[1],jm[0]};
        wtailB = {jm[10],jm[9],jm[8]};

        repeat (300) @(posedge clk27);

        $display("=== JOB A: header aleatorio, zbits=2 (MUITOS hits, roda 'pra sempre') ===");
        send_job(midA, wtailA, 32'h00000000, 32'd0, 8'd2);   // nbatches=0 = infinito
        $display("--- job A enviado, aguardando hits ---");

        // deixa rodar um pouco, ate' aparecer pelo menos 1 FOUND de A (prova que A esta' rodando de verdade)
        fork
            wait (n_found >= 1);
            begin repeat (500000) @(posedge clk27); $display(">>> FAIL: job A nunca produziu FOUND (nem controle iniciou) <<<"); $finish; end
        join_any
        disable fork;
        $display("--- job A confirmado rodando (achou pelo menos 1 hit), cancelando NO MEIO agora ---");
        n_found = 0;  // reseta contador pra so' contar os hits do job B daqui pra frente

        $display("=== Envia JOB B (genesis) IMEDIATAMENTE, sem esperar A terminar ===");
        send_job(midB, wtailB, jm[11], jm[12], jm[13][7:0]);

        // espera o DONE de B (com timeout generoso)
        fork
            begin
                wait (n_done >= 1);
                $display("=== JOB B terminou. hits de B = %0d, esperado = 1 (0x7c2bac1d) ===", n_found);
                if (n_found == 1 && found_list[0] == 32'h7c2bac1d)
                    $display(">>> PASS: sistema recuperou corretamente do cancelamento no meio <<<");
                else
                    $display(">>> FAIL: hits=%0d found[0]=%08x (esperado 1 hit = 7c2bac1d) <<<", n_found, found_list[0]);
            end
            begin repeat (2000000) @(posedge clk27); $display(">>> FAIL: TIMEOUT -- FPGA travou apos o cancelamento! <<<"); end
        join_any
        disable fork;
        $finish;
    end
endmodule
