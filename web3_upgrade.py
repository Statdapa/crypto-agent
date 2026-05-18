"""
web3_upgrade.py
===============
Modul Web3 yang dirancang untuk di-MERGE ke btc_agent_okx.py yang sudah ada.

CARA PAKAI:
  1. pip install -r requirements_web3.txt
  2. Tambahkan ke .env kamu (lihat bagian CONFIG di bawah)
  3. Copy-paste seluruh file ini ke bawah bagian OKX DEX di btc_agent_okx.py
     (setelah baris `handle_telegram_updates.offset = 0` ~line 918)
  4. Tambahkan command handler baru ke handle_telegram_updates() — lihat PATCH section di bawah

YANG DITAMBAHKAN:
  - /wallet 0x...     → saldo ETH wallet
  - /txn 0x...        → 5 transaksi terakhir
  - /tokens 0x...     → daftar token ERC-20
  - /whale 0x...      → deteksi whale activity
  - /eth              → status jaringan Ethereum saat ini
  - Free chat: agent otomatis deteksi pertanyaan tentang wallet
"""

import os
import requests
import hmac
import hashlib
import base64
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG — Tambahkan ke .env kamu
# ─────────────────────────────────────────────
#
#   ETHERSCAN_API_KEY=your_key_here
#   INFURA_PROJECT_ID=your_infura_id       ← opsional, untuk data real-time
#
# Daftar gratis:
#   Etherscan : https://etherscan.io/myapikey
#   Infura    : https://infura.io (Ethereum Mainnet)
# ─────────────────────────────────────────────

ETHERSCAN_KEY  = os.getenv("ETHERSCAN_API_KEY", "")
INFURA_ID      = os.getenv("INFURA_PROJECT_ID", "")
ETHERSCAN_BASE = "https://api.etherscan.io/v2/api"

WEI_TO_ETH          = 1e-18
WHALE_THRESHOLD_ETH  = 100.0    # Ubah sesuai preferensi kamu


# ═══════════════════════════════════════════════
# SECTION A: ETHERSCAN API (Data Historis On-chain)
# ═══════════════════════════════════════════════

def _etherscan(params: dict) -> dict:
    """
    Helper internal: kirim request ke Etherscan API.
    Mengembalikan dict JSON atau raise ValueError jika error.
    """
    if not ETHERSCAN_KEY:
        raise ValueError("ETHERSCAN_API_KEY belum diset di .env")

    params["apikey"] = ETHERSCAN_KEY
    params["chainid"] = 1  
    try:
        r = requests.get(ETHERSCAN_BASE, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        raise ValueError(f"Request gagal: {e}")

    # Etherscan pakai status "0" untuk error (bukan HTTP 4xx)
    if data.get("status") == "0" and data.get("message") not in ("No transactions found", "No records found"):
        raise ValueError(f"Etherscan: {data.get('result', 'Unknown error')}")

    return data


def web3_get_eth_balance(wallet: str) -> str:
    """
    Ambil saldo ETH dari wallet address via Etherscan.
    Dipanggil oleh command /wallet dan free-chat parser.

    Return: string siap kirim ke Telegram (Markdown).
    """
    wallet = wallet.strip()
    if not wallet.startswith("0x") or len(wallet) < 40:
        return "❌ Format wallet tidak valid. Contoh: `0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`"

    try:
        data    = _etherscan({"module": "account", "action": "balance",
                               "address": wallet, "tag": "latest"})
        wei     = int(data["result"])
        eth_val = wei * WEI_TO_ETH

        # Ambil harga ETH dari CoinGecko (sudah ada di agent kamu)
        usd_val = ""
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "ethereum", "vs_currencies": "usd"}, timeout=5
            )
            eth_price = r.json()["ethereum"]["usd"]
            usd_val   = f"≈ *${eth_val * eth_price:,.2f} USD*"
        except Exception:
            pass

        short = f"{wallet[:8]}...{wallet[-4:]}"
        return (
            f"💎 *Saldo Wallet ETH*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👛 `{short}`\n\n"
            f"⟠ ETH  : *{eth_val:,.6f} ETH*\n"
            f"💵 USD  : {usd_val if usd_val else '_harga tidak tersedia_'}\n"
            f"🔢 Wei  : `{wei:,}`\n\n"
            f"[🔍 Lihat di Etherscan](https://etherscan.io/address/{wallet})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ _Bukan financial advice_"
        )
    except ValueError as e:
        return f"❌ Error: {e}"


def web3_get_transactions(wallet: str, limit: int = 5) -> str:
    """
    Tampilkan riwayat transaksi terakhir sebuah wallet.
    Dipanggil oleh command /txn.

    Return: string Markdown untuk Telegram.
    """
    wallet = wallet.strip()
    if not wallet.startswith("0x") or len(wallet) < 40:
        return "❌ Format wallet tidak valid."

    try:
        data = _etherscan({
            "module": "account", "action": "txlist",
            "address": wallet,
            "startblock": 0, "endblock": 99999999,
            "page": 1, "offset": limit,
            "sort": "desc",
        })
        txs = data.get("result", [])
    except ValueError as e:
        return f"❌ Error: {e}"

    if not txs:
        return f"📭 Tidak ada transaksi ditemukan untuk `{wallet[:10]}...`"

    short = f"{wallet[:8]}...{wallet[-4:]}"
    lines = [
        f"📋 *Transaksi Terakhir*\n"
        f"👛 `{short}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    ]

    for i, tx in enumerate(txs[:limit], 1):
        ts       = datetime.utcfromtimestamp(int(tx["timeStamp"])).strftime("%d %b %Y %H:%M")
        eth_val  = int(tx["value"]) * WEI_TO_ETH
        is_out   = tx["from"].lower() == wallet.lower()
        arrow    = "📤 OUT" if is_out else "📥 IN "
        status   = "✅" if tx["isError"] == "0" else "❌"
        counterparty = tx["to"] if is_out else tx["from"]
        cp_short = f"{counterparty[:8]}...{counterparty[-4:]}" if counterparty else "Contract"

        lines.append(
            f"*[{i}]* {status} {arrow} | `{ts}`\n"
            f"     ⟠ {eth_val:.6f} ETH\n"
            f"     {'To' if is_out else 'From'}: `{cp_short}`\n"
            f"     Hash: `{tx['hash'][:14]}...`\n"
        )

    lines.append(f"[🔍 Lihat semua di Etherscan](https://etherscan.io/address/{wallet})")
    return "\n".join(lines)


def web3_get_tokens(wallet: str, limit: int = 10) -> str:
    """
    Tampilkan token ERC-20 yang dimiliki wallet.
    Menggunakan riwayat transfer untuk menemukan token unik.
    Dipanggil oleh command /tokens.
    """
    wallet = wallet.strip()
    if not wallet.startswith("0x") or len(wallet) < 40:
        return "❌ Format wallet tidak valid."

    try:
        data = _etherscan({
            "module": "account", "action": "tokentx",
            "address": wallet,
            "startblock": 0, "endblock": 99999999,
            "page": 1, "offset": 100,   # ambil 100 transfer, cari yang unik
            "sort": "desc",
        })
        txs = data.get("result", [])
    except ValueError as e:
        return f"❌ Error: {e}"

    if not txs:
        return f"📭 Tidak ada token ERC-20 ditemukan untuk `{wallet[:10]}...`"

    # Deduplikasi berdasarkan contractAddress
    seen, tokens = set(), []
    for tx in txs:
        addr = tx.get("contractAddress", "")
        if addr and addr not in seen:
            seen.add(addr)
            tokens.append({
                "name"    : tx.get("tokenName", "Unknown"),
                "symbol"  : tx.get("tokenSymbol", "?"),
                "contract": addr,
            })

    short = f"{wallet[:8]}...{wallet[-4:]}"
    lines = [
        f"🪙 *Token Holdings*\n"
        f"👛 `{short}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    ]

    for i, t in enumerate(tokens[:limit], 1):
        lines.append(
            f"*{i}.* {t['name']} (`{t['symbol']}`)\n"
            f"     Contract: `{t['contract'][:14]}...`\n"
        )

    lines.append(f"\n📊 Total {len(tokens)} token unik ditemukan")
    lines.append(f"[🔍 Lihat di Etherscan](https://etherscan.io/address/{wallet}#tokentxns)")
    return "\n".join(lines)


def web3_whale_watch(wallet: str, threshold: float = WHALE_THRESHOLD_ETH) -> str:
    """
    Analisis whale activity dari sebuah wallet.
    Cek apakah ada transaksi besar (> threshold ETH) baru-baru ini.
    Dipanggil oleh command /whale.
    """
    wallet = wallet.strip()
    if not wallet.startswith("0x") or len(wallet) < 40:
        return "❌ Format wallet tidak valid."

    try:
        data = _etherscan({
            "module": "account", "action": "txlist",
            "address": wallet,
            "startblock": 0, "endblock": 99999999,
            "page": 1, "offset": 50,
            "sort": "desc",
        })
        txs = data.get("result", [])
    except ValueError as e:
        return f"❌ Error: {e}"

    if not txs:
        return f"📭 Tidak ada data transaksi untuk analisis."

    # Hitung statistik
    all_eth    = [int(tx["value"]) * WEI_TO_ETH for tx in txs]
    whale_txs  = [
        {
            "eth" : int(tx["value"]) * WEI_TO_ETH,
            "ts"  : datetime.utcfromtimestamp(int(tx["timeStamp"])).strftime("%d %b %Y %H:%M"),
            "hash": tx["hash"],
            "dir" : "OUT" if tx["from"].lower() == wallet.lower() else "IN",
            "ok"  : tx["isError"] == "0",
        }
        for tx in txs
        if int(tx["value"]) * WEI_TO_ETH >= threshold
    ]

    total_vol = sum(all_eth)
    avg_tx    = total_vol / len(txs) if txs else 0
    max_tx    = max(all_eth) if all_eth else 0

    whale_label = "🐋 *WHALE DETECTED*" if whale_txs else "🐟 Tidak ada whale activity"
    short = f"{wallet[:8]}...{wallet[-4:]}"

    lines = [
        f"🔭 *Whale Watch*\n"
        f"👛 `{short}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"\n📊 *Statistik 50 Tx Terakhir*\n"
        f"Total volume : `{total_vol:,.4f} ETH`\n"
        f"Rata-rata tx : `{avg_tx:,.4f} ETH`\n"
        f"Tx terbesar  : `{max_tx:,.4f} ETH`\n"
        f"Threshold    : `{threshold} ETH`\n"
        f"Whale moves  : `{len(whale_txs)} transaksi`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{whale_label}\n"
    ]

    if whale_txs:
        lines.append("")
        for wt in whale_txs[:5]:
            status = "✅" if wt["ok"] else "❌"
            arrow  = "📤" if wt["dir"] == "OUT" else "📥"
            lines.append(
                f"{status} {arrow} *{wt['eth']:,.2f} ETH* — {wt['ts']}\n"
                f"     `{wt['hash'][:18]}...`\n"
            )
    else:
        lines.append(f"_Tidak ada transaksi > {threshold} ETH ditemukan_\n")

    lines.append(f"[🔍 Lihat di Etherscan](https://etherscan.io/address/{wallet})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# SECTION B: WEB3.PY — Real-time Ethereum Node
# ═══════════════════════════════════════════════
# Opsional: hanya aktif jika INFURA_PROJECT_ID diset.
# Lebih real-time dari Etherscan, tapi butuh koneksi node.

def web3_eth_network_status() -> str:
    """
    Tampilkan status jaringan Ethereum saat ini.
    Dipanggil oleh command /eth.

    Jika Web3.py tersedia dan Infura diset → data live dari node.
    Jika tidak → fallback ke Etherscan API untuk info dasar.
    """
    # Coba Web3.py dulu (real-time)
    if INFURA_ID:
        try:
            from web3 import Web3
            w3 = Web3(Web3.HTTPProvider(f"https://mainnet.infura.io/v3/{INFURA_ID}"))
            if w3.is_connected():
                block     = w3.eth.get_block("latest")
                gas_gwei  = round(float(w3.from_wei(w3.eth.gas_price, "gwei")), 2)
                base_fee  = round(float(w3.from_wei(block.get("baseFeePerGas", 0), "gwei")), 2)
                return (
                    f"⟠ *Status Jaringan Ethereum*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔗 Status      : ✅ Terhubung (Infura)\n"
                    f"📦 Block       : `#{block['number']:,}`\n"
                    f"⛽ Gas Price   : `{gas_gwei} Gwei`\n"
                    f"📊 Base Fee   : `{base_fee} Gwei`\n"
                    f"📝 Tx di blok  : `{len(block['transactions'])}`\n"
                    f"🕐 Timestamp   : `{datetime.utcfromtimestamp(block['timestamp']).strftime('%d %b %Y %H:%M')} UTC`\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"_Data real-time via Infura_"
                )
        except ImportError:
            pass   # web3 tidak terinstall, pakai fallback
        except Exception as e:
            pass   # koneksi gagal, pakai fallback

    # Fallback: ETH gas price via Etherscan
    try:
        data    = _etherscan({"module": "gastracker", "action": "gasoracle"})
        result  = data.get("result", {})
        return (
            f"⟠ *Status Jaringan Ethereum*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⛽ Gas (Low)    : `{result.get('SafeGasPrice', 'N/A')} Gwei`\n"
            f"⛽ Gas (Normal) : `{result.get('ProposeGasPrice', 'N/A')} Gwei`\n"
            f"⛽ Gas (Fast)   : `{result.get('FastGasPrice', 'N/A')} Gwei`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_Data via Etherscan (set INFURA_PROJECT_ID untuk data real-time)_"
        )
    except ValueError as e:
        return f"❌ Gagal ambil data: {e}"


# ═══════════════════════════════════════════════
# SECTION C: FREE CHAT INTEGRATION
# ═══════════════════════════════════════════════
# Tambahkan ke blok `else:` (free chat) di handle_telegram_updates()

import re as _re

_WALLET_PATTERN = _re.compile(r"0x[a-fA-F0-9]{40,}")

def web3_detect_and_answer(text: str) -> str | None:
    """
    Deteksi apakah pertanyaan bebas mengandung wallet address atau
    keyword Web3 dan langsung jawab tanpa perlu LLM call.

    Kembalikan string jawaban jika terdeteksi, None jika tidak relevan.

    Cara dintegrasikan ke free chat:
        # Di handle_telegram_updates(), blok else (free chat):
        web3_reply = web3_detect_and_answer(text)
        if web3_reply:
            send_telegram(web3_reply)
            continue   # skip ke update berikutnya
        # ... lanjut ke LLM seperti biasa
    """
    text_lower = text.lower()

    # Ekstrak wallet address jika ada di pesan
    wallets = _WALLET_PATTERN.findall(text)
    if wallets:
        wallet = wallets[0]
        # Tentukan jenis pertanyaan
        if any(kw in text_lower for kw in ["saldo", "balance", "punya berapa", "miliki"]):
            return web3_get_eth_balance(wallet)
        if any(kw in text_lower for kw in ["transaksi", "transaction", "tx", "kirim", "terakhir", "history"]):
            return web3_get_transactions(wallet)
        if any(kw in text_lower for kw in ["token", "erc", "holding", "coins", "altcoin"]):
            return web3_get_tokens(wallet)
        if any(kw in text_lower for kw in ["whale", "paus", "besar", "jumbo"]):
            return web3_whale_watch(wallet)
        # Default: jika ada wallet tapi keyword tidak spesifik → tampilkan saldo
        return web3_get_eth_balance(wallet)

    # Keyword Web3 tanpa wallet address
    if any(kw in text_lower for kw in ["gas ethereum", "gas eth", "gas price", "biaya eth", "status ethereum"]):
        return web3_eth_network_status()

    return None   # Tidak relevan, biarkan LLM handle


# ═══════════════════════════════════════════════
# SECTION D: COMMAND HANDLERS (PATCH untuk handle_telegram_updates)
# ═══════════════════════════════════════════════
#
# Tambahkan blok elif berikut ke dalam handle_telegram_updates(),
# SEBELUM blok `else:` (free chat), persis setelah handler /mingguan.
#
# ─────────────────────────────────────────────
# PATCH — copy blok elif ini ke handle_telegram_updates():
# ─────────────────────────────────────────────
#
#           elif text_lower.startswith("/wallet"):
#               parts = text_lower.split()
#               if len(parts) < 2:
#                   send_telegram("❓ Contoh: `/wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
#               else:
#                   send_telegram("⏳ Mengambil data wallet...")
#                   send_telegram(web3_get_eth_balance(parts[1]))
#
#           elif text_lower.startswith("/txn"):
#               parts = text.split()
#               if len(parts) < 2:
#                   send_telegram("❓ Contoh: `/txn 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
#               else:
#                   send_telegram("⏳ Mengambil transaksi...")
#                   limit = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 5
#                   send_telegram(web3_get_transactions(parts[1], limit=limit))
#
#           elif text_lower.startswith("/tokens"):
#               parts = text.split()
#               if len(parts) < 2:
#                   send_telegram("❓ Contoh: `/tokens 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
#               else:
#                   send_telegram("⏳ Mengambil token holdings...")
#                   send_telegram(web3_get_tokens(parts[1]))
#
#           elif text_lower.startswith("/whale"):
#               parts = text.split()
#               if len(parts) < 2:
#                   send_telegram("❓ Contoh: `/whale 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
#               else:
#                   send_telegram("🔭 Menganalisis whale activity...")
#                   threshold = float(parts[2]) if len(parts) > 2 else WHALE_THRESHOLD_ETH
#                   send_telegram(web3_whale_watch(parts[1], threshold=threshold))
#
#           elif text_lower == "/eth":
#               send_telegram("⏳ Mengambil status Ethereum...")
#               send_telegram(web3_eth_network_status())
#
# ─────────────────────────────────────────────
# PATCH free chat — tambahkan ke awal blok `else:` (sebelum baris `ctx = ...`):
# ─────────────────────────────────────────────
#
#               web3_reply = web3_detect_and_answer(text)
#               if web3_reply:
#                   send_telegram(web3_reply)
#                   continue
#
# ─────────────────────────────────────────────
# PATCH HELP_MSG — tambahkan ke HELP_MSG yang sudah ada:
# ─────────────────────────────────────────────
#
# ⟠ *On-chain Ethereum*
# /wallet 0x... — Cek saldo ETH wallet
# /txn 0x...    — 5 transaksi terakhir
# /tokens 0x... — Token ERC-20 di wallet
# /whale 0x...  — Deteksi whale activity
# /eth          — Status gas & jaringan Ethereum
#
# ═══════════════════════════════════════════════
