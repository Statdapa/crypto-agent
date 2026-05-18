from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import sqlite3
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import feedparser
import requests
import schedule
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover
    Retry = None  # type: ignore[assignment]

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

try:
    from web3_upgrade import (
        web3_detect_and_answer,
        web3_eth_network_status,
        web3_get_eth_balance,
        web3_get_tokens,
        web3_get_transactions,
        web3_whale_watch,
    )
except Exception as import_error:  # pragma: no cover
    WEB3_IMPORT_ERROR = import_error

    def _web3_missing(*args: Any, **kwargs: Any) -> str:
        return f"Fitur Web3 belum tersedia: {WEB3_IMPORT_ERROR}"

    web3_get_eth_balance = _web3_missing
    web3_get_transactions = _web3_missing
    web3_get_tokens = _web3_missing
    web3_whale_watch = _web3_missing
    web3_eth_network_status = _web3_missing

    def web3_detect_and_answer(text: str) -> Optional[str]:
        return None
else:
    WEB3_IMPORT_ERROR = None


load_dotenv()


# ---------------------------------------------------------------------------
# 1. CONFIGURATION
# ---------------------------------------------------------------------------

def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class AppConfig:
    groq_api_key: str
    groq_model: str
    telegram_token: str
    telegram_default_chat_id: str
    okx_api_key: str
    okx_secret_key: str
    okx_passphrase: str
    okx_base_url: str
    okx_wallet_address: str
    okx_max_trade_usdt: float
    app_timezone_name: str
    db_path: str
    http_timeout: int
    default_coin: str
    market_coins: tuple[str, ...]
    snapshot_interval_minutes: int
    news_interval_minutes: int
    anomaly_interval_minutes: int
    telegram_poll_seconds: int
    send_startup_report: bool
    max_sql_rows: int
    anomaly_price_change_pct: float
    anomaly_volume_multiplier: float

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            groq_model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
            telegram_default_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            okx_api_key=os.getenv("OKX_API_KEY", ""),
            okx_secret_key=os.getenv("OKX_SECRET_KEY", ""),
            okx_passphrase=os.getenv("OKX_PASSPHRASE", ""),
            okx_base_url=os.getenv("OKX_BASE_URL", "https://www.okx.com").rstrip("/"),
            okx_wallet_address=os.getenv("OKX_WALLET_ADDRESS", ""),
            okx_max_trade_usdt=env_float("OKX_MAX_TRADE_USDT", 10.0),
            app_timezone_name=os.getenv("APP_TIMEZONE", "Asia/Jakarta"),
            db_path=os.getenv("DB_PATH", "crypto_intelligence_agent.db"),
            http_timeout=env_int("HTTP_TIMEOUT", 15),
            default_coin=os.getenv("DEFAULT_COIN", "BTC").upper().strip(),
            market_coins=tuple(env_list("MARKET_COINS", "BTC,ETH,SOL")),
            snapshot_interval_minutes=max(1, env_int("SNAPSHOT_INTERVAL_MINUTES", 15)),
            news_interval_minutes=max(1, env_int("NEWS_INTERVAL_MINUTES", 15)),
            anomaly_interval_minutes=max(1, env_int("ANOMALY_INTERVAL_MINUTES", 30)),
            telegram_poll_seconds=max(1, env_int("TELEGRAM_POLL_SECONDS", 3)),
            send_startup_report=env_bool("SEND_STARTUP_REPORT", False),
            max_sql_rows=max(10, env_int("MAX_SQL_ROWS", 100)),
            anomaly_price_change_pct=env_float("ANOMALY_PRICE_CHANGE_PCT", 4.0),
            anomaly_volume_multiplier=env_float("ANOMALY_VOLUME_MULTIPLIER", 2.5),
        )


CFG = AppConfig.from_env()
APP_TIMEZONE = ZoneInfo(CFG.app_timezone_name) if ZoneInfo else None
_llm: Optional[ChatGroq] = None


# ---------------------------------------------------------------------------
# 2. STATIC COIN CONFIGURATION
# ---------------------------------------------------------------------------

COINS: dict[str, dict[str, str]] = {
    "BTC": {"id": "bitcoin", "name": "Bitcoin", "tv": "BINANCE:BTCUSDT"},
    "ETH": {"id": "ethereum", "name": "Ethereum", "tv": "BINANCE:ETHUSDT"},
    "BNB": {"id": "binancecoin", "name": "BNB", "tv": "BINANCE:BNBUSDT"},
    "SOL": {"id": "solana", "name": "Solana", "tv": "BINANCE:SOLUSDT"},
    "XRP": {"id": "ripple", "name": "XRP", "tv": "BINANCE:XRPUSDT"},
    "ADA": {"id": "cardano", "name": "Cardano", "tv": "BINANCE:ADAUSDT"},
    "AVAX": {"id": "avalanche-2", "name": "Avalanche", "tv": "BINANCE:AVAXUSDT"},
    "DOT": {"id": "polkadot", "name": "Polkadot", "tv": "BINANCE:DOTUSDT"},
    "TRX": {"id": "tron", "name": "TRON", "tv": "BINANCE:TRXUSDT"},
    "LTC": {"id": "litecoin", "name": "Litecoin", "tv": "BINANCE:LTCUSDT"},
    "ATOM": {"id": "cosmos", "name": "Cosmos", "tv": "BINANCE:ATOMUSDT"},
    "NEAR": {"id": "near", "name": "NEAR Protocol", "tv": "BINANCE:NEARUSDT"},
    "APT": {"id": "aptos", "name": "Aptos", "tv": "BINANCE:APTUSDT"},
    "SUI": {"id": "sui", "name": "Sui", "tv": "BINANCE:SUIUSDT"},
    "TON": {"id": "the-open-network", "name": "Toncoin", "tv": "BINANCE:TONUSDT"},
    "ICP": {"id": "internet-computer", "name": "Internet Computer", "tv": "BINANCE:ICPUSDT"},
    "FIL": {"id": "filecoin", "name": "Filecoin", "tv": "BINANCE:FILUSDT"},
    "HBAR": {"id": "hedera-hashgraph", "name": "Hedera", "tv": "BINANCE:HBARUSDT"},
    "VET": {"id": "vechain", "name": "VeChain", "tv": "BINANCE:VETUSDT"},
    "XLM": {"id": "stellar", "name": "Stellar", "tv": "BINANCE:XLMUSDT"},
    "ALGO": {"id": "algorand", "name": "Algorand", "tv": "BINANCE:ALGOUSDT"},
    "ETC": {"id": "ethereum-classic", "name": "Ethereum Classic", "tv": "BINANCE:ETCUSDT"},
    "XMR": {"id": "monero", "name": "Monero", "tv": "BINANCE:XMRUSDT"},
    "DOGE": {"id": "dogecoin", "name": "Dogecoin", "tv": "BINANCE:DOGEUSDT"},
    "SHIB": {"id": "shiba-inu", "name": "Shiba Inu", "tv": "BINANCE:SHIBUSDT"},
    "PEPE": {"id": "pepe", "name": "Pepe", "tv": "BINANCE:PEPEUSDT"},
    "FLOKI": {"id": "floki", "name": "Floki", "tv": "BINANCE:FLOKIUSDT"},
    "BONK": {"id": "bonk", "name": "Bonk", "tv": "BINANCE:BONKUSDT"},
    "UNI": {"id": "uniswap", "name": "Uniswap", "tv": "BINANCE:UNIUSDT"},
    "LINK": {"id": "chainlink", "name": "Chainlink", "tv": "BINANCE:LINKUSDT"},
    "AAVE": {"id": "aave", "name": "Aave", "tv": "BINANCE:AAVEUSDT"},
    "MKR": {"id": "maker", "name": "Maker", "tv": "BINANCE:MKRUSDT"},
    "CRV": {"id": "curve-dao-token", "name": "Curve", "tv": "BINANCE:CRVUSDT"},
    "COMP": {"id": "compound-governance-token", "name": "Compound", "tv": "BINANCE:COMPUSDT"},
    "SNX": {"id": "synthetix-network-token", "name": "Synthetix", "tv": "BINANCE:SNXUSDT"},
    "LDO": {"id": "lido-dao", "name": "Lido DAO", "tv": "BINANCE:LDOUSDT"},
    "GMX": {"id": "gmx", "name": "GMX", "tv": "BINANCE:GMXUSDT"},
    "CAKE": {"id": "pancakeswap-token", "name": "PancakeSwap", "tv": "BINANCE:CAKEUSDT"},
    "ARB": {"id": "arbitrum", "name": "Arbitrum", "tv": "BINANCE:ARBUSDT"},
    "OP": {"id": "optimism", "name": "Optimism", "tv": "BINANCE:OPUSDT"},
    "MATIC": {"id": "matic-network", "name": "Polygon", "tv": "BINANCE:MATICUSDT"},
    "IMX": {"id": "immutable-x", "name": "Immutable X", "tv": "BINANCE:IMXUSDT"},
    "STRK": {"id": "starknet", "name": "Starknet", "tv": "BINANCE:STRKUSDT"},
    "ZK": {"id": "zksync", "name": "zkSync", "tv": "BINANCE:ZKUSDT"},
    "FET": {"id": "fetch-ai", "name": "Fetch.ai", "tv": "BINANCE:FETUSDT"},
    "RENDER": {"id": "render-token", "name": "Render", "tv": "BINANCE:RENDERUSDT"},
    "GRT": {"id": "the-graph", "name": "The Graph", "tv": "BINANCE:GRTUSDT"},
    "TAO": {"id": "bittensor", "name": "Bittensor", "tv": "BINANCE:TAOUSDT"},
    "AGIX": {"id": "singularitynet", "name": "SingularityNET", "tv": "BINANCE:AGIXUSDT"},
    "WLD": {"id": "worldcoin-wld", "name": "Worldcoin", "tv": "BINANCE:WLDUSDT"},
    "AXS": {"id": "axie-infinity", "name": "Axie Infinity", "tv": "BINANCE:AXSUSDT"},
    "SAND": {"id": "the-sandbox", "name": "The Sandbox", "tv": "BINANCE:SANDUSDT"},
    "MANA": {"id": "decentraland", "name": "Decentraland", "tv": "BINANCE:MANAUSDT"},
    "GALA": {"id": "gala", "name": "Gala", "tv": "BINANCE:GALAUSDT"},
    "ENJ": {"id": "enjincoin", "name": "Enjin Coin", "tv": "BINANCE:ENJUSDT"},
    "OKB": {"id": "okb", "name": "OKB", "tv": "OKX:OKBUSDT"},
    "CRO": {"id": "crypto-com-chain", "name": "Cronos", "tv": "BINANCE:CROUSDT"},
    "KCS": {"id": "kucoin-shares", "name": "KuCoin Token", "tv": "KUCOIN:KCSUSDT"},
    "INJ": {"id": "injective-protocol", "name": "Injective", "tv": "BINANCE:INJUSDT"},
    "SEI": {"id": "sei-network", "name": "Sei", "tv": "BINANCE:SEIUSDT"},
    "TIA": {"id": "celestia", "name": "Celestia", "tv": "BINANCE:TIAUSDT"},
    "JTO": {"id": "jito-governance-token", "name": "Jito", "tv": "BINANCE:JTOUSDT"},
    "PYTH": {"id": "pyth-network", "name": "Pyth Network", "tv": "BINANCE:PYTHUSDT"},
    "JUP": {"id": "jupiter-exchange-solana", "name": "Jupiter", "tv": "BINANCE:JUPUSDT"},
    "PENDLE": {"id": "pendle", "name": "Pendle", "tv": "BINANCE:PENDLEUSDT"},
    "ENA": {"id": "ethena", "name": "Ethena", "tv": "BINANCE:ENAUSDT"},
    "BB": {"id": "bouncebit", "name": "BounceBit", "tv": "BINANCE:BBUSDT"},
    "NOT": {"id": "notcoin", "name": "Notcoin", "tv": "BINANCE:NOTUSDT"},
    "LISTA": {"id": "lista-dao", "name": "Lista DAO", "tv": "BINANCE:LISTAUSDT"},
    "IO": {"id": "io-net", "name": "io.net", "tv": "BINANCE:IOUSDT"},
    "ZRO": {"id": "layerzero", "name": "LayerZero", "tv": "BINANCE:ZROUSDT"},
    "BOME": {"id": "book-of-meme", "name": "BOME", "tv": "BINANCE:BOMEUSDT"},
}

RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/feed",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
}

CRYPTO_KEYWORDS = {
    "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain", "halving",
    "mining", "etf", "sec", "bull", "bear", "rally", "dump", "defi",
    "altcoin", "exchange", "solana", "bnb", "wallet", "token", "stablecoin",
}


# ---------------------------------------------------------------------------
# 3. UTILITY LAYER
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def now_local() -> datetime:
    return datetime.now(APP_TIMEZONE) if APP_TIMEZONE else datetime.now()


def html_escape(value: Any) -> str:
    return html.escape(str(value), quote=False)


def normalize_coin(coin_symbol: str | None = None) -> str:
    coin = (coin_symbol or CFG.default_coin or "BTC").upper().strip()
    return coin if coin in COINS else (CFG.default_coin if CFG.default_coin in COINS else "BTC")


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def fmt_price(n: Any) -> str:
    if n is None or n == "N/A":
        return "N/A"
    try:
        value = float(n)
    except (TypeError, ValueError):
        return str(n)
    if abs(value) >= 1000:
        return f"${value:,.2f}"
    if abs(value) >= 1:
        return f"${value:.4f}"
    return f"${value:.8f}"


def fmt_num(n: Any) -> str:
    if n is None or n == "N/A":
        return "N/A"
    try:
        value = float(n)
    except (TypeError, ValueError):
        return str(n)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e12:
        return f"{sign}${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"{sign}${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"{sign}${value / 1e6:.2f}M"
    return f"{sign}${value:,.2f}"


def get_llm() -> ChatGroq:
    global _llm
    if _llm is None:
        if not CFG.groq_api_key:
            raise RuntimeError("GROQ_API_KEY belum diset di .env")
        _llm = ChatGroq(model=CFG.groq_model, api_key=CFG.groq_api_key)
    return _llm


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "crypto-intelligence-agent/2.0"})
    if Retry is not None:
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    return session


HTTP = make_session()


def safe_json_dict(response: requests.Response) -> dict[str, Any]:
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Response JSON tidak berbentuk object")
    return data


def safe_json_list(response: requests.Response) -> list[Any]:
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Response JSON tidak berbentuk list")
    return data


# ---------------------------------------------------------------------------
# 4. DATABASE AND STORAGE
# ---------------------------------------------------------------------------

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CFG.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = db_connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                link TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                summary TEXT,
                published_at TEXT,
                fetched_at TEXT NOT NULL,
                relevance_score REAL DEFAULT 0,
                sent_tg INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coin TEXT NOT NULL,
                price REAL NOT NULL,
                change_24h REAL,
                change_7d REAL,
                change_30d REAL,
                high_24h REAL,
                low_24h REAL,
                volume REAL,
                market_cap REAL,
                rsi REAL,
                ma7 REAL,
                ma14 REAL,
                ma20 REAL,
                macd REAL,
                macd_signal REAL,
                macd_hist REAL,
                bb_upper REAL,
                bb_mid REAL,
                bb_lower REAL,
                fg_value INTEGER,
                fg_label TEXT,
                signal TEXT,
                signal_strength INTEGER,
                score INTEGER,
                reasons_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS anomaly_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coin TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                evidence_json TEXT,
                created_at TEXT NOT NULL,
                sent_tg INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS intelligence_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL,
                coin TEXT,
                content TEXT NOT NULL,
                sources_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_news_fetched_at ON news(fetched_at);
            CREATE INDEX IF NOT EXISTS idx_market_coin_created ON market_snapshots(coin, created_at);
            CREATE INDEX IF NOT EXISTS idx_anomaly_coin_created ON anomaly_events(coin, created_at);
            """
        )
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS news_fts USING fts5(title, summary, source, content='news', content_rowid='id')"
            )
        except sqlite3.OperationalError:
            # Some Python builds do not include FTS5. LIKE search is used as fallback.
            pass
        conn.commit()
    finally:
        conn.close()


def set_state(key: str, value: str) -> None:
    conn = db_connect()
    try:
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, utc_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_state(key: str, default: str = "") -> str:
    conn = db_connect()
    try:
        row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. MARKET DATA AND INDICATORS
# ---------------------------------------------------------------------------

def get_price(coin_symbol: str = "BTC") -> Optional[dict[str, Any]]:
    coin_symbol = normalize_coin(coin_symbol)
    coin_id = COINS[coin_symbol]["id"]
    try:
        response = HTTP.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
            timeout=CFG.http_timeout,
        )
        data = safe_json_dict(response)
        md = data.get("market_data")
        if not isinstance(md, dict):
            raise ValueError("market_data tidak tersedia")

        current_price = md.get("current_price", {}).get("usd")
        if current_price is None:
            raise ValueError("Harga USD tidak tersedia")

        return {
            "coin": coin_symbol,
            "price": float(current_price),
            "change_24h": float(md.get("price_change_percentage_24h") or 0),
            "change_7d": float(md.get("price_change_percentage_7d") or 0),
            "change_30d": float(md.get("price_change_percentage_30d") or 0),
            "high_24h": float(md.get("high_24h", {}).get("usd") or 0),
            "low_24h": float(md.get("low_24h", {}).get("usd") or 0),
            "volume": float(md.get("total_volume", {}).get("usd") or 0),
            "market_cap": float(md.get("market_cap", {}).get("usd") or 0),
            "ath": float(md.get("ath", {}).get("usd") or 0),
            "ath_change": float(md.get("ath_change_percentage", {}).get("usd") or 0),
            "fetched_at": utc_iso(),
        }
    except Exception as exc:
        print(f"Error get_price {coin_symbol}: {exc}")
        return None


def get_fear_greed() -> dict[str, Any]:
    try:
        response = HTTP.get("https://api.alternative.me/fng/?limit=1", timeout=CFG.http_timeout)
        data = safe_json_dict(response)
        items = data.get("data")
        if not isinstance(items, list) or not items:
            raise ValueError("Fear & Greed data kosong")
        item = items[0]
        return {
            "value": int(item.get("value", 50)),
            "label": str(item.get("value_classification", "Neutral")),
            "fetched_at": utc_iso(),
        }
    except Exception as exc:
        print(f"Error get_fear_greed: {exc}")
        return {"value": 50, "label": "Neutral", "fetched_at": utc_iso()}


def get_ohlc(coin_symbol: str = "BTC", days: int = 30) -> list[list[Any]]:
    coin_symbol = normalize_coin(coin_symbol)
    coin_id = COINS[coin_symbol]["id"]
    try:
        response = HTTP.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc",
            params={"vs_currency": "usd", "days": str(days)},
            timeout=CFG.http_timeout,
        )
        data = safe_json_list(response)
        return [row for row in data if isinstance(row, list) and len(row) >= 5]
    except Exception as exc:
        print(f"Error get_ohlc {coin_symbol}: {exc}")
        return []


def calculate_ma(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calculate_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_macd(closes: list[float]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < 35:
        return None, None, None

    def ema_series(values: list[float], period: int) -> list[float]:
        k = 2 / (period + 1)
        result = [values[0]]
        for price in values[1:]:
            result.append(price * k + result[-1] * (1 - k))
        return result

    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd_series = [a - b for a, b in zip(ema12[-len(ema26):], ema26)]
    signal_series = ema_series(macd_series, 9)
    macd_line = macd_series[-1]
    signal_line = signal_series[-1]
    hist = macd_line - signal_line
    return round(macd_line, 4), round(signal_line, 4), round(hist, 4)


def calculate_bollinger(closes: list[float], period: int = 20, std_dev: int = 2) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    ma = sum(window) / period
    variance = sum((x - ma) ** 2 for x in window) / period
    std = variance ** 0.5
    return round(ma + std_dev * std, 4), round(ma, 4), round(ma - std_dev * std, 4)


def get_analysis(coin_symbol: str = "BTC") -> dict[str, Any]:
    ohlc = get_ohlc(coin_symbol, days=30)
    default = {
        "ma7": "N/A", "ma14": "N/A", "ma20": "N/A", "rsi": "N/A",
        "macd": "N/A", "macd_signal": "N/A", "macd_hist": "N/A",
        "bb_upper": "N/A", "bb_mid": "N/A", "bb_lower": "N/A",
    }
    if not ohlc:
        return default
    try:
        closes = [float(candle[4]) for candle in ohlc]
    except Exception as exc:
        print(f"Error parsing OHLC {coin_symbol}: {exc}")
        return default

    ma7 = calculate_ma(closes, 7)
    ma14 = calculate_ma(closes, 14)
    ma20 = calculate_ma(closes, 20)
    rsi = calculate_rsi(closes)
    macd_line, macd_signal, macd_hist = calculate_macd(closes)
    bb_upper, bb_mid, bb_lower = calculate_bollinger(closes, period=20)
    return {
        "ma7": round(ma7, 4) if ma7 is not None else "N/A",
        "ma14": round(ma14, 4) if ma14 is not None else "N/A",
        "ma20": round(ma20, 4) if ma20 is not None else "N/A",
        "rsi": rsi if rsi is not None else "N/A",
        "macd": macd_line if macd_line is not None else "N/A",
        "macd_signal": macd_signal if macd_signal is not None else "N/A",
        "macd_hist": macd_hist if macd_hist is not None else "N/A",
        "bb_upper": bb_upper if bb_upper is not None else "N/A",
        "bb_mid": bb_mid if bb_mid is not None else "N/A",
        "bb_lower": bb_lower if bb_lower is not None else "N/A",
    }


# ---------------------------------------------------------------------------
# 6. SIGNAL, STORAGE, AND ANOMALY ENGINE
# ---------------------------------------------------------------------------

def generate_signal(price_data: dict[str, Any], analysis: dict[str, Any], fg: dict[str, Any]) -> dict[str, Any]:
    price = price_data["price"]
    rsi = analysis["rsi"]
    macd = analysis["macd"]
    macd_sig = analysis["macd_signal"]
    bb_up = analysis["bb_upper"]
    bb_low = analysis["bb_lower"]
    ma7 = analysis["ma7"]
    ma14 = analysis["ma14"]
    ch24 = float(price_data.get("change_24h", 0) or 0)
    fg_val = int(fg.get("value", 50))
    score = 0
    reasons: list[str] = []

    if is_number(rsi):
        if rsi < 30:
            score += 3
            reasons.append("RSI oversold: +3")
        elif rsi < 45:
            score += 1
            reasons.append("RSI weak/discounted: +1")
        elif rsi > 70:
            score -= 3
            reasons.append("RSI overbought: -3")
        elif rsi > 60:
            score -= 1
            reasons.append("RSI heated: -1")

    if is_number(macd) and is_number(macd_sig):
        if macd > macd_sig:
            score += 2
            reasons.append("MACD bullish crossover: +2")
        else:
            score -= 2
            reasons.append("MACD bearish crossover: -2")

    if is_number(bb_up) and is_number(bb_low) and is_number(price):
        if price < bb_low:
            score += 2
            reasons.append("Price below lower Bollinger Band: +2")
        elif price > bb_up:
            score -= 2
            reasons.append("Price above upper Bollinger Band: -2")

    if is_number(ma7) and is_number(ma14):
        if ma7 > ma14:
            score += 1
            reasons.append("MA7 above MA14: +1")
        else:
            score -= 1
            reasons.append("MA7 below MA14: -1")

    if ch24 > 3:
        score += 1
        reasons.append("24h momentum positive: +1")
    elif ch24 < -3:
        score -= 1
        reasons.append("24h momentum negative: -1")

    if fg_val < 25:
        score += 2
        reasons.append("Extreme fear contrarian signal: +2")
    elif fg_val > 75:
        score -= 2
        reasons.append("Extreme greed risk signal: -2")

    if score >= 4:
        signal = "BUY"
        signal_strength = min(90, 55 + score * 4)
    elif score <= -4:
        signal = "SELL"
        signal_strength = min(90, 55 + abs(score) * 4)
    else:
        signal = "HOLD"
        signal_strength = max(40, 70 - abs(score) * 5)

    if signal == "BUY":
        entry_low = round(price * 0.99, 8)
        entry_high = round(price * 1.005, 8)
        target = round(price * 1.05, 8)
        stop_loss = round(price * 0.97, 8)
    elif signal == "SELL":
        entry_low = round(price * 0.995, 8)
        entry_high = round(price * 1.01, 8)
        target = round(price * 0.95, 8)
        stop_loss = round(price * 1.03, 8)
    else:
        entry_low = round(price * 0.98, 8)
        entry_high = round(price * 1.02, 8)
        target = round(price * 1.04, 8)
        stop_loss = round(price * 0.96, 8)

    return {
        "signal": signal,
        "signal_strength": int(signal_strength),
        "confidence": int(signal_strength),  # backward-compatible alias; not statistical confidence.
        "score": int(score),
        "reasons": reasons,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "target": target,
        "stop_loss": stop_loss,
    }


def to_db_number(value: Any) -> Optional[float]:
    return float(value) if is_number(value) else None


def save_market_snapshot(coin_symbol: str, price_data: dict[str, Any], analysis: dict[str, Any], fg: dict[str, Any], signal_data: dict[str, Any]) -> None:
    conn = db_connect()
    try:
        conn.execute(
            """
            INSERT INTO market_snapshots (
                coin, price, change_24h, change_7d, change_30d, high_24h, low_24h,
                volume, market_cap, rsi, ma7, ma14, ma20, macd, macd_signal,
                macd_hist, bb_upper, bb_mid, bb_lower, fg_value, fg_label, signal,
                signal_strength, score, reasons_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalize_coin(coin_symbol),
                price_data["price"],
                price_data.get("change_24h"),
                price_data.get("change_7d"),
                price_data.get("change_30d"),
                price_data.get("high_24h"),
                price_data.get("low_24h"),
                price_data.get("volume"),
                price_data.get("market_cap"),
                to_db_number(analysis.get("rsi")),
                to_db_number(analysis.get("ma7")),
                to_db_number(analysis.get("ma14")),
                to_db_number(analysis.get("ma20")),
                to_db_number(analysis.get("macd")),
                to_db_number(analysis.get("macd_signal")),
                to_db_number(analysis.get("macd_hist")),
                to_db_number(analysis.get("bb_upper")),
                to_db_number(analysis.get("bb_mid")),
                to_db_number(analysis.get("bb_lower")),
                int(fg.get("value", 50)),
                str(fg.get("label", "Neutral")),
                signal_data.get("signal"),
                int(signal_data.get("signal_strength", signal_data.get("confidence", 0))),
                int(signal_data.get("score", 0)),
                json.dumps(signal_data.get("reasons", []), ensure_ascii=False),
                utc_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_snapshots(coin_symbol: str, limit: int = 20) -> list[sqlite3.Row]:
    conn = db_connect()
    try:
        return conn.execute(
            """
            SELECT * FROM market_snapshots
            WHERE coin=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (normalize_coin(coin_symbol), limit),
        ).fetchall()
    finally:
        conn.close()


def store_anomaly(coin: str, severity: str, title: str, description: str, evidence: dict[str, Any]) -> bool:
    coin = normalize_coin(coin)
    conn = db_connect()
    try:
        cutoff = (utc_now() - timedelta(hours=2)).replace(microsecond=0).isoformat()
        duplicate = conn.execute(
            """
            SELECT id FROM anomaly_events
            WHERE coin=? AND title=? AND created_at>?
            LIMIT 1
            """,
            (coin, title, cutoff),
        ).fetchone()
        if duplicate:
            return False
        conn.execute(
            """
            INSERT INTO anomaly_events (coin, severity, title, description, evidence_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (coin, severity, title, description, json.dumps(evidence, ensure_ascii=False), utc_iso()),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def detect_market_anomalies(coin_symbol: str) -> list[dict[str, Any]]:
    coin_symbol = normalize_coin(coin_symbol)
    rows = get_recent_snapshots(coin_symbol, limit=30)
    if not rows:
        return []

    latest = rows[0]
    anomalies: list[dict[str, Any]] = []
    price_change = float(latest["change_24h"] or 0)
    rsi = latest["rsi"]
    fg_value = latest["fg_value"]
    volume = float(latest["volume"] or 0)

    if abs(price_change) >= CFG.anomaly_price_change_pct:
        direction = "up" if price_change > 0 else "down"
        anomalies.append({
            "coin": coin_symbol,
            "severity": "HIGH" if abs(price_change) >= CFG.anomaly_price_change_pct * 1.8 else "MEDIUM",
            "title": f"{coin_symbol} abnormal 24h price move",
            "description": f"{coin_symbol} moved {price_change:+.2f}% over 24h, which exceeds the configured anomaly threshold.",
            "evidence": {"change_24h": price_change, "direction": direction, "threshold": CFG.anomaly_price_change_pct},
        })

    if is_number(rsi) and (rsi >= 75 or rsi <= 25):
        anomalies.append({
            "coin": coin_symbol,
            "severity": "MEDIUM",
            "title": f"{coin_symbol} RSI extreme",
            "description": f"RSI is {rsi:.2f}, indicating an extreme momentum condition.",
            "evidence": {"rsi": rsi},
        })

    if isinstance(fg_value, int) and (fg_value >= 80 or fg_value <= 20):
        anomalies.append({
            "coin": coin_symbol,
            "severity": "MEDIUM",
            "title": "Market sentiment extreme",
            "description": f"Fear & Greed is {fg_value}/100, indicating an extreme sentiment regime.",
            "evidence": {"fg_value": fg_value},
        })

    historical_volumes = [float(r["volume"] or 0) for r in rows[1:] if float(r["volume"] or 0) > 0]
    if volume > 0 and len(historical_volumes) >= 5:
        avg_volume = statistics.mean(historical_volumes)
        if avg_volume > 0 and volume >= avg_volume * CFG.anomaly_volume_multiplier:
            anomalies.append({
                "coin": coin_symbol,
                "severity": "HIGH",
                "title": f"{coin_symbol} volume spike",
                "description": f"Volume is {volume:,.0f}, about {volume / avg_volume:.2f}x recent average.",
                "evidence": {"volume": volume, "avg_volume": avg_volume, "multiplier": volume / avg_volume},
            })

    stored: list[dict[str, Any]] = []
    for item in anomalies:
        if store_anomaly(item["coin"], item["severity"], item["title"], item["description"], item["evidence"]):
            stored.append(item)
    return stored


# ---------------------------------------------------------------------------
# 7. NEWS PIPELINE AND LIGHTWEIGHT RAG
# ---------------------------------------------------------------------------

def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def parse_feed_time(entry: Any) -> Optional[str]:
    for key in ("published", "updated", "created"):
        value = entry.get(key)
        if value:
            try:
                dt = parsedate_to_datetime(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
            except Exception:
                continue
    return None


def relevance_score(title: str, summary: str) -> float:
    text = f"{title} {summary}".lower()
    keyword_hits = sum(1 for kw in CRYPTO_KEYWORDS if kw in text)
    score = float(keyword_hits)
    if any(word in text for word in ("hack", "exploit", "sec", "etf", "lawsuit", "stablecoin", "whale")):
        score += 2.0
    return score


def fetch_rss_news(max_per_feed: int = 5) -> list[dict[str, Any]]:
    all_news: list[dict[str, Any]] = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            count = 0
            for entry in feed.entries:
                if count >= max_per_feed:
                    break
                title = strip_html(entry.get("title", ""))
                link = str(entry.get("link", "")).strip()
                summary = strip_html(entry.get("summary", entry.get("description", "")))[:800]
                if not title or not link:
                    continue
                score = relevance_score(title, summary)
                if score <= 0:
                    continue
                all_news.append({
                    "title": title,
                    "link": link,
                    "source": source,
                    "summary": summary,
                    "published_at": parse_feed_time(entry),
                    "fetched_at": utc_iso(),
                    "relevance_score": score,
                })
                count += 1
        except Exception as exc:
            print(f"RSS {source} gagal: {exc}")
    return all_news


def save_news_dedupe(news_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conn = db_connect()
    new_articles: list[dict[str, Any]] = []
    try:
        has_fts = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='news_fts'"
        ).fetchone() is not None
        for art in news_list:
            try:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO news (title, link, source, summary, published_at, fetched_at, relevance_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        art["title"], art["link"], art["source"], art.get("summary"),
                        art.get("published_at"), art.get("fetched_at", utc_iso()), art.get("relevance_score", 0),
                    ),
                )
                if cur.rowcount > 0:
                    row_id = cur.lastrowid
                    if has_fts:
                        conn.execute(
                            "INSERT INTO news_fts(rowid, title, summary, source) VALUES (?, ?, ?, ?)",
                            (row_id, art["title"], art.get("summary", ""), art["source"]),
                        )
                    new_articles.append(art)
            except Exception as exc:
                print(f"DB insert news gagal: {exc}")
        conn.commit()
    finally:
        conn.close()
    return new_articles


def search_news(query: str, limit: int = 5) -> list[sqlite3.Row]:
    clean = re.sub(r"[^a-zA-Z0-9_\s-]", " ", query).strip()
    conn = db_connect()
    try:
        has_fts = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='news_fts'"
        ).fetchone() is not None
        if has_fts and clean:
            fts_query = " OR ".join([token for token in clean.split()[:8] if len(token) > 1])
            if fts_query:
                return conn.execute(
                    """
                    SELECT n.* FROM news_fts f
                    JOIN news n ON n.id = f.rowid
                    WHERE news_fts MATCH ?
                    ORDER BY n.relevance_score DESC, n.fetched_at DESC
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
        like = f"%{clean}%" if clean else "%"
        return conn.execute(
            """
            SELECT * FROM news
            WHERE title LIKE ? OR summary LIKE ? OR source LIKE ?
            ORDER BY relevance_score DESC, fetched_at DESC
            LIMIT ?
            """,
            (like, like, like, limit),
        ).fetchall()
    finally:
        conn.close()


def get_news_for_telegram(top_n: int = 5) -> str:
    raw = fetch_rss_news(max_per_feed=5)
    if raw:
        new_articles = save_news_dedupe(raw)
    else:
        new_articles = []

    articles = new_articles[:top_n]
    if not articles:
        conn = db_connect()
        try:
            rows = conn.execute(
                "SELECT title, link, source, summary FROM news ORDER BY fetched_at DESC LIMIT ?",
                (top_n,),
            ).fetchall()
        finally:
            conn.close()
        articles = [dict(row) for row in rows]

    if not articles:
        return "Tidak ada berita crypto tersimpan saat ini."

    lines = ["<b>Crypto Intelligence Brief</b>", html_escape(now_local().strftime("%d %b %Y, %H:%M")), ""]
    for i, art in enumerate(articles[:top_n], 1):
        title = html_escape(art["title"])
        source = html_escape(art.get("source", "Unknown"))
        summary = html_escape(strip_html(art.get("summary", ""))[:180])
        link = html_escape(art.get("link", ""))
        lines.append(f"<b>{i}. {title}</b>")
        if summary:
            lines.append(summary)
        lines.append(f"Source: <a href=\"{link}\">{source}</a>")
        lines.append("")
    return "\n".join(lines).strip()


def check_and_push_news() -> None:
    raw = fetch_rss_news(max_per_feed=5)
    if not raw:
        return
    new_articles = save_news_dedupe(raw)
    if not new_articles:
        print("Tidak ada berita baru")
        return
    msg = get_news_for_telegram(top_n=min(5, len(new_articles)))
    send_telegram(msg)


# ---------------------------------------------------------------------------
# 8. INTELLIGENCE REPORTING AND TELEGRAM FORMATTING
# ---------------------------------------------------------------------------

def get_ai_commentary(coin_symbol: str, price_data: dict[str, Any], analysis: dict[str, Any], fg: dict[str, Any], signal_data: dict[str, Any]) -> str:
    price = price_data["price"]
    reasons = "; ".join(signal_data.get("reasons", [])) or "No rule explanation available"
    prompt = f"""You are a serious Indonesian crypto intelligence analyst.
Do not overclaim. Do not call this statistical confidence.
Write max 3 concise Indonesian sentences.

Coin: {coin_symbol}/USDT
Price: ${price:,.8f}
Rule-based signal: {signal_data['signal']}
Signal strength: {signal_data['signal_strength']}/100
Score reasons: {reasons}
RSI: {analysis['rsi']}
MACD: {analysis['macd']} vs signal {analysis['macd_signal']}
24h change: {price_data['change_24h']:.2f}%
7d change: {price_data['change_7d']:.2f}%
Fear & Greed: {fg['value']} ({fg['label']})
Entry zone: ${signal_data['entry_low']:,.8f} - ${signal_data['entry_high']:,.8f}
Target: ${signal_data['target']:,.8f}
Stop loss: ${signal_data['stop_loss']:,.8f}
"""
    try:
        response = get_llm().invoke([HumanMessage(content=prompt)])
        return str(response.content).strip()
    except Exception as exc:
        return f"Analisis AI tidak tersedia: {exc}"


def build_signal_message(coin_symbol: str, price_data: dict[str, Any], analysis: dict[str, Any], fg: dict[str, Any], signal_data: dict[str, Any], ai_comment: str) -> str:
    coin_symbol = normalize_coin(coin_symbol)
    tv_link = COINS.get(coin_symbol, {}).get("tv", "BINANCE:BTCUSDT")
    tv_url = f"https://www.tradingview.com/chart/?symbol={tv_link}"
    reasons = signal_data.get("reasons", [])
    reasons_text = "\n".join([f"- {html_escape(r)}" for r in reasons[:8]]) or "- No rule explanation available"

    return f"""
<b>Crypto Intelligence Agent - {html_escape(coin_symbol)}/USDT</b>
{html_escape(now_local().strftime('%d %b %Y, %H:%M'))} {html_escape(CFG.app_timezone_name)}

<b>Market Snapshot</b>
Price: <b>{html_escape(fmt_price(price_data['price']))}</b>
24h: {price_data['change_24h']:+.2f}%
7d: {price_data['change_7d']:+.2f}%
Volume: {html_escape(fmt_num(price_data['volume']))}
Market cap: {html_escape(fmt_num(price_data['market_cap']))}

<b>Technical Indicators</b>
RSI(14): {html_escape(analysis['rsi'])}
MACD: {html_escape(analysis['macd'])} | Signal: {html_escape(analysis['macd_signal'])}
MA7: {html_escape(fmt_price(analysis['ma7']))}
MA14: {html_escape(fmt_price(analysis['ma14']))}
Bollinger Upper: {html_escape(fmt_price(analysis['bb_upper']))}
Bollinger Lower: {html_escape(fmt_price(analysis['bb_lower']))}

<b>Sentiment</b>
Fear & Greed: {html_escape(fg['value'])}/100 - {html_escape(fg['label'])}

<b>Rule-Based Signal</b>
Signal: <b>{html_escape(signal_data['signal'])}</b>
Signal strength: {html_escape(signal_data['signal_strength'])}/100
Score: {html_escape(signal_data['score'])}

<b>Rule Explanation</b>
{reasons_text}

<b>Levels</b>
Entry zone: {html_escape(fmt_price(signal_data['entry_low']))} - {html_escape(fmt_price(signal_data['entry_high']))}
Target: {html_escape(fmt_price(signal_data['target']))}
Stop loss: {html_escape(fmt_price(signal_data['stop_loss']))}

<b>AI Commentary</b>
<i>{html_escape(ai_comment)}</i>

<a href="{html_escape(tv_url)}">Open TradingView Chart</a>

<i>Not financial advice. This is intelligence support, not guaranteed prediction.</i>
""".strip()


def format_status_message(coin_symbol: str, price_data: dict[str, Any], analysis: dict[str, Any], fg: dict[str, Any], signal_data: dict[str, Any]) -> str:
    return f"""
<b>Status {html_escape(coin_symbol)}/USDT</b>
Price: {html_escape(fmt_price(price_data['price']))} ({price_data['change_24h']:+.2f}%)
RSI: {html_escape(analysis['rsi'])} | MACD: {html_escape(analysis['macd'])}
MA7: {html_escape(fmt_price(analysis['ma7']))} | MA14: {html_escape(fmt_price(analysis['ma14']))}
Fear & Greed: {html_escape(fg['value'])} - {html_escape(fg['label'])}
Signal: <b>{html_escape(signal_data['signal'])}</b> ({html_escape(signal_data['signal_strength'])}/100 strength)
Entry: {html_escape(fmt_price(signal_data['entry_low']))} - {html_escape(fmt_price(signal_data['entry_high']))}
""".strip()


def save_intelligence_report(report_type: str, coin: Optional[str], content: str, sources: list[dict[str, Any]]) -> None:
    conn = db_connect()
    try:
        conn.execute(
            """
            INSERT INTO intelligence_reports (report_type, coin, content, sources_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (report_type, coin, content, json.dumps(sources, ensure_ascii=False), utc_iso()),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 9. ANALYTICS AGENT
# ---------------------------------------------------------------------------

def extract_coins_from_text(text: str) -> list[str]:
    upper = text.upper()
    found = []
    for sym in COINS:
        if re.search(rf"\b{re.escape(sym)}\b", upper):
            found.append(sym)
    return found or [normalize_coin()]


def parse_coin_from_text(text: str) -> str:
    coins = extract_coins_from_text(text)
    return coins[0] if coins else normalize_coin()


def latest_snapshot_summary(coin: str) -> str:
    rows = get_recent_snapshots(coin, limit=1)
    if not rows:
        return f"No stored market snapshot for {coin}."
    r = rows[0]
    return (
        f"{coin}: price={r['price']}, change_24h={r['change_24h']}, rsi={r['rsi']}, "
        f"macd={r['macd']}, signal={r['signal']}, signal_strength={r['signal_strength']}, "
        f"score={r['score']}, fg={r['fg_value']} ({r['fg_label']}), created_at={r['created_at']}"
    )


def compare_coins(coins: list[str]) -> str:
    rows: list[str] = []
    for coin in coins[:6]:
        recent = get_recent_snapshots(coin, limit=1)
        if not recent:
            rows.append(f"- {coin}: no stored snapshot yet")
            continue
        r = recent[0]
        rows.append(
            f"- {coin}: price {fmt_price(r['price'])}, 24h {float(r['change_24h'] or 0):+.2f}%, "
            f"RSI {r['rsi']}, signal {r['signal']} ({r['signal_strength']}/100), score {r['score']}"
        )
    return "<b>Coin Comparison</b>\n" + "\n".join(html_escape(line) for line in rows)


def recent_anomalies(limit: int = 8) -> str:
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT * FROM anomaly_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return "Belum ada anomaly event tersimpan. Jalankan /refresh atau tunggu scheduler berjalan."
    lines = ["<b>Recent Anomaly Events</b>"]
    for r in rows:
        lines.append(
            f"- <b>{html_escape(r['severity'])}</b> {html_escape(r['coin'])}: "
            f"{html_escape(r['title'])} - {html_escape(r['description'])}"
        )
    return "\n".join(lines)


def answer_intelligence_question(question: str) -> str:
    lower = question.lower()
    coins = extract_coins_from_text(question)

    if any(word in lower for word in ("compare", "bandingkan", "perbandingan")):
        return compare_coins(coins)

    if any(word in lower for word in ("anomaly", "anomali", "abnormal", "alert")):
        return recent_anomalies()

    if any(word in lower for word in ("news", "berita", "headline")):
        articles = search_news(question, limit=5)
        if not articles:
            return "Belum ada berita relevan di database. Jalankan /berita untuk ingest RSS terbaru."
        lines = ["<b>Relevant News</b>"]
        for art in articles:
            lines.append(f"- <a href=\"{html_escape(art['link'])}\">{html_escape(art['title'])}</a> ({html_escape(art['source'])})")
        return "\n".join(lines)

    market_context = "\n".join(latest_snapshot_summary(c) for c in coins[:4])
    news_rows = search_news(question + " " + " ".join(coins), limit=5)
    news_context = "\n".join(
        f"[{i+1}] {row['source']} - {row['title']} - {row['summary'][:300]} - {row['link']}"
        for i, row in enumerate(news_rows)
    ) or "No relevant news found in local database."

    prompt = f"""You are a strict data intelligence assistant for crypto market monitoring.
Answer in Indonesian unless the user clearly uses English.
Use only the provided local context. If evidence is weak, say so.
Do not invent prices, news, or trades.

Question: {question}

Market context:
{market_context}

Retrieved news context:
{news_context}

Return:
1. Direct answer
2. Evidence used
3. Limitation / what data is missing
"""
    try:
        response = get_llm().invoke([HumanMessage(content=prompt)])
        content = str(response.content).strip()
    except Exception as exc:
        content = f"AI analytics tidak tersedia: {exc}\n\nMarket context:\n{market_context}"

    sources = [dict(row) for row in news_rows]
    save_intelligence_report("qa", coins[0] if coins else None, content, sources)
    return html_escape(content).replace("\n", "\n")


# ---------------------------------------------------------------------------
# 10. OKX AND WEB3 STATUS
# ---------------------------------------------------------------------------

def okx_sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    msg = f"{timestamp}{method.upper()}{path}{body}"
    mac = hmac.new(CFG.okx_secret_key.encode(), msg.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def okx_headers(method: str, path: str, body: str = "") -> dict[str, str]:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "OK-ACCESS-KEY": CFG.okx_api_key,
        "OK-ACCESS-SIGN": okx_sign(timestamp, method, path, body),
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": CFG.okx_passphrase,
        "Content-Type": "application/json",
    }


def okx_get_balance() -> Optional[dict[str, float]]:
    if not (CFG.okx_api_key and CFG.okx_secret_key and CFG.okx_passphrase):
        return None
    try:
        path = "/api/v5/account/balance"
        response = HTTP.get(CFG.okx_base_url + path, headers=okx_headers("GET", path), timeout=CFG.http_timeout)
        data = safe_json_dict(response)
        if data.get("code") == "0":
            balances: dict[str, float] = {}
            for item in data.get("data", [{}])[0].get("details", []):
                amount = float(item.get("cashBal", 0) or 0)
                if amount > 0:
                    balances[str(item.get("ccy", "UNKNOWN"))] = amount
            return balances
        print(f"OKX balance API error: {data}")
        return None
    except Exception as exc:
        print(f"OKX balance error: {exc}")
        return None


def okx_check_portfolio_dex() -> str:
    if not CFG.okx_wallet_address:
        return "Set OKX_WALLET_ADDRESS di .env dulu."
    try:
        params = {"address": CFG.okx_wallet_address, "chains": "501"}
        response = HTTP.get(
            f"{CFG.okx_base_url}/api/v5/dex/balance/token-balances-by-address",
            params=params,
            headers={"OK-ACCESS-KEY": CFG.okx_api_key, "Content-Type": "application/json"},
            timeout=CFG.http_timeout,
        )
        data = safe_json_dict(response)
        if data.get("code") == "0" and data.get("data"):
            tokens = data["data"][0].get("tokenAssets", [])
            result: list[str] = []
            for token in tokens[:15]:
                balance = float(token.get("balance", 0) or 0)
                if balance > 0:
                    price = float(token.get("tokenPrice", 0) or 0)
                    symbol = html_escape(token.get("symbol", "UNKNOWN"))
                    result.append(f"- {symbol}: {balance:.6f} (approx ${price * balance:.2f})")
            return "\n".join(result) if result else "Wallet kosong atau token belum terbaca."
        return f"Gagal ambil data portfolio: {html_escape(data)}"
    except Exception as exc:
        return f"Error: {html_escape(exc)}"


def format_okx_status() -> str:
    lines = ["<b>OKX Status</b>"]
    if CFG.okx_api_key:
        balances = okx_get_balance()
        if balances:
            lines.append("\n<b>Balance CEX</b>")
            for ccy, amount in list(balances.items())[:8]:
                lines.append(f"- {html_escape(ccy)}: {amount:.6f}")
        else:
            lines.append("\nGagal ambil balance CEX. Cek OKX_API_KEY, OKX_SECRET_KEY, dan OKX_PASSPHRASE di .env.")
    else:
        lines.append("\nOKX_API_KEY belum diset di .env.")

    lines.append("\n<b>Portfolio DEX / Wallet</b>")
    lines.append(okx_check_portfolio_dex())
    if CFG.okx_wallet_address:
        short = CFG.okx_wallet_address[:6] + "..." + CFG.okx_wallet_address[-4:]
        lines.append(f"\nWallet: <code>{html_escape(short)}</code>")
    else:
        lines.append("\nWallet address belum diset di .env.")
    lines.append("\n<i>Not financial advice.</i>")
    return "\n".join(lines)


def okx_register_competition() -> str:
    if not CFG.okx_wallet_address:
        return "Set OKX_WALLET_ADDRESS di .env dulu."
    short = CFG.okx_wallet_address[:8] + "..." + CFG.okx_wallet_address[-4:]
    return f"""
<b>Cara register kompetisi OKX</b>
1. Buka halaman kompetisi OKX Agentic Trading.
2. Klik Join competition.
3. Connect OKX Wallet.
4. Copy prompt registrasi dari Step 02.
5. Paste ke agent atau jalankan via tool yang diminta kompetisi.

Wallet: <code>{html_escape(short)}</code>
""".strip()


# ---------------------------------------------------------------------------
# 11. CORE WORKFLOWS
# ---------------------------------------------------------------------------

def run_full_analysis(coin_symbol: str) -> str:
    coin_symbol = normalize_coin(coin_symbol)
    price_data = get_price(coin_symbol)
    if not price_data:
        return f"Gagal ambil data {html_escape(coin_symbol)}. Coba lagi nanti."
    fg = get_fear_greed()
    analysis = get_analysis(coin_symbol)
    signal_data = generate_signal(price_data, analysis, fg)
    ai_comment = get_ai_commentary(coin_symbol, price_data, analysis, fg, signal_data)
    save_market_snapshot(coin_symbol, price_data, analysis, fg, signal_data)
    return build_signal_message(coin_symbol, price_data, analysis, fg, signal_data, ai_comment)


def auto_report(coin_symbol: str = "BTC") -> None:
    coin_symbol = normalize_coin(coin_symbol)
    print(f"Auto report {coin_symbol}...")
    price_data = get_price(coin_symbol)
    if not price_data:
        return
    fg = get_fear_greed()
    analysis = get_analysis(coin_symbol)
    signal_data = generate_signal(price_data, analysis, fg)
    ai_comment = get_ai_commentary(coin_symbol, price_data, analysis, fg, signal_data)
    save_market_snapshot(coin_symbol, price_data, analysis, fg, signal_data)
    anomalies = detect_market_anomalies(coin_symbol)

    should_alert = (
        (signal_data["signal"] in ("BUY", "SELL") and signal_data["signal_strength"] >= 75)
        or bool(anomalies)
    )
    if should_alert:
        send_telegram(build_signal_message(coin_symbol, price_data, analysis, fg, signal_data, ai_comment))
        for anomaly in anomalies:
            send_telegram(format_anomaly_message(anomaly))
    else:
        print(f"No alert for {coin_symbol}: {signal_data['signal']} {signal_data['signal_strength']}/100")


def format_anomaly_message(anomaly: dict[str, Any]) -> str:
    return f"""
<b>Anomaly Alert - {html_escape(anomaly['coin'])}</b>
Severity: <b>{html_escape(anomaly['severity'])}</b>
Title: {html_escape(anomaly['title'])}

{html_escape(anomaly['description'])}

Evidence: <code>{html_escape(json.dumps(anomaly.get('evidence', {}), ensure_ascii=False))}</code>
""".strip()


def run_anomaly_scan() -> None:
    for coin in CFG.market_coins:
        coin = normalize_coin(coin)
        if not get_recent_snapshots(coin, limit=1):
            msg = run_full_analysis(coin)
            print(f"Snapshot created for {coin}: {len(msg)} chars")
        anomalies = detect_market_anomalies(coin)
        for anomaly in anomalies:
            send_telegram(format_anomaly_message(anomaly))


def get_weekly_summary(coin_symbol: str = "BTC") -> str:
    coin_symbol = normalize_coin(coin_symbol)
    cutoff = (utc_now() - timedelta(days=7)).replace(microsecond=0).isoformat()
    conn = db_connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM market_snapshots
            WHERE coin=? AND created_at>?
            ORDER BY created_at ASC
            """,
            (coin_symbol, cutoff),
        ).fetchall()
        news_rows = conn.execute(
            "SELECT title, source FROM news WHERE fetched_at>? ORDER BY fetched_at DESC LIMIT 8",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return f"Belum ada data mingguan untuk {html_escape(coin_symbol)}. Jalankan /analisis {html_escape(coin_symbol)} dulu."

    prices = [float(r["price"]) for r in rows if r["price"] is not None]
    signals = [str(r["signal"]) for r in rows if r["signal"]]
    fg_values = [int(r["fg_value"]) for r in rows if r["fg_value"] is not None]
    start_price = prices[0]
    end_price = prices[-1]
    change = ((end_price - start_price) / start_price * 100) if start_price else 0.0
    avg_fg = statistics.mean(fg_values) if fg_values else 50.0
    top_news = "\n".join([f"- {row['title'][:90]} ({row['source']})" for row in news_rows]) or "- No stored news."
    content = f"""
<b>Weekly Intelligence Summary - {html_escape(coin_symbol)}</b>
Period: last 7 days

<b>Price</b>
Start: {html_escape(fmt_price(start_price))}
End: {html_escape(fmt_price(end_price))}
Change: {change:+.2f}%
High: {html_escape(fmt_price(max(prices)))}
Low: {html_escape(fmt_price(min(prices)))}

<b>Signal Distribution</b>
BUY: {signals.count('BUY')}
SELL: {signals.count('SELL')}
HOLD: {signals.count('HOLD')}
Average Fear & Greed: {avg_fg:.0f}/100

<b>Stored News Highlights</b>
{html_escape(top_news)}

<i>Not financial advice. This is a stored-data intelligence summary.</i>
""".strip()
    save_intelligence_report("weekly", coin_symbol, content, [])
    return content


# ---------------------------------------------------------------------------
# 12. TELEGRAM BOT
# ---------------------------------------------------------------------------

HELP_MSG = """<b>Crypto Intelligence Agent - Commands</b>

<b>Market Intelligence</b>
/analisis - Full BTC intelligence report
/analisis eth - Full ETH intelligence report
/status - Quick BTC status
/status sol - Quick SOL status
/compare btc eth sol - Compare latest stored snapshots

<b>News and RAG-lite</b>
/berita - Ingest and show latest crypto news
/topnews - Show top 3 news items
/ask [question] - Ask over stored market/news context

<b>Anomaly and Summary</b>
/anomaly - Recent anomaly events
/refresh - Create fresh snapshots and anomaly checks
/mingguan - Weekly BTC summary
/mingguan eth - Weekly ETH summary

<b>OKX and Web3</b>
/okx - OKX balance and wallet status
/daftar - Registration helper
/wallet [address] - ETH wallet balance
/txn [address] [limit] - Wallet transactions
/tokens [address] - Token holdings
/whale [address] [threshold] - Whale activity
/eth - Ethereum network status

<b>Utility</b>
/coins - Available coin symbols
/help - Show this menu

Free text also works as an intelligence question over stored context."""


def coins_message() -> str:
    lines = ["<b>Available Coins</b>", ""]
    for sym, info in COINS.items():
        lines.append(f"- <b>{html_escape(sym)}</b> - {html_escape(info['name'])} ({html_escape(info['tv'])})")
    lines.append("\nUse: /analisis [symbol]")
    return "\n".join(lines)


def split_telegram_message(message: str, limit: int = 3600) -> list[str]:
    if len(message) <= limit:
        return [message]
    chunks: list[str] = []
    current = ""
    for line in message.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit].rstrip())
            continue
        if len(current) + len(line) > limit:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def send_telegram(message: str, chat_id: Optional[str | int] = None, parse_mode: Optional[str] = "HTML") -> bool:
    if not CFG.telegram_token:
        print("TELEGRAM_TOKEN belum diset di .env")
        return False
    target_chat = str(chat_id or CFG.telegram_default_chat_id or "")
    if not target_chat:
        print("TELEGRAM_CHAT_ID belum diset dan chat_id tidak tersedia")
        return False

    url = f"https://api.telegram.org/bot{CFG.telegram_token}/sendMessage"
    ok_all = True
    for chunk in split_telegram_message(message):
        payload: dict[str, Any] = {
            "chat_id": target_chat,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            response = HTTP.post(url, data=payload, timeout=CFG.http_timeout)
            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                continue

            print(f"Telegram API error: {data}")
            if parse_mode:
                # Retry once as plain text if Telegram rejects malformed HTML/Markdown.
                fallback = dict(payload)
                fallback.pop("parse_mode", None)
                fallback["text"] = strip_html_tags(chunk)
                retry_response = HTTP.post(url, data=fallback, timeout=CFG.http_timeout)
                retry_response.raise_for_status()
                retry_data = retry_response.json()
                ok_all = ok_all and bool(retry_data.get("ok"))
            else:
                ok_all = False
        except Exception as exc:
            print(f"Gagal kirim Telegram: {exc}")
            ok_all = False
    return ok_all


def handle_telegram_updates() -> None:
    if not CFG.telegram_token:
        print("TELEGRAM_TOKEN belum diset di .env")
        return

    offset_raw = get_state("telegram_offset", os.getenv("TELEGRAM_INITIAL_OFFSET", "0"))
    try:
        offset = int(offset_raw)
    except ValueError:
        offset = 0

    url = f"https://api.telegram.org/bot{CFG.telegram_token}/getUpdates"
    try:
        response = HTTP.get(url, params={"timeout": 10, "offset": offset}, timeout=CFG.http_timeout + 5)
        # Handle 409 Conflict — another instance was running, wait and retry
        if response.status_code == 409:
            print("409 Conflict: another bot instance detected. Waiting 5s to resolve...")
            time.sleep(5)
            response = HTTP.get(url, params={"timeout": 0, "offset": offset}, timeout=15)
        data = safe_json_dict(response)
        updates = data.get("result", [])
        if not isinstance(updates, list):
            return

        for update in updates:
            update_id = int(update.get("update_id", offset))
            set_state("telegram_offset", str(update_id + 1))
            message = update.get("message") or update.get("edited_message") or {}
            text = str(message.get("text", "")).strip()
            chat_id = message.get("chat", {}).get("id")
            if not text or not chat_id:
                continue

            print(f"Telegram message: {text}")
            text_lower = text.lower().strip()

            if text_lower in ("/start", "/help"):
                send_telegram(HELP_MSG, chat_id=chat_id)
            elif text_lower == "/coins":
                send_telegram(coins_message(), chat_id=chat_id)
            elif text_lower.startswith("/analisis"):
                coin = parse_coin_from_text(text)
                send_telegram(f"Menganalisis {html_escape(coin)}/USDT...", chat_id=chat_id)
                send_telegram(run_full_analysis(coin), chat_id=chat_id)
            elif text_lower.startswith("/status"):
                coin = parse_coin_from_text(text)
                price_data = get_price(coin)
                if not price_data:
                    send_telegram(f"Gagal ambil data {html_escape(coin)}.", chat_id=chat_id)
                    continue
                fg = get_fear_greed()
                analysis = get_analysis(coin)
                signal_data = generate_signal(price_data, analysis, fg)
                save_market_snapshot(coin, price_data, analysis, fg, signal_data)
                send_telegram(format_status_message(coin, price_data, analysis, fg, signal_data), chat_id=chat_id)
            elif text_lower.startswith("/compare"):
                send_telegram(compare_coins(extract_coins_from_text(text)), chat_id=chat_id)
            elif text_lower.startswith("/refresh"):
                for coin in CFG.market_coins:
                    auto_report(coin)
                send_telegram("Refresh selesai. Snapshot dan anomaly check sudah dijalankan.", chat_id=chat_id)
            elif text_lower == "/okx":
                send_telegram(format_okx_status(), chat_id=chat_id)
            elif text_lower in ("/daftar", "/register"):
                send_telegram(okx_register_competition(), chat_id=chat_id)
            elif text_lower == "/berita":
                send_telegram("Mengambil dan mengindeks berita terbaru...", chat_id=chat_id)
                send_telegram(get_news_for_telegram(top_n=5), chat_id=chat_id)
            elif text_lower == "/topnews":
                send_telegram(get_news_for_telegram(top_n=3), chat_id=chat_id)
            elif text_lower.startswith("/mingguan"):
                coin = parse_coin_from_text(text)
                send_telegram(get_weekly_summary(coin), chat_id=chat_id)
            elif text_lower in ("/anomaly", "/anomali"):
                send_telegram(recent_anomalies(), chat_id=chat_id)
            elif text_lower.startswith("/ask"):
                question = text.split(" ", 1)[1] if " " in text else "Apa insight terbaru dari data tersimpan?"
                send_telegram(answer_intelligence_question(question), chat_id=chat_id)
            elif text_lower.startswith("/scan"):
                parts = text.split()
                wallet = parts[1] if len(parts) > 1 else (CFG.okx_wallet_address or os.getenv("WALLET_ADDRESS", ""))
                if not wallet:
                    send_telegram("Wallet address tidak dikonfigurasi. Ketik: /scan 0xYourAddress", chat_id=chat_id)
                else:
                    send_telegram(f"Scanning anomali untuk {wallet[:8]}...{wallet[-4:]}...", chat_id=chat_id)
                    try:
                        from anomaly_detector import AnomalyDetector
                        detector = AnomalyDetector(db_path=CFG.db_path)
                        wallet_alerts = detector.check_wallet_anomaly(wallet)
                        market_alerts = (
                            detector.check_market_anomaly("BTC") +
                            detector.check_market_anomaly("ETH")
                        )
                        all_alerts = wallet_alerts + market_alerts
                        if not all_alerts:
                            send_telegram(
                                f"Scan selesai untuk {wallet[:8]}...{wallet[-4:]}\n\nTidak ada anomali terdeteksi. Wallet aman.",
                                chat_id=chat_id
                            )
                        else:
                            send_telegram(f"Scan selesai. {len(all_alerts)} anomali ditemukan:", chat_id=chat_id)
                            for alert in all_alerts:
                                send_telegram(alert.to_telegram(), chat_id=chat_id)
                    except Exception as scan_err:
                        send_telegram(f"Scan error: {scan_err}", chat_id=chat_id)
            elif text_lower.startswith("/wallet"):
                parts = text.split()
                if len(parts) < 2:
                    send_telegram("Contoh: /wallet 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", chat_id=chat_id)
                else:
                    send_telegram(html_escape(web3_get_eth_balance(parts[1])), chat_id=chat_id)
            elif text_lower.startswith("/txn"):
                parts = text.split()
                if len(parts) < 2:
                    send_telegram("Contoh: /txn 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", chat_id=chat_id)
                else:
                    limit = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 5
                    send_telegram(html_escape(web3_get_transactions(parts[1], limit=limit)), chat_id=chat_id)
            elif text_lower.startswith("/tokens"):
                parts = text.split()
                if len(parts) < 2:
                    send_telegram("Contoh: /tokens 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", chat_id=chat_id)
                else:
                    send_telegram(html_escape(web3_get_tokens(parts[1])), chat_id=chat_id)
            elif text_lower.startswith("/whale"):
                parts = text.split()
                if len(parts) < 2:
                    send_telegram("Contoh: /whale 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 100", chat_id=chat_id)
                else:
                    threshold = float(parts[2]) if len(parts) > 2 else 100.0
                    send_telegram(html_escape(web3_whale_watch(parts[1], threshold=threshold)), chat_id=chat_id)
            elif text_lower == "/eth":
                send_telegram(html_escape(web3_eth_network_status()), chat_id=chat_id)
            else:
                web3_reply = web3_detect_and_answer(text)
                if web3_reply:
                    send_telegram(html_escape(web3_reply), chat_id=chat_id)
                else:
                    send_telegram(answer_intelligence_question(text), chat_id=chat_id)
    except Exception as exc:
        print(f"Error polling Telegram: {exc}")


# ---------------------------------------------------------------------------
# 13. SCHEDULER AND ENTRYPOINT
# ---------------------------------------------------------------------------

def schedule_jobs() -> None:
    for coin in CFG.market_coins:
        coin = normalize_coin(coin)
        schedule.every(CFG.snapshot_interval_minutes).minutes.do(lambda c=coin: auto_report(c))
    schedule.every(CFG.news_interval_minutes).minutes.do(check_and_push_news)
    schedule.every(CFG.anomaly_interval_minutes).minutes.do(run_anomaly_scan)
    schedule.every().saturday.at("08:00").do(lambda: send_telegram(get_weekly_summary("BTC")))
    schedule.every().saturday.at("08:05").do(lambda: send_telegram(get_weekly_summary("ETH")))


def validate_startup_config() -> list[str]:
    warnings: list[str] = []
    if not CFG.groq_api_key:
        warnings.append("GROQ_API_KEY missing: AI commentary and /ask will fall back or fail.")
    if not CFG.telegram_token:
        warnings.append("TELEGRAM_TOKEN missing: Telegram bot cannot run.")
    if not CFG.telegram_default_chat_id:
        warnings.append("TELEGRAM_CHAT_ID missing: default scheduled alerts cannot be sent, but replies can still use incoming chat_id.")
    if CFG.default_coin not in COINS:
        warnings.append(f"DEFAULT_COIN {CFG.default_coin} is not supported. BTC will be used.")
    unsupported = [c for c in CFG.market_coins if c not in COINS]
    if unsupported:
        warnings.append(f"Unsupported MARKET_COINS ignored by normalize_coin: {', '.join(unsupported)}")
    return warnings


def main() -> None:
    init_db()
    print("Crypto Intelligence Agent - active")
    print(f"Timezone: {CFG.app_timezone_name}")
    print(f"Database: {CFG.db_path}")
    print(f"Market coins: {', '.join(CFG.market_coins)}")
    for warning in validate_startup_config():
        print(f"CONFIG WARNING: {warning}")

    if CFG.send_startup_report:
        for coin in CFG.market_coins:
            msg = run_full_analysis(coin)
            send_telegram(msg)
        check_and_push_news()

    schedule_jobs()
    while True:
        schedule.run_pending()
        handle_telegram_updates()
        time.sleep(CFG.telegram_poll_seconds)


if __name__ == "__main__":
    main()