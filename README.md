# FinSight Agent

**Enterprise crypto intelligence platform for Southeast Asia.**  
AI-powered anomaly detection, real-time market signals, and on-chain wallet monitoring — delivered via Telegram bot and web dashboard.

---

## The Problem

Crypto treasury holders — from individual traders to small enterprises in Southeast Asia — lack affordable tools to monitor portfolio risk in real time. Traditional anomaly detection requires dedicated analysts. Manual monitoring misses threats that happen in minutes.

## The Solution

FinSight Agent combines AI agents with on-chain execution monitoring:

- **Real-time anomaly detection** on wallet transactions using Z-score statistical analysis
- **Unvalidated rule-based signals** (BUY/SELL/HOLD) based on technical indicators
- **Proactive alerts** via Telegram — no dashboard needed to stay informed
- **Web dashboard** for enterprise-grade visualization and historical analysis
- **OKX integration** — both CEX balance and DEX wallet monitoring

---

## Architecture

```
.env (API keys)
    │
    ├── crypto_intelligence_agent.py    ← Telegram bot + AI agent (LangChain + Groq)
    ├── web3_upgrade.py     ← Etherscan + OKX DEX integration
    ├── anomaly_detector.py ← Statistical anomaly detection engine
    └── app.py              ← Flask backend serving dashboard.html
```

**Data flow:**
```
OKX API / Etherscan → anomaly_detector.py → SQLite DB → Flask API → Dashboard
Binance / CoinGecko → crypto_intelligence_agent.py   → Telegram alerts
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
python crypto_intelligence_agent.py

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
├── crypto_intelligence_agent.py      ← Main Telegram bot
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

## 2026 safety update

- Set `DASHBOARD_TOKEN` in the server's `.env`, then enter it in the dashboard prompt. Sensitive API routes require the `X-Dashboard-Token` header. Never put the token in a query string.
- Agent and Flask app must share `DB_PATH=crypto_intelligence_agent.db`.
- The dashboard does not calculate audited profit, win rate or live portfolio PnL. It does not execute trades; its indicators are not probability-calibrated.
- Web chat is a read-only helper, not a connection to the Groq agent. No historical strategy backtest is implemented in this repository.
- Run tests with `python -m unittest discover -s tests -v`. GitHub Actions checks Python and embedded JavaScript syntax.
- Do not use OKX withdrawal-enabled API keys.


## Paper-only spot execution (experimental)

The new module `trading_engine.py` records simulated spot fills in an isolated SQLite file. It never transmits orders to an exchange. It uses existing `market_snapshots` from the agent and does **not** claim profitable or backtested performance.

```bash
# Run the intelligence agent in a separate terminal to populate its database.
python crypto_intelligence_agent.py

# Only acts on a timezone-aware, recent saved signal; no signal -> no trade.
python paper_trade.py tick --source-db crypto_intelligence_agent.db --ledger data/paper_trading.db
python paper_trade.py status
python paper_trade.py fills
python paper_trade.py halt
python paper_trade.py resume --ack-resume-risk

python -m unittest discover -s tests -v
```

Default limits: BTC/USDT spot only, $1,000 virtual cash, 1% equity at risk per trade, 20% maximum position, 3% stop, 5% take-profit, maximum 3 entries per day, 3% daily equity loss stop, 10% maximum drawdown and 45-minute signal expiry. Fees and slippage are both modeled; no leverage, shorting, real orders or profit guarantee. Stops are evaluated on **ticks supplied to the simulator**, not exchange stop orders. Gaps and missing ticks can cause losses greater than the limits. Live execution should not be connected until exchange-specific filters, idempotent placement, reconciliation, manual risk review and walk-forward validation are implemented and tested.

### Paper monitoring and accounting

```bash
python paper_trade.py watch --poll-seconds 60
python paper_trade.py watch --poll-seconds 10 --max-cycles 2
python paper_trade.py stats
```

The watch process polls only the already saved agent snapshot; it neither calls private exchange endpoints nor places exchange orders. Only a new, recent, timezone-aware snapshot can cause a paper fill. Metrics include **closed-paper-trade win rate** (not the trading strategy's real out-of-sample win rate). Stopping the watch process does not place exchange-side stops; no real order protections exist.

### Causal backtesting baseline (separate from the agent strategy)

`python backtest_baseline.py --csv data/raw/BTC_USDT_4h.csv --fast 20 --slow 50 --output data/reports/sma_baseline.json`

The CSV must contain `timestamp,open,high,low,close` with explicit timezone-aware ISO-8601 timestamps, sorted ascending. **This SMA baseline is not a backtest of the existing AI/market-regime strategy.** Signals are computed only from bars through t−1, trades are assumed filled at bar t open, and same-bar stop-and-target touches are resolved pessimistically (stop first). Fees and slippage are modeled, but exchange constraints, market impact, data gaps, corporate or microstructure issues and funding costs are not. In-sample backtest performance does not imply live win rate. Never feed unfinished candles.

### Market-data identity safeguard (SOL/BTC/ETH)

Requests for BTC, ETH and SOL now validate CoinGecko's response **id** and **symbol**, compare the USD quote against an independent public Binance spot USDT ticker, and reject discrepancies greater than 8%. This is deliberately fail-closed: if the independent reference API is blocked/unavailable, the agent skips that snapshot rather than risking a mislabeled asset price. All symbols have their CoinGecko identity checked. Snapshot writes also require the requested coin and quoted coin to match. Unknown explicit coin symbols are no longer silently interpreted as BTC.

For Telegram use `/analisis sol` or `/status sol` (not unsupported shorthand `/sol`). If old database rows are suspect, retain the database for audit and request a new verified snapshot rather than changing historical prices by hand. Testing: `python -m unittest discover -s tests -v`.

## Demo paper trading with virtual SL/TP and Telegram alerts

This is simulated spot trading, **not actual exchange demo/testnet orders**.
Use the same database/ledger on both processes. Make sure the Telegram token
and owner chat ID are configured in your local .env. Never commit them.

Start the intelligence agent in terminal 1:

    python crypto_intelligence_agent.py

Start independently verified live-quote paper monitoring in terminal 2:

    python paper_trade.py demo-watch --poll-seconds 60

Check without leaving Telegram: /paper, /position, /trades.
On actual paper BUY/SELL fills the watcher sends simulated OPEN/CLOSE alerts,
entry, virtual SL/TP, and net realized PnL. Polling evaluates stop/take only
at observed quotes: price gaps can exceed intended stop loss. Market quote
validation rejects mismatched/unavailable sources. Sizing and risk controls
remain from the paper ledger. A Telegram delivery failure leaves a fill queued
for retry, including with:

    python paper_trade.py notify

Delivery is at-least-once (a process crash between Telegram delivery and
acknowledgement can duplicate a message). The existing watch command remains
available but does NOT have verified live-quote monitoring; use demo-watch for
this workflow. No fabricated trades or win rates are displayed. Orders are
never submitted to a real or demo exchange. Do not run multiple demo-watch
workers on the same ledger.

## Binance-wide dynamic spot market discovery

All **currently active Binance spot USDT** pairs are discovered from the public
exchangeInfo endpoint instead of being hard-coded. The market scanner polls the
full exchange-wide 24h ticker in a batch every SNAPSHOT_INTERVAL_MINUTES and
reports the top 15 absolute 24h movers filtered by >= 1 million USDT volume.
Telegram commands: /scanall (all-market scan), /coins (catalog size and examples),
/analisis [symbol] or /status [symbol] for a deep analysis of any active
Binance spot USDT base symbol. Full RSI/MA/MACD analysis uses historical
completed 4h candles, not every pair's 24h ticker data. Pairs with too little
OHLCV data cannot produce an actionable rule-based signal. CoinGecko independent
price cross-check remains on BTC, ETH, SOL; other pairs have exchangeInfo symbol
identity checks on ticker and OHLCV (one Binance source). Missing 7-day
market data is displayed as N/A, not invented.

To control rate limits and Groq calls, the scheduled deep analysis remains
MARKET_COINS=BTC,ETH,SOL by default; use MARKET_COINS=BTC,ETH,SOL,AVAX,... for
a small, explicit scheduled deep-watchlist. Scanning the entire catalog does
NOT automatically create BUY/SELL snapshots, nor submit paper or live orders
on all coins. For one additional paper instrument use a distinct ledger and
--symbol (e.g. SOL/USDT), but the Telegram account status command currently
reads only PAPER_DB_PATH. Do not run separate ledgers as if they shared one
portfolio risk budget. None of this uses or requires real exchange API keys.

## MULTI-COIN DEMO (shared virtual balance, NO LIVE ORDERS)

The experimental multi-coin engine uses one shared 1,000-USDT **virtual** account
for up to three independently marked Binance spot USDT positions. This is not
an exchange demo/testnet account. Entry signals are generated by existing
RSI/MACD/MA/Bollinger rules on a bounded liquid candidate list. A 24-hour price
mover alone is *never* an entry. Only completed 4h candles are used for technical
analysis. This component is not a validated investment strategy.

Configuration and limits: 15% equity maximum per position, 45% marked total
exposure, 1% equity risk per trade, 3% virtual SL, 5% virtual TP, 3% daily
equity loss trigger, 10% maximum drawdown trigger, five entries per day, one-hour
reentry cooldown after exits, 10bps modeled fees and 5bps slippage per side.
A 1-hour cooldown cannot prevent rapid losses on volatile markets.
Mark prices are periodically fetched and may miss intra-poll crosses; losses may
exceed the nominal stop. Market gaps and depegs are not modeled faithfully.

Start in one terminal (from project root, .venv active):

    python crypto_intelligence_agent.py

Start the new shared multi-coin paper demo in a **different** terminal:

    python multi_paper_trade.py scan
    python multi_paper_trade.py watch --poll-seconds 60 --max-candidates 8

Check:

    python multi_paper_trade.py status
    python multi_paper_trade.py fills
    python multi_paper_trade.py stats
    python multi_paper_trade.py notify
    python multi_paper_trade.py halt
    python multi_paper_trade.py resume --ack-resume-risk

Telegram owner commands: /multi, /multipos and /multitrades. A notification is
sent after a simulated BUY or SELL fill (including stop/take closes, realized net
PnL, entry/exit fees). Delivery failures stay queued for retry; a crash after
Telegram acceptance and before acknowledgement may duplicate an alert.

The older BTC-only simulator uses a different paper ledger; **do not run the old
watchers alongside multi watch** if you intend to observe one shared strategy
or compare performance. Only this multi trader shares money across its coins.
No API keys for trade or withdrawal are required. Do not connect live exchange
execution. Stop-loss and take-profit are not real exchange orders.

Candidate ranking scans all *currently active Binance spot USDT pairs* via public
24-hour ticker data. A bounded set of liquid movers receives detailed 4h analysis
(max 12, default 8) to control API rate limiting; this does NOT automatically
analyze RSI/MACD for hundreds of pairs every minute. Leverage-like tickers,
stablecoin bases, wrapped assets, extremely low-liquidity names and extraordinary
24-hour moves are filtered heuristically, not guaranteed to be risk-free.

### Telegram command consistency for multi-coin demo

When using `python multi_paper_trade.py watch`, the default owner-chat commands
`/position` and `/multipos` show **all open positions from**
`MULTI_PAPER_DB_PATH`, including current mark, entry, quantity, virtual SL/TP,
and estimated unrealized net PnL. The aliases `/paper` and `/multi` show
shared portfolio equity; `/trades` and `/multitrades` show its fill history.
Legacy *single-coin* account queries were moved to `/singlepaper`,
`/singleposition`, and `/singletrades` to prevent misleading
`Position: NONE` while a multi-coin position is open. Restart the Telegram
agent after updating the code; use the same working directory and configured
ledger path for the Telegram and multi-paper processes. The reports show the
last observed quote, which is not necessarily current if a data feed is stale.
