# FinSight Agent

**Enterprise crypto intelligence platform for Southeast Asia.**  
AI-powered anomaly detection, real-time market signals, and on-chain wallet monitoring — delivered via Telegram bot and web dashboard.

---

## The Problem

Crypto treasury holders — from individual traders to small enterprises in Southeast Asia — lack affordable tools to monitor portfolio risk in real time. Traditional anomaly detection requires dedicated analysts. Manual monitoring misses threats that happen in minutes.

## The Solution

FinSight Agent combines AI agents with on-chain execution monitoring:

- **Real-time anomaly detection** on wallet transactions using Z-score statistical analysis
- **AI signals** (BUY/SELL/HOLD) generated from RSI, MACD, Bollinger Bands across 60+ coins
- **Proactive alerts** via Telegram — no dashboard needed to stay informed
- **Web dashboard** for enterprise-grade visualization and historical analysis
- **OKX integration** — both CEX balance and DEX wallet monitoring

---

## Architecture

```
.env (API keys)
    │
    ├── btc_agent_okx.py    ← Telegram bot + AI agent (LangChain + Groq)
    ├── web3_upgrade.py     ← Etherscan + OKX DEX integration
    ├── anomaly_detector.py ← Statistical anomaly detection engine
    └── app.py              ← Flask backend serving dashboard.html
```

**Data flow:**
```
OKX API / Etherscan → anomaly_detector.py → SQLite DB → Flask API → Dashboard
Binance / CoinGecko → btc_agent_okx.py   → Telegram alerts
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| AI Agent | LangChain + Groq (Llama 3.1) |
| Anomaly Detection | Python, NumPy (Z-score, IQR) |
| On-chain | Etherscan API, Web3.py |
| CEX Integration | OKX API |
| Backend | Flask, SQLite |
| Frontend | HTML/CSS/JS, Chart.js |
| Alerts | Telegram Bot API |

---

## Setup

### 1. Clone & install

```bash
cd "your-project-folder"
python -m venv .venv

# Windows
.venv\Scripts\Activate.ps1

# Mac/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure `.env`

Copy `.env.example` to `.env` and fill in your keys:

```env
GROQ_API_KEY=your_groq_key
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
OKX_API_KEY=your_okx_key
OKX_SECRET_KEY=your_okx_secret
OKX_PASSPHRASE=your_okx_passphrase
WALLET_ADDRESS=0xYourEthereumWallet
OKX_WALLET_ADDRESS=0xYourOKXDEXWallet
ETHERSCAN_API_KEY=your_etherscan_key
DASHBOARD_TOKEN=any_password_you_want
```

**Get free API keys:**
- Groq: https://console.groq.com
- Etherscan: https://etherscan.io/apis
- OKX: https://www.okx.com/account/my-api

### 3. Run

```bash
# Terminal 1 — Telegram bot
python btc_agent_okx.py

# Terminal 2 — Web dashboard
python app.py
```

Open dashboard: **http://localhost:5000**

---

## Telegram Commands

| Command | Description |
|---|---|
| `/analisis btc` | Full AI analysis for BTC |
| `/status eth` | Quick price + signal status |
| `/mingguan` | Weekly summary report |
| `/wallet 0x...` | ETH wallet balance |
| `/txn 0x...` | Recent transactions |
| `/tokens 0x...` | Token holdings |
| `/whale 0x...` | Whale activity detection |
| `/scan` | Run anomaly scan |
| `/okx` | OKX CEX balance |
| `/berita` | Latest crypto news |
| `/coins` | List all tracked coins |

---

## Anomaly Detection

FinSight detects 6 types of wallet anomalies:

| Type | Trigger | Severity |
|---|---|---|
| `RAPID_DRAIN` | Large ETH outflow in short window | CRITICAL |
| `LARGE_TRANSFER` | Z-score > 3.0 above historical mean | HIGH |
| `DUST_ATTACK` | Multiple tiny inflows from unknown addresses | HIGH |
| `FREQUENCY_SPIKE` | Tx rate 5x above baseline | MEDIUM-HIGH |
| `FAILED_TX_SPIKE` | >40% failure rate in recent txs | MEDIUM-HIGH |
| `NEW_ADDRESS_INTERACTION` | Transfer to/from unknown wallet | MEDIUM |

And 4 market anomalies: `PRICE_CRASH`, `PRICE_PUMP`, `RSI_OVERBOUGHT/OVERSOLD`, `EXTREME_FEAR/GREED`, `SIGNAL_FLIP`.

---

## Dashboard Features

- **Live price chart** — switchable coin, interval (1D/1W/1M/3M/1Y), chart type (line/bar)
- **AI signals** — RSI-based BUY/SELL/HOLD for 9+ coins with rationale
- **Market page** — Top 20 by market cap, filters, AI signal per coin
- **Security scan** — Real-time anomaly detection via Etherscan
- **Portfolio** — Live holdings valuation
- **AI Agent chat** — Ask questions in natural language

---

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/health` | System health check |
| `GET /api/prices` | Live coin prices |
| `GET /api/chart?coin=bitcoin&days=7` | Historical chart data |
| `GET /api/market` | Top 20 market data |
| `GET /api/feargreed` | Fear & Greed Index |
| `GET /api/wallet/balance` | ETH wallet balance |
| `GET /api/wallet/transactions` | Recent transactions |
| `GET /api/okx/balance` | OKX CEX balance |
| `POST /api/scan` | Run anomaly scan |
| `GET /api/scan/history` | Past anomaly alerts |
| `GET /api/report/weekly?coin=BTC` | Weekly report |
| `GET /api/signals` | AI signals from DB |
| `GET /api/config` | Configuration status |

---

## Project Structure

```
AI AGENT/
├── .env                  ← API keys (never commit this)
├── .env.example          ← Template
├── requirements.txt      ← Python dependencies
├── btc_agent_okx.py      ← Main Telegram bot
├── web3_upgrade.py       ← Web3/Etherscan/OKX DEX
├── anomaly_detector.py   ← Anomaly detection engine
├── app.py                ← Flask backend
├── dashboard.html        ← Web dashboard
└── btc_agent.db          ← SQLite database (auto-created)
```

---

## Built For

**lablab.ai — Build the Next Intelligent Enterprise Solution**  
Track: Data & Intelligence  
Team: FinSight

*Not financial advice. Always verify alerts manually before taking action.*
