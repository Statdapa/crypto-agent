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

## Stage 10 — Observed Performance Analytics and Causal Research Backtest

From project root with your local .venv, keep the existing shared multi-coin
ledger unchanged. Performance analytics reads **all** BUY and SELL fills and
matches closes to their actual entry IDs, not merely the last 200 fills.

    python multi_paper_trade.py performance
    python performance_report.py --ledger data/multi_paper.db
    # JSON: data/reports/multi_performance.json
    # CSV: data/reports/multi_closed_trades.csv
    # CSV: data/reports/multi_observed_equity.csv

Telegram: /performance for closed-trade win rate, realized net PnL, expectancy,
profit factor, open estimated PnL, and observed drawdown. Null/NA metrics mean
insufficient closed trades, **not** 0% win rate. Older data cannot be backfilled:
equity snapshots start when this update was installed, so any historical
drawdown is *partial* and mark-frequency dependent. No annualized Sharpe ratio
is calculated from irregular marks. The mark history includes only accepted
observations; missed quote changes and intra-poll drawdowns are not measured.

A **research-only** backtest of the agent's canonical rule-based signal scoring
is available for a fixed universe of 4-hour CSV candles. Signals use only
fully completed bars through t−1, with hypothetical next-bar-open fills;
intrabar SL/TP is evaluated conservatively (stop-first when both touched).
Fees, slippage, shared cash, max 3 open positions and position limits are
modeled. A 70/30 chronological train/test split reports results separately;
it does **not** optimize parameters.

Use genuine historical CSV files with timezone-aware ISO timestamp and
timestamp,open,high,low,close columns (one file per coin):

    python backtest_multi_rules.py --csv BTC/USDT=data/raw/BTC_USDT_4h.csv
    # Additional local complete datasets:
    python backtest_multi_rules.py --csv BTC/USDT=data/raw/BTC_USDT_4h.csv SOL/USDT=data/raw/SOL_USDT_4h.csv

Results: data/reports/multi_backtest.json. Input needs >=100 synchronized
continuous 4h bars. Do not label it a replay of the **dynamic top movers
scanner**: historical Binance listings, volume-ranking candidates and delisted
coins are not fully reconstructed, so fixed-universe survivorship bias remains.
Historical Fear & Greed is unavailable here; the research run explicitly
holds it at neutral=50. Because that differs from live agent sentiment and
dynamic candidate selection, out-of-sample results estimate a *proxy*
strategy, not live win-rate accuracy. No future return is used to choose
a symbol or a fill. Do not tune strategy parameters on the held-out test set.

### Download missing real Binance candles for historical multi-coin research

CSV candle data lives under local \`data/raw/\` and is intentionally excluded
from git. A fresh clone does not contain \`BTC_USDT_4h.csv\` or
\`SOL_USDT_4h.csv\`. The previous crypto-alpha-engine project's raw files
are not automatically copied to this repository.

Download verified active Binance spot USDT candles, only fully closed 4h bars:

    python download_backtest_data.py BTC SOL --limit 500
    python backtest_multi_rules.py --csv BTC/USDT=data/raw/BTC_USDT_4h.csv SOL/USDT=data/raw/SOL_USDT_4h.csv

To replace intentionally and get the latest 500 candles:

    python download_backtest_data.py BTC SOL --limit 500 --overwrite

This downloader uses public data only; it does not need exchange API keys,
does not use invented historical candles and does not place any trading order.
Each CSV has timezone-aware \`timestamp,open,high,low,close\` headers and
contiguous UTC 4h candles. One Binance request supports at most 1,000 candles;
for longer histories, use a separate paginated historical collector and retain
the same schema. A Binance API outage or blocked region prevents downloading,
and the file is not silently fabricated. The existing report is a fixed-coin
historical proxy, *not* a full backtest of the dynamically selected scanner.


## Stage 11 — Quant Research Lab (research-only)

**Motivation:** The fixed BTC/SOL rule backtest reported negative out-of-sample
expectancy and 0 wins from 7 completed test trades. This is a small sample,
but it is evidence AGAINST promoting the current rule system to real trading.
No model or claim can replicate Jane Street's institutional proprietary data,
research, risk systems, team, or market-making infrastructure.

### Run with existing completed 4h CSVs

In Windows PowerShell (project root, .venv installed with requirements.txt):

    git pull origin feat/paper-trading-risk-engine-20261111
    python -m unittest discover -s tests -v
    python quant_research.py --csv BTC/USDT=data/raw/BTC_USDT_4h.csv SOL/USDT=data/raw/SOL_USDT_4h.csv

If historical CSVs are missing:

    python download_backtest_data.py BTC SOL --limit 500

The output is saved to \`data/reports/quant_research.json\`, with a
research/NO AUTO-EXECUTION marker. No new exchange credentials, services, or
Telegram commands are required. Keep the currently running paper strategy
unchanged. **Do not copy a high-probability prediction into a real order.**

### Methods

- Shared 4h synchronized spot datasets (>=300 *completed* candles per coin);
  symbols checked against their own actual OHLC data, gapless and UTC-aware.
- Feature at closed bar t: returns over 4h/24h/72h, realized 20-period log
  volatility, 10/30 MA spread, candle range and close location, normalized
  RSI 14, and distance to 20-period high. No future candles in features.
- Historical label: BUY at t+1 open and exit at t+6 close by default, less
  fees and slippage at **both ends**. This is a different experiment than
  the 3% SL/5% TP paper execution strategy.
- Expanding chronological learning; purged fit/calibration/test partitions.
  Only the fit partition determines means, standard deviations, logistic
  parameters, and the high-vol regime threshold. Calibration is trained on
  a separate earlier chronological partition, never the test partition.
  Future-label overlapping partitions are excluded. Three disjoint test
  windows cover the late 40% of the sample (approx. 60-73%,73-86%,86-100%).
- Model: numpy-only ridge-regularized logistic regression with sigmoid
  calibration, not a giant neural network. Evaluated against constant prior
  positive rate using out-of-sample Brier score and log-loss.
- Fixed predeclared probability threshold 0.65 (not optimized against test).
  Label-based research trade outcomes are non-overlapping **per symbol**,
  with a global deduplication across sequential folds.
- Outcomes: per-coin and trend/volatility regime, predictive calibration,
  net returns on selected fixed-horizon trades. Selected return sums are
  **unit-notional arithmetic research scores, not portfolio PnL**, and are
  not independent observations if different coins move together.
- A conservative experimental gate requires >=30 selected trades, >0 mean net
  return, minimum activity, positive selected return in *each* fold and
  improvement in Brier score over the baseline. Even if this research gate
  happens to pass, \`promotion_to_paper_execution\` remains **false**.
- Problems still to solve before a production strategy: dynamic delisted
  coin universe; historical real spread and order-book depth, survivorship
  bias, liquidity-aware sizing and adverse selection, robust OOS across
  multiple market cycles, correlated exposure, execution latency, risk
  stress tests, and sustained prospective paper monitoring.

**Do not optimize probability threshold, indicators, assets or costs by
looking at held-out test results repeatedly.** For later improvements,
create a fresh untouched test period or run a prospective paper study.


## Stage 12 — Historical Expansion and Feature Ablation (no execution)

The original 500-candle, ~83-day sample produced unstable ROC-AUC across BTC
and SOL (per-fold model AUC ranged below 0.5). Do **not** conclude that
changing thresholds on the same test set creates an edge. This stage adds
multi-year historical 4h candles and diagnostic ablations that retain an
untouched final 20% of the data.

From PowerShell in the repository root:

    git pull origin feat/paper-trading-risk-engine-20261111
    python -m unittest discover -s tests -v
    python download_historical_data.py BTC SOL --bars 6000 --overwrite
    python feature_ablation.py --csv BTC/USDT=data/raw/BTC_USDT_4h.csv SOL/USDT=data/raw/SOL_USDT_4h.csv

**Historical download:** public Binance 4h klines are paginated backward in
batches of at most 1000 and validated for UTC boundaries, contiguous timestamps,
OHLC integrity and complete candles. 6000 candles represent about 1000 days,
depending on exchange history. Data from a more recently listed asset can be
shorter. CSVs live under gitignored \`data/raw/\`. The \`--overwrite\` flag
explicitly replaces the shorter 500-candle research files, **not** the trading
ledger. The existing paper database \`data/multi_paper.db\` is untouched.
If Binance is unavailable (network/region/rate limit), collection fails
without generating fake candles. Check that the two downloaded datasets
have the same 4h timestamp interval; the research pipeline intentionally
rejects mismatches rather than silently dropping date ranges.

**Feature ablations:** the diagnostic research script compares all 9 original
features against variants with momentum, trend, volatility or oscillator
groups removed, plus an only-momentum baseline. Training scaling is fit on
past data only. Each validation period uses an earlier, purged calibration
interval. Its final 20% of historical feature timestamps is held aside
**without computing holdout labels, probabilities or scores in this report**.
The report is saved to \`data/reports/feature_ablation.json\` and displays
development-period Brier improvements versus a constant-rate baseline.
This stage makes no selection or trading decision. Variants evaluated against
the *development windows* require a new independent test on later data before
any prospective paper deployment. Serially overlapping label horizons and
correlated coins limit effective sample size even when N is large.

Ablation is **not** an optimizer that guarantees profit: no live trading,
paper model promotion, or adjusted order sizing is added here.


## Stage 13 — Statistical Robustness Lab (research-only)

The Stage 12 feature-ablation development results did **not** establish a
consistent edge: all six mean Brier improvements were negative despite ~6,000
closed 4h bars per coin. Stage 13 tests whether any apparent improvement
survives uncertainty and multiplicity while retaining the same **untouched**
last-20%-of-history holdout.

In Windows PowerShell from your repository root (existing .venv):

    git pull origin feat/paper-trading-risk-engine-20261111
    python -m unittest discover -s tests -v
    python quant_robustness.py --csv BTC/USDT=data/raw/BTC_USDT_4h.csv SOL/USDT=data/raw/SOL_USDT_4h.csv

Default output: \`data/reports/quant_robustness.json\`.

Optional **pre-specified sensitivity checks** (do not cherry-pick settings
after seeing results):

    python quant_robustness.py --csv BTC/USDT=data/raw/BTC_USDT_4h.csv SOL/USDT=data/raw/SOL_USDT_4h.csv --block-bars 36 --bootstrap-iterations 1200 --seed 2026 --output data/reports/quant_robustness_block36.json

What it measures:
- **Paired circular moving-block bootstrap** resamples *4h time clusters*
  together across BTC, SOL and all six feature variants. Within each of the
  three chronological development folds, consecutive timestamps are
  resampled in circular contiguous blocks (18 four-hour bars by default).
  Blocks do not connect different folds. This is more appropriate than
  treating overlapping label events or correlated coins as independent.
- **Familywise one-sided max-statistic correction** reports exploratory
  simultaneous lower confidence bounds and multiplicity-adjusted p-values
  for the six predeclared Brier improvements over their train-only
  constant-probability baseline. Values with adjusted p<=0.05 and lower
  bound >0 would be encouraging *for that development sample*; they are
  not proof of a tradable edge or predictive performance in a fresh period.
- **Regime attribution** uses signal-time MA spread (UP/DOWN/SIDEWAYS) and
  *training-fit-set* volatility median for LOW/HIGH_VOL buckets. Results
  show counts, actual positive rate, predicted probability and relative
  Brier scores. These condition on historical observed data only.
- **Economic cost sensitivity** evaluates the predeclared all-features
  65% threshold on fixed-horizon nonoverlapping research signals for zero,
  baseline, double and triple modeled fees/slippage. These returns are
  not actual executed portfolio results, nor is the model refitted.
- **Holdout barrier**: the first 80% of feature timestamps determine
  development candidates, with purging before the final 20% begins.
  Holdout labels are never even materialized in this diagnostic run;
  tests verify that changing prices wholly inside the final holdout
  cannot change Stage 13 outputs. The reserved holdout is for a
  separately preregistered future evaluation, not iterative selection.

Limitations: even moving blocks only approximate sampling uncertainty with
nonstationary, overlapping financial returns. The current project has already
used development feedback to formulate models, violating any strict
preregistration assumption. No active/delisted universe reconstruction, order
book, spread impact, portfolio-level risk or genuine prospective forward
validation is included. **Model promotion remains disabled** regardless of
research output. No new exchange/private API, no live orders and no change to
the existing paper trading strategy.


## Stage 14 — Economic Edge Validation (RESEARCH ONLY)

Stage 13 found 0 of 6 ablation models with a statistically significant
positive Brier improvement and only SIX nonoverlapping fixed-horizon,
0.65-threshold, all-features selected entries. Their average positive unit
return should **not** be confused with a portfolio backtest or proof of edge.

### Run (PowerShell, existing 6000-bar BTC/SOL historical CSVs)

    git pull --ff-only origin feat/paper-trading-risk-engine-20261111
    python -m unittest discover -s tests -v
    python economic_edge.py --csv BTC/USDT=data/raw/BTC_USDT_4h.csv SOL/USDT=data/raw/SOL_USDT_4h.csv

The last command writes a readable JSON:
\`data/reports/economic_edge.json\`. It never accesses Binance private APIs,
trading credentials, Telegram execution or the persistent paper portfolio
database. It does **not** modify the live paper trading bot.

### Two experiments are intentionally reported separately

1. **Six-trade fixed-horizon concentration audit:** derives from the already
   examined Stage 13 all-features model and its unchanged >=0.65 probability
   filter and fixed six-bar horizon. Lists trade-level outputs, distributions
   by symbol, development fold and signal-time regime, leave-one-out mean
   return, mean excluding two best trades, top-two share of positive
   and aggregate unit-notional returns. These ex-post deletions are
   sensitivity analyses and never trading rules.
2. **Portfolio OHLC proxy:** from the development model's 4h closed-bar
   predictions, LONG entries happen only at the next candle OPEN. Uses a
   single virtual 1000 USDT cash ledger, <=15% equity per coin, <=45% total,
   <=3 positions, 1% equity risk budget per 3% stop, 5% take target,
   10bps fee and 5bps slippage each direction. Decisions for simultaneous
   coin signals sort by higher prespecified probability (coin as tie breaker).
   Intrabar stop and take checks are conservatively STOP first; gaps trigger
   at the OPEN, which can be much worse than a stop; positions not previously
   closed exit at the original six-bar horizon close. Shared cash, same-bar
   exits, bounded entry count and risk flags are modeled. All positions
   are flat by the research end. The report includes equity curve, drawdown,
   realized PnL, win rate, profit factor, and each simulated fill.

The portfolio proxy **is not an exact historical replay of** the dynamically
selected, quote-driven Telegram paper bot. It has no historical exchange-wide
ranking, book depth, true spread, latency, daily funding, or intrabar price
path. Model labels for a fixed six-bar holding period do NOT match stop/take
outcomes, so the two experiment returns should NOT be directly compared
as though they were the same trade set.

### Research decision gate and holdout

The diagnostic gate flags fewer than 30 fixed-horizon selections, fewer than
30 completed portfolio trades, top-two-win concentration above 75%, and
nonpositive portfolio return. These are deliberately conservative screening
heuristics rather than validated significance tests. **Even if all passed,
paper automation and live trading remain DISABLED.**

Just like Stage 13, all ML fitting, probability scoring and simulated fills
are confined to development data. Bar timestamps from the reserved 20%
holdout are used only to identify the boundary. Tests mutate the holdout
prices and verify that Stage 14 results do not change.

The next genuine decision would require a *precommitted* candidate, fresh
unseen chronological validation and prospective paper observation; repeated
test/holdout tweaking is forbidden. Current development results have already
been inspected, so they are exploratory, not prospective evidence.
