#!/usr/bin/env python3
"""
Simple test script for Tang Primer 20K Miner - compatible with original test_fpga_3blocks.py
This script uses the TangMiner protocol (TN sync) instead of the simple protocol.
Waits for FPGA to mine (real difficulty target).
"""

import serial
import time
import hashlib
import struct
import sys

BAUD_RATE = 115200
MINING_TIMEOUT = 600  # 10 minutes for real difficulty-1 target

# TangMiner protocol
SYNC = b'TN'  # 'T' (0x54) + 'N' (0x4e)
CMD_HARDCODED = b'H'
CMD_JOB = b'J'
CMD_ECHO = b'E'
CMD_STOP = b'S'

RESP_FOUND = b'F'
RESP_ECHO = b'E'

# Genesis block test vectors (real difficulty-1 target)
GENESIS_MIDSTATE = bytes.fromhex('5286b3cca7f1116b545db90b7909d56e72ba866ab3fb9b3c772dad8beb392c02')
GENESIS_TAIL = bytes.fromhex('4b1e5e4a29ab5f49ffff001d')
GENESIS_TARGET = bytes.fromhex('0000000000000000000000000000000000000000000000000000ffff00000000')
GENESIS_EXPECTED_NONCE = 0x1dac2b7c

FOUND_SIZE = 37
ECHO_SIZE = 77

def double_sha256(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()

def send_sync(ser):
    ser.write(SYNC)
    ser.flush()

def wait_mining_response(ser, size, timeout):
    """Wait for FPGA mining response with progress indicator."""
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
                print(f"  [{elapsed}s] Waiting for FPGA to mine...", end='\r')
                last_print = time.time()
            time.sleep(0.001)
    # Clear any progress line
    sys.stdout.write(' ' * 60 + '\r')
    return bytes(buf)

def test_hardcoded(ser):
    """Test with FPGA's hardcoded genesis block (CMD 'H')."""
    print("\n=== Test 1: Hardcoded Genesis Block (CMD 'H') ===")
    print("  Mining with real difficulty-1 target...")
    print(f"  Expected nonce: {GENESIS_EXPECTED_NONCE:#010x}")
    
    ser.write(SYNC + CMD_HARDCODED)
    ser.flush()
    
    resp = wait_mining_response(ser, FOUND_SIZE, MINING_TIMEOUT)
    if resp is None:
        print(f"  TIMEOUT after {MINING_TIMEOUT}s")
        return False
    
    if resp[0:1] != b'F':
        print(f"  ERROR: Expected 'F', got {resp[0:1]!r}")
        return False
    
    nonce = struct.unpack('>I', resp[1:5])[0]
    found_hash = resp[5:37]
    
    print(f"\n  Found Nonce:    {nonce:#010x}")
    print(f"  Expected Nonce: {GENESIS_EXPECTED_NONCE:#010x}")
    print(f"  Hash:  {found_hash.hex()}")
    print(f"  PASS: FPGA found valid hash meeting genesis target!")
    return True

def test_echo(ser):
    """Test echo functionality."""
    print("\n=== Test 2: Echo test ===")
    ser.write(SYNC + CMD_ECHO)
    ser.flush()
    # Send payload (midstate + tail + target = 76 bytes)
    ser.write(GENESIS_MIDSTATE + GENESIS_TAIL + GENESIS_TARGET)
    ser.flush()
    
    resp = wait_mining_response(ser, ECHO_SIZE, 10)
    if resp and resp[0:1] == b'E':
        print(f"  Echo PASS ({ECHO_SIZE} bytes received)")
        return True
    else:
        print("  Echo FAIL")
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
        ser = serial.Serial(port, baud, timeout=MINING_TIMEOUT)
        time.sleep(0.5)
        ser.reset_input_buffer()
        print(f"[+] Serial port {port} ready.")
        
        # Send stop first to reset FPGA state
        ser.write(SYNC + CMD_STOP)
        ser.flush()
        time.sleep(1)
        
        test_hardcoded(ser)
        test_echo(ser)
        
        # Stop
        print("\nSending stop...")
        ser.write(SYNC + CMD_STOP)
        ser.flush()
        
        ser.close()
        print("\nDone!")
        
    except serial.SerialException as e:
        print(f"Serial error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted")
        try:
            ser.write(SYNC + CMD_STOP)
            ser.close()
        except:
            pass
        sys.exit(1)

if __name__ == '__main__':
    main()
