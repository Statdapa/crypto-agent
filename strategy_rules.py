"""Canonical pure technical indicators and rule-based signal scoring.

Shared by the Telegram market agent and causal research backtest. No network,
secrets, optional Telegram/AI dependencies, or market data collection.
"""
from __future__ import annotations

from typing import Any, Optional


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


