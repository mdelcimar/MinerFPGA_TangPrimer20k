#!/usr/bin/env python3
"""
Test script for Tang Primer 20K Miner - Genesis Block and historical Bitcoin blocks.
Uses TangMiner protocol (TN sync).
"""

import serial
import time
import hashlib
import struct
import sys

BAUD_RATE = 115200
TIMEOUT = 600  # 10 minutes - real mining with difficulty-1 target (genesis)

# Protocolo TangMiner
SYNC = b'TN'
CMD_JOB = b'J'
CMD_STOP = b'S'
FOUND_SIZE = 37

def sha256_pad(data):
    """Gera o padding SHA-256 padrão para um bloco de dados de 64 bytes."""
    length = len(data) * 8
    pad = b'\x80' + b'\x00' * ((56 - (len(data) + 1) % 64) % 64)
    pad += struct.pack('>Q', length)
    return pad

def calculate_midstate(block_header_first_64):
    """Calcula o midstate (estado SHA-256) dos primeiros 64 bytes do cabeçalho."""
    # Constantes iniciais do SHA-256
    H = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    ]
    
    K = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
    ]

    def rotr(x, n): return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF

    W = list(struct.unpack('>16I', block_header_first_64))
    for i in range(16, 64):
        s0 = rotr(W[i-15], 7) ^ rotr(W[i-15], 18) ^ (W[i-15] >> 3)
        s1 = rotr(W[i-2], 17) ^ rotr(W[i-2], 19) ^ (W[i-2] >> 10)
        W.append((W[i-16] + s0 + W[i-7] + s1) & 0xFFFFFFFF)

    a, b, c, d, e, f, g, h = H
    for i in range(64):
        S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)
        ch = (e & f) ^ ((~e) & g)
        temp1 = (h + S1 + ch + K[i] + W[i]) & 0xFFFFFFFF
        S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)
        maj = (a & b) ^ (a & c) ^ (b & c)
        temp2 = (S0 + maj) & 0xFFFFFFFF

        h = g
        g = f
        f = e
        e = (d + temp1) & 0xFFFFFFFF
        d = c
        c = b
        b = a
        a = (temp1 + temp2) & 0xFFFFFFFF

    # O midstate é retornado em formato Big-Endian
    return struct.pack('>8I', a, b, c, d, e, f, g, h)

def prepare_block_job(header_hex, expected_nonce):
    """Prepara midstate, tail e target a partir do cabeçalho do bloco de 80 bytes."""
    header = bytes.fromhex(header_hex)
    midstate = calculate_midstate(header[:64])
    # O tail consiste em 12 bytes: Merkle (4B finais) + nTime (4B) + nBits (4B)
    tail = header[64:76]
    
    # Target REAL do bloco (bits do cabeçalho)
    bits = int.from_bytes(header[72:76], 'little')
    exponent = bits >> 24
    mantissa = bits & 0x007fffff
    target = mantissa * (2 ** (8 * (exponent - 3)))
    target_bytes = target.to_bytes(32, 'little')  # FPGA espera little-endian
    return midstate, tail, target_bytes, expected_nonce

# Blocos de Teste (Cabeçalho de 80 bytes completo + Nonce esperado)
BLOCKS = {
    "Genesis Block (#0)": prepare_block_job(
        "0100000000000000000000000000000000000000000000000000000000000000000000003ba3edfd7a7b12b27ac72c3e67768f617fc81bc3888a51323a9fb8aa4b1e5e4a29ab5f49ffff001d1dac2b7c",
        0x1dac2b7c
    ),
    "Block #1": prepare_block_job(
        "0100000000000000000000000000000000000000000000000000000000000000000000004a5e1e4ba1235f11155cf4ade2a8c3c7f68f76673e2cc773ed6a6de90a100074baab5f49ffff001d1dac2b7c",
        0x1dac2b7c
    ),
    "Block #170 (Satoshi to Finney)": prepare_block_job(
        "010000005d0124f1c97a5b672728448f8605c4dbd60e722512d7c07e3201000000000000f2e0f46f4eb3074a3f4e2c00a9fa937e25287f3dd4d52140bb6b7e56994a3a60fba25f49ffff001d71321703",
        0x03173271
    )
}

def send_job(ser, midstate, tail, target):
    ser.write(SYNC)
    ser.write(CMD_JOB)
    ser.write(midstate + tail + target)
    ser.flush()

def wait_response(ser, size, timeout=TIMEOUT):
    """Wait for response with progress indication."""
    start = time.time()
    buf = bytearray()
    last_print = start
    while len(buf) < size:
        if ser.in_waiting:
            buf.extend(ser.read(ser.in_waiting))
        elif time.time() - start > timeout:
            return None
        else:
            elapsed = int(time.time() - start)
            if elapsed > 0 and elapsed % 10 == 0 and time.time() - last_print >= 10:
                print(f"  [{elapsed}s] Mining...", end='\r')
                last_print = time.time()
            time.sleep(0.001)
    sys.stdout.write(' ' * 40 + '\r')
    return bytes(buf)

def test_block(ser, name, midstate, tail, target, expected_nonce):
    print(f"\n--- Testing {name} ---")
    send_job(ser, midstate, tail, target)
    
    resp = wait_response(ser, FOUND_SIZE)
    if resp is None:
        print("  Status: TIMEOUT")
        return False
    
    if resp[0:1] != b'F':
        print(f"  Status: ERROR (Unexpected response {resp[0:1]!r})")
        return False
    
    found_nonce = struct.unpack('>I', resp[1:5])[0]
    found_hash = resp[5:37]
    
    print(f"  Expected Nonce: {expected_nonce:#010x}")
    print(f"  Found Nonce:    {found_nonce:#010x}")
    print(f"  Found Hash:     {found_hash.hex()}")
    
    if found_nonce == expected_nonce:
        print("  Result: PASS (Nonce match!)")
        return True
    else:
        print("  Result: WARNING (Nonce returned does not match expected original nonce)")
        return False

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <serial_port> [baud_rate]")
        print(f"Example: {sys.argv[0]} /dev/ttyUSB0 115200")
        sys.exit(1)
    
    port = sys.argv[1]
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else BAUD_RATE
    
    print(f"Connecting to {port} at {baud} baud...")
    
    try:
        ser = serial.Serial(port, baud, timeout=TIMEOUT)
        time.sleep(0.5)
        ser.reset_input_buffer()
        
        for name, (midstate, tail, target, expected_nonce) in BLOCKS.items():
            # Send STOP before each test to reset FPGA
            ser.write(SYNC + CMD_STOP)
            ser.flush()
            time.sleep(1)
            ser.reset_input_buffer()
            test_block(ser, name, midstate, tail, target, expected_nonce)
            time.sleep(0.5)
        
        # Stop FPGA
        ser.write(SYNC + CMD_STOP)
        ser.flush()
        ser.close()
        print("\nTests completed!")
        
    except serial.SerialException as e:
        print(f"Serial error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)

if __name__ == '__main__':
    main()
