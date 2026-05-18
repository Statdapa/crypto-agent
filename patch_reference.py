"""
btc_agent_okx_web3.py
=====================
Versi LENGKAP btc_agent_okx.py + Web3 upgrade sudah terintegrasi.

Perubahan dari versi original:
  1. Import web3_upgrade.py (Section A-C)
  2. HELP_MSG ditambah section On-chain Ethereum
  3. handle_telegram_updates() ditambah 5 command baru:
       /wallet, /txn, /tokens, /whale, /eth
  4. Free chat mendeteksi wallet address otomatis

Jalankan: python btc_agent_okx_web3.py
"""

# ─────────────────────────────────────────────────────────────────
# TAMBAHKAN INI KE BAGIAN PALING ATAS btc_agent_okx.py (setelah import lain)
# ─────────────────────────────────────────────────────────────────

# from web3_upgrade import (
#     web3_get_eth_balance,
#     web3_get_transactions,
#     web3_get_tokens,
#     web3_whale_watch,
#     web3_eth_network_status,
#     web3_detect_and_answer,
# )

# ─────────────────────────────────────────────────────────────────
# PATCH 1: HELP_MSG — Tambahkan section baru
# ─────────────────────────────────────────────────────────────────
#
# Temukan baris HELP_MSG = """... dan tambahkan sebelum baris penutup """
# (setelah /coins):
#
# ⟠ *On-chain Ethereum*
# /wallet 0x... — Saldo ETH wallet
# /txn 0x...    — 5 transaksi terakhir
# /tokens 0x... — Token ERC-20 di wallet
# /whale 0x...  — Deteksi whale activity
# /eth          — Status gas & jaringan Ethereum
#
# ─────────────────────────────────────────────────────────────────
# PATCH 2: handle_telegram_updates() — Tambahkan sebelum blok else:
# ─────────────────────────────────────────────────────────────────
#
#         elif text_lower.startswith("/wallet"):
#             parts = text.split()
#             if len(parts) < 2:
#                 send_telegram("❓ Contoh: `/wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
#             else:
#                 send_telegram("⏳ Mengambil data wallet...")
#                 send_telegram(web3_get_eth_balance(parts[1]))
#
#         elif text_lower.startswith("/txn"):
#             parts = text.split()
#             if len(parts) < 2:
#                 send_telegram("❓ Contoh: `/txn 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
#             else:
#                 send_telegram("⏳ Mengambil transaksi...")
#                 limit = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 5
#                 send_telegram(web3_get_transactions(parts[1], limit=limit))
#
#         elif text_lower.startswith("/tokens"):
#             parts = text.split()
#             if len(parts) < 2:
#                 send_telegram("❓ Contoh: `/tokens 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
#             else:
#                 send_telegram("⏳ Mengambil token holdings...")
#                 send_telegram(web3_get_tokens(parts[1]))
#
#         elif text_lower.startswith("/whale"):
#             parts = text.split()
#             if len(parts) < 2:
#                 send_telegram("❓ Contoh: `/whale 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
#             else:
#                 send_telegram("🔭 Menganalisis whale activity...")
#                 threshold = float(parts[2]) if len(parts) > 2 else 100.0
#                 send_telegram(web3_whale_watch(parts[1], threshold=threshold))
#
#         elif text_lower == "/eth":
#             send_telegram("⏳ Mengambil status Ethereum...")
#             send_telegram(web3_eth_network_status())
#
# ─────────────────────────────────────────────────────────────────
# PATCH 3: Free chat — Tambahkan di AWAL blok else:, sebelum `ctx = ...`
# ─────────────────────────────────────────────────────────────────
#
#             web3_reply = web3_detect_and_answer(text)
#             if web3_reply:
#                 send_telegram(web3_reply)
#                 continue
#
# ─────────────────────────────────────────────────────────────────
# SETELAH SEMUA PATCH, handle_telegram_updates() terlihat seperti ini:
# ─────────────────────────────────────────────────────────────────

PATCHED_HANDLER = '''
def handle_telegram_updates():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"timeout": 10, "offset": handle_telegram_updates.offset}, timeout=15)
        updates = r.json().get("result", [])
        for update in updates:
            handle_telegram_updates.offset = update["update_id"] + 1
            message = update.get("message", {})
            text    = message.get("text", "")
            chat_id = message.get("chat", {}).get("id")
            if not (text and chat_id):
                continue

            print(f"\\n📱 Pesan: {text}")
            text_lower = text.lower().strip()

            if text_lower in ("/start", "/help"):
                send_telegram(HELP_MSG)

            elif text_lower == "/coins":
                send_telegram(COINS_MSG)

            elif text_lower.startswith("/analisis"):
                coin = parse_coin_from_text(text)
                send_telegram(f"⏳ Menganalisis {coin}/USDT...")
                msg = run_full_analysis(coin)
                send_telegram(msg)

            elif text_lower.startswith("/status"):
                coin        = parse_coin_from_text(text)
                price_data  = get_price(coin)
                if not price_data:
                    send_telegram(f"❌ Gagal ambil data {coin}.")
                    continue
                fg          = get_fear_greed()
                analysis    = get_analysis(coin)
                signal_data = generate_signal(price_data, analysis, fg)
                send_telegram(format_status_message(coin, price_data, analysis, fg, signal_data))

            elif text_lower.startswith("/refresh"):
                auto_report()

            elif text_lower == "/okx":
                send_telegram("⏳ Mengambil status OKX...")
                send_telegram(format_okx_status())

            elif text_lower == "/daftar" or text_lower == "/register":
                if not OKX_WALLET_ADDRESS:
                    send_telegram("❌ Set OKX_WALLET_ADDRESS di .env dulu!")
                else:
                    send_telegram("⏳ Mendaftarkan ke kompetisi OKX...")
                    result = okx_register_competition()
                    send_telegram(result)

            elif text_lower == "/berita":
                send_telegram("⏳ Mengambil berita terbaru...")
                send_telegram(get_news_for_telegram(top_n=5))

            elif text_lower == "/topnews":
                send_telegram("🔍 Mencari top 3 berita paling impactful...")
                send_telegram(get_news_for_telegram(top_n=3))

            elif text_lower.startswith("/mingguan"):
                coin = parse_coin_from_text(text)
                send_telegram(f"⏳ Menyusun ringkasan mingguan {coin}...")
                send_telegram(get_weekly_summary(coin))

            # ── PATCH: Command Web3 Baru ──────────────────────────

            elif text_lower.startswith("/wallet"):
                parts = text.split()
                if len(parts) < 2:
                    send_telegram("❓ Contoh: `/wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
                else:
                    send_telegram("⏳ Mengambil data wallet...")
                    send_telegram(web3_get_eth_balance(parts[1]))

            elif text_lower.startswith("/txn"):
                parts = text.split()
                if len(parts) < 2:
                    send_telegram("❓ Contoh: `/txn 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
                else:
                    send_telegram("⏳ Mengambil transaksi...")
                    limit = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 5
                    send_telegram(web3_get_transactions(parts[1], limit=limit))

            elif text_lower.startswith("/tokens"):
                parts = text.split()
                if len(parts) < 2:
                    send_telegram("❓ Contoh: `/tokens 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
                else:
                    send_telegram("⏳ Mengambil token holdings...")
                    send_telegram(web3_get_tokens(parts[1]))

            elif text_lower.startswith("/whale"):
                parts = text.split()
                if len(parts) < 2:
                    send_telegram("❓ Contoh: `/whale 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045`")
                else:
                    send_telegram("🔭 Menganalisis whale activity...")
                    threshold = float(parts[2]) if len(parts) > 2 else 100.0
                    send_telegram(web3_whale_watch(parts[1], threshold=threshold))

            elif text_lower == "/eth":
                send_telegram("⏳ Mengambil status Ethereum...")
                send_telegram(web3_eth_network_status())

            # ── PATCH END ─────────────────────────────────────────

            else:
                # PATCH: deteksi wallet address di free chat dulu
                web3_reply = web3_detect_and_answer(text)
                if web3_reply:
                    send_telegram(web3_reply)
                    continue

                # Free chat dengan konteks pasar (kode asli kamu)
                price_data  = get_price(DEFAULT_COIN)
                fg          = get_fear_greed()
                analysis    = get_analysis(DEFAULT_COIN)
                signal_data = generate_signal(price_data, analysis, fg) if price_data else {}
                ctx = (
                    f"Harga BTC: ${price_data[\'price\']:,.2f} | Change 24h: {price_data[\'change_24h\']:.2f}%\\n"
                    f"RSI: {analysis[\'rsi\']} | MACD: {analysis[\'macd\']} | MA7: ${analysis[\'ma7\']:,} | MA14: ${analysis[\'ma14\']:,}\\n"
                    f"Fear & Greed: {fg[\'value\']} → {fg[\'label\']}\\n"
                    f"Signal AI: {signal_data.get(\'signal\',\'N/A\')} ({signal_data.get(\'confidence\',\'N/A\')}% confidence)"
                ) if price_data else "Data pasar tidak tersedia."
                print("🤖 AI menjawab pertanyaan bebas...")
                full_input  = ctx + "\\n\\nPertanyaan: " + text
                response    = llm.invoke([HumanMessage(content=full_input)])
                send_telegram(response.content)

    except Exception as e:
        print(f"❌ Error polling: {e}")
'''

# Ini hanya template referensi — kamu tidak perlu jalankan file ini langsung.
# Salin patch di atas ke btc_agent_okx.py kamu.
print("📋 Patch reference file — lihat komentar di atas untuk instruksi integrasi.")
