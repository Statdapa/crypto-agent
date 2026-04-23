from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
import requests
import os
import time
import schedule
from datetime import datetime

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

llm = ChatGroq(model="llama-3.1-8b-instant", api_key=GROQ_API_KEY)

# ─────────────────────────────────────────
# 1. DATA FETCHER
# ─────────────────────────────────────────

def get_btc_price():
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "bitcoin", "vs_currencies": "usd", "include_24hr_change": "true"}
    )
    data = r.json()["bitcoin"]
    return {
        "price": data["usd"],
        "change_24h": data.get("usd_24h_change", 0)
    }

def get_fear_greed():
    r = requests.get("https://api.alternative.me/fng/?limit=1")
    data = r.json()["data"][0]
    return {
        "value": int(data["value"]),
        "label": data["value_classification"]
    }

def get_btc_ohlc():
    r = requests.get(
        "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc",
        params={"vs_currency": "usd", "days": "14"}
    )
    return r.json()

# ─────────────────────────────────────────
# 2. ANALISIS TEKNIKAL
# ─────────────────────────────────────────

def calculate_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def get_analysis():
    ohlc = get_btc_ohlc()
    closes = [candle[4] for candle in ohlc]
    ma7  = calculate_ma(closes, 7)
    ma14 = calculate_ma(closes, 14)
    rsi  = calculate_rsi(closes)
    return {
        "ma7":  round(ma7, 2)  if ma7  else "N/A",
        "ma14": round(ma14, 2) if ma14 else "N/A",
        "rsi":  rsi if rsi else "N/A"
    }

# ─────────────────────────────────────────
# 3. TELEGRAM
# ─────────────────────────────────────────

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        })
        print("✅ Notifikasi terkirim ke Telegram!")
    except Exception as e:
        print(f"❌ Gagal kirim Telegram: {e}")

def handle_telegram_updates():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"timeout": 10, "offset": handle_telegram_updates.offset})
        updates = r.json().get("result", [])
        for update in updates:
            handle_telegram_updates.offset = update["update_id"] + 1
            message = update.get("message", {})
            text = message.get("text", "")
            chat_id = message.get("chat", {}).get("id")
            if text and chat_id:
                print(f"\n📱 Pesan Telegram masuk: {text}")
                if text.lower() == "/start":
                    send_telegram("🤖 *BTC AI Agent aktif!*\n\nKamu bisa tanya apa aja soal BTC.\n\nCommand:\n/refresh - Update data terbaru\n/status - Lihat kondisi pasar")
                elif text.lower() == "/refresh":
                    auto_report()
                elif text.lower() == "/status":
                    _, btc, analysis, fg = build_context()
                    msg = f"""
📊 *Status BTC Sekarang*
💰 Harga: *${btc['price']:,.0f}* ({btc['change_24h']:.2f}%)
📈 RSI: {analysis['rsi']}
📉 MA7: ${analysis['ma7']:,}
📉 MA14: ${analysis['ma14']:,}
😨 Fear & Greed: {fg['value']} → {fg['label']}
"""
                    send_telegram(msg)
                else:
                    _, btc, analysis, fg = build_context()
                    ctx = f"""
Harga BTC: ${btc['price']:,.2f} USD
Perubahan 24h: {btc['change_24h']:.2f}%
RSI: {analysis['rsi']}
MA7: ${analysis['ma7']:,}
MA14: ${analysis['ma14']:,}
Fear & Greed: {fg['value']} → {fg['label']}
"""
                    print("🤖 AI sedang menjawab...")
                    response = chat(text, ctx)
                    send_telegram(response)
    except Exception as e:
        print(f"❌ Error polling: {e}")

handle_telegram_updates.offset = 0

# ─────────────────────────────────────────
# 4. BUILD CONTEXT & AUTO REPORT
# ─────────────────────────────────────────

def build_context():
    print("🔄 Mengambil data pasar...")
    btc      = get_btc_price()
    fg       = get_fear_greed()
    analysis = get_analysis()

    context = f"""
=== DATA PASAR CRYPTO (Update: {datetime.now().strftime('%Y-%m-%d %H:%M')}) ===
Harga BTC    : ${btc['price']:,.2f} USD
Perubahan 24h: {btc['change_24h']:.2f}%

=== INDIKATOR TEKNIKAL ===
MA 7 hari    : ${analysis['ma7']:,}
MA 14 hari   : ${analysis['ma14']:,}
RSI (14)     : {analysis['rsi']} {'(Overbought ⚠️)' if isinstance(analysis['rsi'], float) and analysis['rsi'] > 70 else '(Oversold 🟢)' if isinstance(analysis['rsi'], float) and analysis['rsi'] < 30 else '(Netral)'}

=== SENTIMEN PASAR ===
Fear & Greed Index: {fg['value']} / 100 → {fg['label']}
"""
    return context, btc, analysis, fg

def auto_report():
    context, btc, analysis, fg = build_context()

    alerts = []
    if isinstance(analysis['rsi'], float):
        if analysis['rsi'] > 70:
            alerts.append("⚠️ RSI Overbought!")
        elif analysis['rsi'] < 30:
            alerts.append("🟢 RSI Oversold - Potensi beli!")
    if fg['value'] < 25:
        alerts.append("😱 Extreme Fear - Potensi bottom!")
    elif fg['value'] > 75:
        alerts.append("🤑 Extreme Greed - Hati-hati!")

    msg = f"""
🤖 *BTC Alert Report*
🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}

💰 Harga: *${btc['price']:,.0f}* ({btc['change_24h']:.2f}%)
📊 RSI: {analysis['rsi']}
📈 MA7: ${analysis['ma7']:,}
📉 MA14: ${analysis['ma14']:,}
😨 Fear & Greed: {fg['value']} → {fg['label']}

{'🚨 *ALERTS:* ' + ' | '.join(alerts) if alerts else '✅ Kondisi Normal'}
"""
    send_telegram(msg)
    return context

# ─────────────────────────────────────────
# 5. CHAT
# ─────────────────────────────────────────

def chat(user_input, context):
    full_input = context + "\n\nPertanyaan user: " + user_input
    response = llm.invoke([HumanMessage(content=full_input)])
    return response.content

# ─────────────────────────────────────────
# 6. MAIN
# ─────────────────────────────────────────

print("🤖 BTC AI Agent aktif!")
print("📱 Bot Telegram siap menerima pesan...\n")

auto_report()
schedule.every(1).hours.do(auto_report)

while True:
    schedule.run_pending()
    handle_telegram_updates()
    time.sleep(3)