module top (
    input clk,
    input uart_rx_pin,
    output uart_tx_pin,
    output [5:0] led
);
    localparam CLKS_PER_BIT = 234; // 27 MHz / 115200 baud.
    localparam JOB_BYTES = 76;
    localparam FOUND_RESP_BYTES = 37;
    localparam ECHO_RESP_BYTES = 77;

    reg [23:0] reset_counter = 24'd0;
    wire reset = !reset_counter[23];

    always @(posedge clk) begin
        if (!reset_counter[23]) begin
            reset_counter <= reset_counter + 24'd1;
        end
    end

    wire [7:0] rx_data;
    wire rx_valid;
    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) rx0 (
        .clk(clk),
        .reset(reset),
        .rx(uart_rx_pin),
        .data(rx_data),
        .valid(rx_valid)
    );

    reg tx_start;
    reg [7:0] tx_data;
    wire tx_busy;
    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) tx0 (
        .clk(clk),
        .reset(reset),
        .start(tx_start),
        .data(tx_data),
        .tx(uart_tx_pin),
        .busy(tx_busy)
    );

    reg core_start;
    reg core_stop;
    reg core_start_pending;
    reg [255:0] midstate;
    reg [95:0] tail;
    reg [255:0] target;
    
    wire [3:0] core_found_vec;
    wire [3:0] core_running_vec;
    wire [31:0] core_found_nonce_arr [0:3];
    wire [255:0] core_found_hash_arr [0:3];
    wire [31:0] current_nonce_arr [0:3];
    wire core_running;
    reg core_found;
    reg [31:0] core_found_nonce;
    reg [255:0] core_found_hash;
    wire [31:0] current_nonce;

    genvar i;
    generate
        for (i = 0; i < 4; i = i + 1) begin : cores
            bitcoin_hash_core #(
                .NONCE_OFFSET(i),
                .NONCE_STRIDE(4)
            ) core_i (
                .clk(clk),
                .reset(reset),
                .start(core_start),
                .stop(core_stop),
                .midstate(midstate),
                .tail(tail),
                .target(target),
                .running(core_running_vec[i]),
                .found(core_found_vec[i]),
                .found_nonce(core_found_nonce_arr[i]),
                .found_hash(core_found_hash_arr[i]),
                .current_nonce(current_nonce_arr[i])
            );
        end
    endgenerate

    assign core_running = |core_running_vec;
    assign current_nonce = current_nonce_arr[0];

    integer j;
    always @(posedge clk) begin
        if (reset) begin
            core_found <= 1'b0;
            core_found_nonce <= 32'd0;
            core_found_hash <= 256'd0;
        end else begin
            if (!core_found) begin
                if (core_found_vec[0]) begin
                    core_found <= 1'b1;
                    core_found_nonce <= core_found_nonce_arr[0];
                    core_found_hash <= core_found_hash_arr[0];
                end else if (core_found_vec[1]) begin
                    core_found <= 1'b1;
                    core_found_nonce <= core_found_nonce_arr[1];
                    core_found_hash <= core_found_hash_arr[1];
                end else if (core_found_vec[2]) begin
                    core_found <= 1'b1;
                    core_found_nonce <= core_found_nonce_arr[2];
                    core_found_hash <= core_found_hash_arr[2];
                end else if (core_found_vec[3]) begin
                    core_found <= 1'b1;
                    core_found_nonce <= core_found_nonce_arr[3];
                    core_found_hash <= core_found_hash_arr[3];
                end
            end
        end
    end

    localparam R_SYNC0 = 3'd0;
    localparam R_SYNC1 = 3'd1;
    localparam R_CMD = 3'd2;
    localparam R_PAYLOAD = 3'd3;

    reg [2:0] rx_state;
    reg [6:0] payload_count;
    reg [7:0] command;

    localparam T_IDLE = 3'd0;
    localparam T_SEND = 3'd1;
    localparam T_WAIT = 3'd2;

    reg [2:0] tx_state;
    reg [6:0] tx_index;
    reg found_seen;
    reg echo_toggle;
    reg echo_seen_toggle;
    reg tx_echo;

    function [7:0] found_response_byte;
        input [6:0] index;
        begin
            case (index)
                6'd0: found_response_byte = "F";
                6'd1: found_response_byte = core_found_nonce[31:24];
                6'd2: found_response_byte = core_found_nonce[23:16];
                6'd3: found_response_byte = core_found_nonce[15:8];
                6'd4: found_response_byte = core_found_nonce[7:0];
                6'd5: found_response_byte = core_found_hash[255:248];
                6'd6: found_response_byte = core_found_hash[247:240];
                6'd7: found_response_byte = core_found_hash[239:232];
                6'd8: found_response_byte = core_found_hash[231:224];
                6'd9: found_response_byte = core_found_hash[223:216];
                6'd10: found_response_byte = core_found_hash[215:208];
                6'd11: found_response_byte = core_found_hash[207:200];
                6'd12: found_response_byte = core_found_hash[199:192];
                6'd13: found_response_byte = core_found_hash[191:184];
                6'd14: found_response_byte = core_found_hash[183:176];
                6'd15: found_response_byte = core_found_hash[175:168];
                6'd16: found_response_byte = core_found_hash[167:160];
                6'd17: found_response_byte = core_found_hash[159:152];
                6'd18: found_response_byte = core_found_hash[151:144];
                6'd19: found_response_byte = core_found_hash[143:136];
                6'd20: found_response_byte = core_found_hash[135:128];
                6'd21: found_response_byte = core_found_hash[127:120];
                6'd22: found_response_byte = core_found_hash[119:112];
                6'd23: found_response_byte = core_found_hash[111:104];
                6'd24: found_response_byte = core_found_hash[103:96];
                6'd25: found_response_byte = core_found_hash[95:88];
                6'd26: found_response_byte = core_found_hash[87:80];
                6'd27: found_response_byte = core_found_hash[79:72];
                6'd28: found_response_byte = core_found_hash[71:64];
                6'd29: found_response_byte = core_found_hash[63:56];
                6'd30: found_response_byte = core_found_hash[55:48];
                6'd31: found_response_byte = core_found_hash[47:40];
                6'd32: found_response_byte = core_found_hash[39:32];
                6'd33: found_response_byte = core_found_hash[31:24];
                6'd34: found_response_byte = core_found_hash[23:16];
                6'd35: found_response_byte = core_found_hash[15:8];
                6'd36: found_response_byte = core_found_hash[7:0];
                default: found_response_byte = 8'h00;
            endcase
        end
    endfunction

    function [7:0] echo_response_byte;
        input [6:0] index;
        begin
            case (index)
                7'd0: echo_response_byte = "E";
                7'd1: echo_response_byte = midstate[255:248];
                7'd2: echo_response_byte = midstate[247:240];
                7'd3: echo_response_byte = midstate[239:232];
                7'd4: echo_response_byte = midstate[231:224];
                7'd5: echo_response_byte = midstate[223:216];
                7'd6: echo_response_byte = midstate[215:208];
                7'd7: echo_response_byte = midstate[207:200];
                7'd8: echo_response_byte = midstate[199:192];
                7'd9: echo_response_byte = midstate[191:184];
                7'd10: echo_response_byte = midstate[183:176];
                7'd11: echo_response_byte = midstate[175:168];
                7'd12: echo_response_byte = midstate[167:160];
                7'd13: echo_response_byte = midstate[159:152];
                7'd14: echo_response_byte = midstate[151:144];
                7'd15: echo_response_byte = midstate[143:136];
                7'd16: echo_response_byte = midstate[135:128];
                7'd17: echo_response_byte = midstate[127:120];
                7'd18: echo_response_byte = midstate[119:112];
                7'd19: echo_response_byte = midstate[111:104];
                7'd20: echo_response_byte = midstate[103:96];
                7'd21: echo_response_byte = midstate[95:88];
                7'd22: echo_response_byte = midstate[87:80];
                7'd23: echo_response_byte = midstate[79:72];
                7'd24: echo_response_byte = midstate[71:64];
                7'd25: echo_response_byte = midstate[63:56];
                7'd26: echo_response_byte = midstate[55:48];
                7'd27: echo_response_byte = midstate[47:40];
                7'd28: echo_response_byte = midstate[39:32];
                7'd29: echo_response_byte = midstate[31:24];
                7'd30: echo_response_byte = midstate[23:16];
                7'd31: echo_response_byte = midstate[15:8];
                7'd32: echo_response_byte = midstate[7:0];
                7'd33: echo_response_byte = tail[95:88];
                7'd34: echo_response_byte = tail[87:80];
                7'd35: echo_response_byte = tail[79:72];
                7'd36: echo_response_byte = tail[71:64];
                7'd37: echo_response_byte = tail[63:56];
                7'd38: echo_response_byte = tail[55:48];
                7'd39: echo_response_byte = tail[47:40];
                7'd40: echo_response_byte = tail[39:32];
                7'd41: echo_response_byte = tail[31:24];
                7'd42: echo_response_byte = tail[23:16];
                7'd43: echo_response_byte = tail[15:8];
                7'd44: echo_response_byte = tail[7:0];
                7'd45: echo_response_byte = target[255:248];
                7'd46: echo_response_byte = target[247:240];
                7'd47: echo_response_byte = target[239:232];
                7'd48: echo_response_byte = target[231:224];
                7'd49: echo_response_byte = target[223:216];
                7'd50: echo_response_byte = target[215:208];
                7'd51: echo_response_byte = target[207:200];
                7'd52: echo_response_byte = target[199:192];
                7'd53: echo_response_byte = target[191:184];
                7'd54: echo_response_byte = target[183:176];
                7'd55: echo_response_byte = target[175:168];
                7'd56: echo_response_byte = target[167:160];
                7'd57: echo_response_byte = target[159:152];
                7'd58: echo_response_byte = target[151:144];
                7'd59: echo_response_byte = target[143:136];
                7'd60: echo_response_byte = target[135:128];
                7'd61: echo_response_byte = target[127:120];
                7'd62: echo_response_byte = target[119:112];
                7'd63: echo_response_byte = target[111:104];
                7'd64: echo_response_byte = target[103:96];
                7'd65: echo_response_byte = target[95:88];
                7'd66: echo_response_byte = target[87:80];
                7'd67: echo_response_byte = target[79:72];
                7'd68: echo_response_byte = target[71:64];
                7'd69: echo_response_byte = target[63:56];
                7'd70: echo_response_byte = target[55:48];
                7'd71: echo_response_byte = target[47:40];
                7'd72: echo_response_byte = target[39:32];
                7'd73: echo_response_byte = target[31:24];
                7'd74: echo_response_byte = target[23:16];
                7'd75: echo_response_byte = target[15:8];
                7'd76: echo_response_byte = target[7:0];
                default: echo_response_byte = 8'h00;
            endcase
        end
    endfunction

    always @(posedge clk) begin
        if (reset) begin
            rx_state <= R_SYNC0;
            payload_count <= 7'd0;
            command <= 8'd0;
            core_start <= 1'b0;
            core_stop <= 1'b0;
            core_start_pending <= 1'b0;
            midstate <= 256'd0;
            tail <= 96'd0;
            target <= 256'd0;
            echo_toggle <= 1'b0;
        end else begin
            core_start <= 1'b0;
            core_stop <= 1'b0;

            if (core_start_pending) begin
                core_start <= 1'b1;
                core_start_pending <= 1'b0;
            end

            if (rx_valid) begin
                case (rx_state)
                    R_SYNC0: rx_state <= (rx_data == "T") ? R_SYNC1 : R_SYNC0;
                    R_SYNC1: rx_state <= (rx_data == "N") ? R_CMD : R_SYNC0;
                    R_CMD: begin
                        command <= rx_data;
                        payload_count <= 7'd0;
                        if (rx_data == "S") begin
                            core_stop <= 1'b1;
                            rx_state <= R_SYNC0;
                        end else if (rx_data == "H") begin
                            midstate <= 256'h5286b3cca7f1116b545db90b7909d56e72ba866ab3fb9b3c772dad8beb392c02;
                            tail <= 96'h4b1e5e4a29ab5f49ffff001d;
                            target <= 256'h0000000000000000000000000000000000000000000000000000ffff00000000;
                            core_start_pending <= 1'b1;
                            rx_state <= R_SYNC0;
                        end else if (rx_data == "J" || rx_data == "E") begin
                            rx_state <= R_PAYLOAD;
                        end else begin
                            rx_state <= R_SYNC0;
                        end
                    end
                    R_PAYLOAD: begin
                        if (payload_count < 7'd32) begin
                            midstate <= {midstate[247:0], rx_data};
                        end else if (payload_count < 7'd44) begin
                            tail <= {tail[87:0], rx_data};
                        end else if (payload_count < 7'd76) begin
                            target <= {target[247:0], rx_data};
                        end

                        if (payload_count == JOB_BYTES - 1) begin
                            if (command == "J") begin
                                core_start_pending <= 1'b1;
                            end else if (command == "E") begin
                                echo_toggle <= ~echo_toggle;
                            end
                            rx_state <= R_SYNC0;
                        end else begin
                            payload_count <= payload_count + 7'd1;
                        end
                    end
                    default: rx_state <= R_SYNC0;
                endcase
            end
        end
    end

    always @(posedge clk) begin
        if (reset) begin
            tx_state <= T_IDLE;
            tx_index <= 6'd0;
            tx_start <= 1'b0;
            tx_data <= 8'hff;
            found_seen <= 1'b0;
            echo_seen_toggle <= 1'b0;
            tx_echo <= 1'b0;
        end else begin
            tx_start <= 1'b0;

            if (!core_found) begin
                found_seen <= 1'b0;
            end

            case (tx_state)
                T_IDLE: begin
                    if (echo_seen_toggle != echo_toggle) begin
                        tx_index <= 7'd0;
                        tx_echo <= 1'b1;
                        tx_state <= T_SEND;
                        echo_seen_toggle <= echo_toggle;
                    end else if (core_found && !found_seen) begin
                        tx_index <= 7'd0;
                        tx_echo <= 1'b0;
                        tx_state <= T_SEND;
                        found_seen <= 1'b1;
                    end
                end

                T_SEND: begin
                    if (!tx_busy) begin
                        tx_data <= tx_echo ? echo_response_byte(tx_index) : found_response_byte(tx_index);
                        tx_start <= 1'b1;
                        tx_state <= T_WAIT;
                    end
                end

                T_WAIT: begin
                    if (tx_busy) begin
                        if ((!tx_echo && tx_index == FOUND_RESP_BYTES - 1) ||
                            (tx_echo && tx_index == ECHO_RESP_BYTES - 1)) begin
                            tx_state <= T_IDLE;
                        end else begin
                            tx_index <= tx_index + 7'd1;
                            tx_state <= T_SEND;
                        end
                    end
                end

                default: tx_state <= T_IDLE;
            endcase
        end
    end

    assign led[0] = ~core_running;
    assign led[1] = ~core_found;
    assign led[2] = ~current_nonce[20];
    assign led[3] = ~current_nonce[21];
    assign led[4] = ~current_nonce[22];
    assign led[5] = ~current_nonce[23];
endmodule
