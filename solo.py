#!/usr/bin/env python3
"""
Solo FPGA Miner via Bitcoin RPC (TangMiner / Tang Primer 20K)
Programa Delcimar Martins (d3lc1m4r@gmail.com)
Test miner with fpga bc1qkvkg4fk500eumvq5jjjgxjfxswkrhx85mvqxum
donate: 
"""

import serial
import time
import hashlib
import struct
import sys
import json
import requests

# ==============================================================================
# CONFIGURAÇÕES DO MINERADOR
# ==============================================================================
RPC_URL = "http://192.168.1.2:8332"
RPC_USER = "user"
RPC_PASS = "pass"

# Seu endereço para receber as moedas
WALLET_ADDRESS = "bc1qkvkg4fk500eumvq5jjjgxjfxswkrhx85mvqxum" 

# Configuração da Porta Serial da FPGA
SERIAL_PORT = "/dev/ttyUSB1"
BAUD_RATE = 115200

# Protocolo TangMiner
SYNC = b'TN'
CMD_JOB = b'J'
CMD_STOP = b'S'
FOUND_SIZE = 37

# Intervalo de verificação de novos blocos (segundos)
POLL_INTERVAL = 1.0 

# ==============================================================================
# FUNÇÕES CRIPTOGRÁFICAS E UTILITÁRIOS
# ==============================================================================
def sha256d(data: bytes) -> bytes:
    """Duplo SHA-256 retornando bytes."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()

def encode_varint(i: int) -> bytes:
    """Serializa um inteiro no formato CompactSize / VarInt do Bitcoin."""
    if i < 0xfd:
        return struct.pack("<B", i)
    elif i <= 0xffff:
        return b"\xfd" + struct.pack("<H", i)
    elif i <= 0xffffffff:
        return b"\xfe" + struct.pack("<I", i)
    else:
        return b"\xff" + struct.pack("<Q", i)

def calculate_midstate(data_64: bytes) -> bytes:
    """Calcula o midstate SHA-256 para exatos 64 bytes de entrada."""
    if len(data_64) != 64:
        raise ValueError(f"calculate_midstate exige 64 bytes, recebido: {len(data_64)}")

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

    W = list(struct.unpack('>16I', data_64))
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

        h = g; g = f; f = e; e = (d + temp1) & 0xFFFFFFFF
        d = c; c = b; b = a; a = (temp1 + temp2) & 0xFFFFFFFF

    return struct.pack('>8I', a, b, c, d, e, f, g, h)

def bits_to_target(bits_hex: str) -> bytes:
    """Converte a representação 'bits' do cabeçalho no Target de 256 bits (little-endian para FPGA)."""
    bits = int(bits_hex, 16)
    exponent = bits >> 24
    mantissa = bits & 0x007fffff
    if bits & 0x00800000:
        mantissa *= -1
    target = mantissa * (2 ** (8 * (exponent - 3)))
    # FPGA compara reverse_words(hash) <= target, target deve vir em little-endian (word order)
    return target.to_bytes(32, byteorder='little')

def calculate_merkle_root(tx_hashes_bytes: list) -> bytes:
    """Calcula a Merkle Root recebendo uma lista de hashes em bytes (little-endian)."""
    if not tx_hashes_bytes:
        return b'\x00' * 32
    
    hashes = list(tx_hashes_bytes)
    while len(hashes) > 1:
        if len(hashes) % 2 != 0:
            hashes.append(hashes[-1])
        new_hashes = []
        for i in range(0, len(hashes), 2):
            new_hashes.append(sha256d(hashes[i] + hashes[i+1]))
        hashes = new_hashes
    return hashes[0] # Retorna a raiz em bytes (little-endian)

# ==============================================================================
# CONEXÃO RPC COM O NÓ BITCOIN
# ==============================================================================
class BitcoinRPC:
    def __init__(self, url, user, password):
        self.url = url
        self.auth = (user, password)
        self.id = 0

    def call(self, method, params=[]):
        self.id += 1
        headers = {'content-type': 'application/json'}
        payload = {
            "method": method,
            "params": params,
            "id": self.id,
            "jsonrpc": "1.0"
        }
        try:
            response = requests.post(self.url, auth=self.auth, data=json.dumps(payload), headers=headers, timeout=15)
            if response.status_code == 200:
                return response.json()['result']
            else:
                print(f"[RPC Error] Code {response.status_code}: {response.text}")
                return None
        except Exception as e:
            print(f"[RPC Exception] {e}")
            return None

    def get_block_template(self):
        return self.call("getblocktemplate", [{"rules": ["segwit"]}])

    def get_best_block_hash(self):
        return self.call("getbestblockhash")

    def submit_block(self, hex_data):
        return self.call("submitblock", [hex_data])

# ==============================================================================
# CONSTRUÇÃO DA TRANSACÃO COINBASE
# ==============================================================================
def create_coinbase_tx(coinbase_value: int, height: int, wallet_address: str, extra_nonce: int = 0):
    """Cria a transação Coinbase e retorna (tx_raw_bytes, tx_hash_bytes)."""
    tx = struct.pack("<I", 1) # Version
    tx += b"\x01" # Input Count
    tx += b"\x00" * 32 # Previous Output Hash
    tx += b"\xff\xff\xff\xff" # Previous Output Index
    
    # ScriptSig com Bip34 height + extraNonce
    height_bytes = struct.pack("<I", height).rstrip(b'\x00')
    extra_nonce_bytes = struct.pack("<I", extra_nonce).rstrip(b'\x00')
    if extra_nonce_bytes == b'':
        extra_nonce_bytes = b'\x00'
    script_bytes = bytes([len(height_bytes) + len(extra_nonce_bytes)]) + height_bytes + extra_nonce_bytes + b"TextMiningFPGA"
    tx += bytes([len(script_bytes)]) + script_bytes
    tx += struct.pack("<I", 0xffffffff) # Sequence
    
    tx += b"\x01" # Output Count
    tx += struct.pack("<Q", coinbase_value) # Value
    
    # Output P2WPKH para endereço bech32 (bc1...)
    if wallet_address.startswith('bc1'):
        # Script P2WPKH: OP_0 <20-byte pubkeyhash>
        # Precisamos decodificar o endereço bech32
        import bech32
        hrp, data = bech32.bech32_decode(wallet_address)
        witver, prog = data[0], bytes(data[1:])
        script_pubkey = bytes([0x00, len(prog)]) + prog
    else:
        # P2PKH padrão (base58)
        import base58
        decoded = base58.b58decode_check(wallet_address)
        script_pubkey = b'\x76\xa9\x14' + decoded[1:] + b'\x88\xac'
    
    tx += bytes([len(script_pubkey)]) + script_pubkey
    tx += struct.pack("<I", 0) # Locktime
    
    tx_hash_bytes = sha256d(tx) # Retorna hash em bytes (little-endian)
    return tx, tx_hash_bytes

# ==============================================================================
# LOOP PRINCIPAL
# ==============================================================================
def main():
    rpc = BitcoinRPC(RPC_URL, RPC_USER, RPC_PASS)
    
    best_hash = rpc.get_best_block_hash()
    if not best_hash:
        print("[ERRO] Não foi possível conectar ao nó RPC.")
        sys.exit(1)
        
    print(f"[+] Conectado ao Nó RPC. Bloco Atual no Topo da Rede: {best_hash}")
    
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=15)
        time.sleep(0.5)
        ser.reset_input_buffer()
        print(f"[+] Porta Serial {SERIAL_PORT} pronta.")
    except Exception as e:
        print(f"[ERRO] Falha ao abrir porta serial: {e}")
        sys.exit(1)

    current_parent_hash = None
    header_prefix_bytes = None
    cur_time = None
    bits_bytes = None
    full_txs_raw_bytes = []
    extra_nonce = 0

    while True:
        latest_hash = rpc.get_best_block_hash()
        
        # Mudança de Bloco na Rede
        if latest_hash != current_parent_hash:
            print(f"\n[!] Atualização na rede detectada!")
            if current_parent_hash is not None:
                print(f"    Hash do Bloco Anterior: {current_parent_hash}")
                print("    Parando trabalho na FPGA (Comando STOP)...")
                ser.write(SYNC + CMD_STOP)
                ser.flush()
            
            current_parent_hash = latest_hash
            extra_nonce = 0  # Reset extraNonce on new block
            template = rpc.get_block_template()
            if not template:
                print("[ERRO] Falha ao obter getblocktemplate.")
                time.sleep(2)
                continue

            # 1. Extração de Parâmetros
            version = template['version']
            prev_block_hash_str = template['previousblockhash']
            prev_block_bytes = bytes.fromhex(prev_block_hash_str)[::-1]
            cur_time = template['curtime']
            bits_bytes = bytes.fromhex(template['bits'])[::-1]
            height = template['height']
            coinbase_val = template['coinbasevalue']
            
# 2. Coinbase & Merkle Root
            coinbase_tx_bytes, coinbase_hash_bytes = create_coinbase_tx(coinbase_val, height, WALLET_ADDRESS, extra_nonce)
            
            # Salva lista de transações em formato bruto (raw bytes) para submissão posterior
            mempool_txs_raw = [bytes.fromhex(tx['data']) for tx in template['transactions']]
            full_txs_raw_bytes = [coinbase_tx_bytes] + mempool_txs_raw
            
            # Converte transações da Mempool de HEX para Bytes Little-Endian (para Merkle Tree)
            mempool_hashes = [bytes.fromhex(tx['hash'])[::-1] for tx in template['transactions']]
            tx_hashes = [coinbase_hash_bytes] + mempool_hashes
            
            merkle_root_bytes = calculate_merkle_root(tx_hashes)

            # 3. Montagem de Dados SHA-256 (64 Bytes Exatos para Midstate)
            header_prefix_bytes = struct.pack("<I", version) + prev_block_bytes + merkle_root_bytes
            header_first_64 = header_prefix_bytes[:64]
            
            midstate = calculate_midstate(header_first_64)
            tail = merkle_root_bytes[28:] + struct.pack("<I", cur_time) + bits_bytes
            target = bits_to_target(template['bits'])
            
            print(f"[+] Minerando Bloco Novo: #{height} (extraNonce={extra_nonce})")
            print(f"    Hash do Bloco Anterior: {prev_block_hash_str}")
            print(f"    Midstate: {midstate.hex()[:16]}...")
            print(f"    Tail:     {tail.hex()}")

            # Envia o Job para a FPGA
            ser.write(SYNC + CMD_JOB + midstate + tail + target)
            ser.flush()

        # Resposta da FPGA
        if ser.in_waiting >= FOUND_SIZE:
            resp = ser.read(FOUND_SIZE)
            if resp[0:1] == b'F':
                found_nonce = struct.unpack('>I', resp[1:5])[0]
                found_hash = resp[5:37]
                
                print("\n==================================================")
                print("   !!! NONCE ENCONTRADO PELA FPGA !!!   ")
                print(f"   Nonce: {found_nonce:#010x}")
                print(f"   Hash:  {found_hash.hex()}")
                print("==================================================")

                # Cabeçalho de 80 bytes (com Nonce empacotado em Little-Endian para a rede)
                header_80b = header_prefix_bytes + struct.pack("<I", cur_time) + bits_bytes + struct.pack("<I", found_nonce)
                
                # Construção do Bloco Completo Serializado: Header + VarInt(Count) + Raw Txs
                block_full_bytes = header_80b + encode_varint(len(full_txs_raw_bytes))
                for tx_raw in full_txs_raw_bytes:
                    block_full_bytes += tx_raw

                result = rpc.submit_block(block_full_bytes.hex())
                
                # O Bitcoin RPC retorna None/null quando o bloco é aceito com sucesso
                if result is None:
                    print("[+] Submissão do Bloco: BLOCO ACEITO COM SUCESSO PELA REDE!")
                    # Reset extraNonce on success
                    extra_nonce = 0
                else:
                    print(f"[!] Bloco REJEITADO: {result}")
                    print("[!] Incrementando extraNonce e recalculando Merkle Root...")
                    extra_nonce += 1
                    # Força recálculo do trabalho na próxima iteração
                    ser.write(SYNC + CMD_STOP)
                    ser.flush()
                    time.sleep(0.1)
                    # Recalcular coinbase + merkle + midstate + tail
                    coinbase_tx_bytes, coinbase_hash_bytes = create_coinbase_tx(coinbase_val, height, WALLET_ADDRESS, extra_nonce)
                    mempool_hashes = [bytes.fromhex(tx['hash'])[::-1] for tx in template['transactions']]
                    tx_hashes = [coinbase_hash_bytes] + mempool_hashes
                    merkle_root_bytes = calculate_merkle_root(tx_hashes)
                    header_prefix_bytes = struct.pack("<I", version) + prev_block_bytes + merkle_root_bytes
                    header_first_64 = header_prefix_bytes[:64]
                    midstate = calculate_midstate(header_first_64)
                    tail = merkle_root_bytes[28:] + struct.pack("<I", cur_time) + bits_bytes
                    target = bits_to_target(template['bits'])
                    print(f"[+] Novo trabalho: extraNonce={extra_nonce}")
                    ser.write(SYNC + CMD_JOB + midstate + tail + target)
                    ser.flush()
                    
                time.sleep(2)

        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Encerrando minerador.")
        sys.exit(0)
