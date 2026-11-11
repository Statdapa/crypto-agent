"""Public Binance spot USDT market universe: dynamic discovery and bounded scanner.

No API key, private account endpoints, or order placement. The full active
exchange catalog is queried, while ticker 24h data is fetched in one batch.
Detailed technical analysis is opt-in per symbol to respect API rate limits.
"""
from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests

BASE_URL = "https://data-api.binance.vision"
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,24}$")


class MarketUnavailable(ValueError):
    pass


def spot_usdt_catalog(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise MarketUnavailable("Binance exchangeInfo response is invalid")
    result = {}
    for market in payload["symbols"]:
        if not isinstance(market, dict):
            continue
        base = str(market.get("baseAsset") or "").upper()
        pair = str(market.get("symbol") or "").upper()
        if not _SYMBOL_PATTERN.fullmatch(base):
            continue
        if (market.get("quoteAsset") != "USDT" or market.get("status") != "TRADING"
                or market.get("isSpotTradingAllowed") is not True
                or pair != base+"USDT"):
            continue
        if base in result:
            raise MarketUnavailable("Duplicate active market base symbol")
        result[base] = pair
    if not result:
        raise MarketUnavailable("No active Binance spot USDT markets")
    return result


def _positive(value, description):
    if isinstance(value, bool):
        raise MarketUnavailable("Invalid boolean "+description)
    try:
        val = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketUnavailable("Invalid "+description) from exc
    if not val.is_finite() or val <= 0:
        raise MarketUnavailable("Non-positive/non-finite "+description)
    return val


def _nonnegative(value, description):
    if isinstance(value, bool):
        raise MarketUnavailable("Invalid boolean "+description)
    try:
        val = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketUnavailable("Invalid "+description) from exc
    if not val.is_finite() or val < 0:
        raise MarketUnavailable("Negative/non-finite "+description)
    return val


class BinanceSpotUniverse:
    def __init__(self, session=None, ttl_seconds=900, timeout=15):
        self.session = session or requests.Session()
        self.ttl = max(60, int(ttl_seconds))
        self.timeout = timeout
        self._catalog = {}
        self._cached_at = 0.0

    def _get(self, endpoint, params=None):
        response = self.session.get(
            BASE_URL+endpoint, params=params, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def catalog(self, force=False):
        if self._catalog and not force and time.monotonic()-self._cached_at < self.ttl:
            return dict(self._catalog)
        # If refresh fails, raise instead of trusting a possibly delisted pair.
        markets = spot_usdt_catalog(self._get("/api/v3/exchangeInfo"))
        self._catalog = markets
        self._cached_at = time.monotonic()
        return dict(markets)

    def require(self, base):
        base = str(base).upper().strip()
        if not _SYMBOL_PATTERN.fullmatch(base):
            raise MarketUnavailable("Invalid spot symbol")
        markets = self.catalog()
        if base not in markets:
            raise MarketUnavailable("Coin "+base+" has no active Binance spot USDT pair")
        return markets[base]

    def ticker_24h(self, base):
        pair = self.require(base)
        data = self._get("/api/v3/ticker/24hr", {"symbol": pair})
        if not isinstance(data, dict) or data.get("symbol") != pair:
            raise MarketUnavailable("Ticker pair mismatch for "+pair)
        price = _positive(data.get("lastPrice"), pair+" last price")
        quote_volume = _nonnegative(data.get("quoteVolume"), pair+" quote volume")
        try:
            pct = float(data.get("priceChangePercent"))
        except (ValueError, TypeError) as exc:
            raise MarketUnavailable("Invalid 24h change") from exc
        if not math.isfinite(pct):
            raise MarketUnavailable("Non-finite 24h change")
        return {
            "coin": base, "pair": pair, "price": float(price),
            "change_24h": pct, "change_7d": None, "change_30d": None,
            "high_24h": float(_positive(data.get("highPrice"), "high")),
            "low_24h": float(_positive(data.get("lowPrice"), "low")),
            "volume": float(quote_volume), "market_cap": None,
            "ath": None, "ath_change": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "Binance spot USDT",
        }

    def candles(self, base, interval="4h", limit=180):
        pair = self.require(base)
        if interval not in ("1h","4h","1d"):
            raise MarketUnavailable("Unsupported candle interval")
        if not isinstance(limit,int) or not 35 <= limit <= 1000:
            raise MarketUnavailable("Invalid OHLCV limit")
        raw = self._get("/api/v3/klines",
                        {"symbol":pair,"interval":interval,"limit":limit})
        if not isinstance(raw,list):
            raise MarketUnavailable("Invalid kline response")
        now_ms = int(time.time()*1000)
        seen = set()
        candles = []
        for item in raw:
            if not isinstance(item,list) or len(item)<7:
                raise MarketUnavailable("Malformed OHLCV item")
            open_time = int(item[0])
            end_time = int(item[6])
            if open_time in seen or end_time <= open_time:
                raise MarketUnavailable("Duplicate or invalid OHLCV time")
            seen.add(open_time)
            if end_time >= now_ms:  # Ignore still forming candle
                continue
            o,h,l,c = (float(_positive(item[i], "OHLC price")) for i in (1,2,3,4))
            if not (l <= o <= h and l <= c <= h):
                raise MarketUnavailable("OHLC violates high/low constraints")
            candles.append([open_time,o,h,l,c])
        if len(candles) < 35 or any(candles[i][0] >= candles[i+1][0]
                                     for i in range(len(candles)-1)):
            raise MarketUnavailable("Insufficient or unordered completed candles")
        return candles

    def scan(self, min_quote_volume=1000000, top=12):
        markets = self.catalog()
        data = self._get("/api/v3/ticker/24hr")
        if not isinstance(data,list):
            raise MarketUnavailable("Invalid exchange-wide ticker data")
        if not 1 <= top <= 30:
            raise MarketUnavailable("Scanner top must be within 1..30")
        volume_limit = _nonnegative(min_quote_volume,"scanner minimum volume")
        ranking=[]
        for item in data:
            if not isinstance(item,dict):
                continue
            pair = str(item.get("symbol") or "")
            if not pair.endswith("USDT"):
                continue
            base = pair[:-4]
            if markets.get(base) != pair:
                continue
            try:
                price = _positive(item.get("lastPrice"), "price")
                volume = _nonnegative(item.get("quoteVolume"), "quote volume")
                pct = float(item.get("priceChangePercent"))
                if not math.isfinite(pct) or volume < volume_limit:
                    continue
            except (MarketUnavailable, TypeError, ValueError):
                continue
            ranking.append({
                "coin": base,"pair":pair,"price":float(price),
                "change_24h":pct,"quote_volume":float(volume),
            })
        ranking.sort(key=lambda row:(-abs(row["change_24h"]),-row["quote_volume"],
                                      row["pair"]))
        return {
            "total_spot_usdt_pairs":len(markets),
            "liquid_pairs_count":len(ranking),
            "min_quote_volume_usdt":float(volume_limit),
            "top_movers":ranking[:top],
        }
