"""Read-only, backwards-compatible data access for FinSight's dashboard.

The Telegram agent writes market_snapshots/anomaly_events; legacy installations
used price_history/anomaly_log. Never fabricate values when tables are missing.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def latest_signals(db_path: str) -> list[dict]:
    if not os.path.isfile(db_path):
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        tables = _tables(conn)
        if "market_snapshots" in tables:
            rows = conn.execute("""
                SELECT s.coin, s.price, s.change_24h, s.rsi, s.signal,
                       s.signal_strength, s.created_at
                FROM market_snapshots s
                WHERE s.id = (
                    SELECT m.id FROM market_snapshots m WHERE m.coin=s.coin
                    ORDER BY m.created_at DESC, m.id DESC LIMIT 1
                ) ORDER BY s.coin
            """).fetchall()
            if rows:
                return [_signal_row(row, "market_snapshots") for row in rows]
        if "price_history" in tables:
            rows = conn.execute("""
                SELECT s.coin, s.price, s.change_24h, s.rsi, s.signal,
                       s.confidence, s.recorded_at
                FROM price_history s
                WHERE s.id = (
                    SELECT p.id FROM price_history p WHERE p.coin=s.coin
                    ORDER BY p.recorded_at DESC, p.id DESC LIMIT 1
                ) ORDER BY s.coin
            """).fetchall()
            return [_signal_row(row, "price_history") for row in rows]
    return []


def _signal_row(row, source):
    coin, price, change, rsi, signal, strength, updated_at = row
    return {
        "coin": coin, "price": price, "change_24h": change,
        "rsi": rsi, "signal": signal,
        "signal_strength": strength, "confidence": strength,
        "updated_at": updated_at, "source": source,
        "method": "rule_based",
    }


def _parse_time(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _read_alerts(conn, tables):
    alerts = []
    if "anomaly_events" in tables:
        for row in conn.execute("""
            SELECT coin, severity, title, description, created_at
            FROM anomaly_events
        """):
            coin, severity, title, description, created_at = row
            alerts.append({
                "type": "MARKET_ANOMALY", "severity": severity, "title": title,
                "coin": coin, "description": description, "detected_at": created_at,
                "source": "anomaly_events",
            })
    if "anomaly_log" in tables:
        for row in conn.execute("""
            SELECT alert_type, severity, title, wallet, coin, value, threshold,
                   description, tx_hash, detected_at FROM anomaly_log
        """):
            kind, severity, title, wallet, coin, value, threshold, description, tx_hash, date = row
            alerts.append({
                "type": kind, "severity": severity, "title": title, "wallet": wallet,
                "coin": coin, "value": value, "threshold": threshold,
                "description": description, "tx_hash": tx_hash,
                "detected_at": date, "source": "anomaly_log",
            })
    return sorted(alerts, key=lambda a: _parse_time(a["detected_at"]), reverse=True)


def recent_alerts(db_path: str, limit: int = 20, since: datetime | None = None) -> list[dict]:
    if not os.path.isfile(db_path):
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        rows = _read_alerts(conn, _tables(conn))
    if since is not None:
        cutoff = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since.astimezone(timezone.utc)
        rows = [row for row in rows if _parse_time(row["detected_at"]) >= cutoff]
    return rows[:max(0, limit)]


def weekly_summary(db_path: str, coin: str, now: datetime | None = None) -> dict | None:
    if not os.path.isfile(db_path):
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=7)
    with closing(sqlite3.connect(db_path)) as conn:
        tables = _tables(conn)
        rows = []
        if "market_snapshots" in tables:
            rows = conn.execute("""
                SELECT price, rsi, signal FROM market_snapshots
                WHERE coin=? AND datetime(created_at)>=datetime(?)
                ORDER BY created_at DESC, id DESC LIMIT 2000
            """, (coin, cutoff.isoformat())).fetchall()
        if not rows and "price_history" in tables:
            rows = conn.execute("""
                SELECT price, rsi, signal FROM price_history
                WHERE coin=? AND datetime(recorded_at)>=datetime(?)
                ORDER BY recorded_at DESC, id DESC LIMIT 2000
            """, (coin, cutoff.isoformat())).fetchall()
    valid_prices = [float(r[0]) for r in rows if r[0] is not None and r[0] > 0]
    if not valid_prices:
        return None
    rsi_values = [float(r[1]) for r in rows if r[1] is not None]
    signals = [str(r[2]) for r in rows if r[2] in ("BUY", "SELL", "HOLD")]
    counts = {name: signals.count(name) for name in ("BUY", "SELL", "HOLD")}
    dominant = max(counts, key=counts.get) if signals else "N/A"
    opened, closed = valid_prices[-1], valid_prices[0]
    change = round((closed - opened) / opened * 100, 2)
    alerts = recent_alerts(db_path, limit=10000, since=cutoff)
    return {
        "coin": coin, "period": "7 days",
        "generated": now.isoformat(),
        "price": {
            "open": round(opened, 2), "close": round(closed, 2),
            "high": round(max(valid_prices), 2),
            "low": round(min(valid_prices), 2),
            "pct_change": change,
            "trend": "bullish" if change > 0 else "bearish" if change < 0 else "flat",
        },
        "signals": {
            "dominant": dominant, "buy_count": counts["BUY"],
            "sell_count": counts["SELL"], "hold_count": counts["HOLD"],
            "avg_rsi": round(sum(rsi_values) / len(rsi_values), 1) if rsi_values else None,
        },
        "anomalies": {
            "total": len(alerts),
            "critical": sum(a["severity"] == "CRITICAL" for a in alerts),
            "high": sum(a["severity"] == "HIGH" for a in alerts),
            "medium": sum(a["severity"] == "MEDIUM" for a in alerts),
            "list": [{"severity": a["severity"], "title": a["title"], "time": a["detected_at"]}
                     for a in alerts[:5]],
        },
        "summary": (
            f"{coin} changed {change:+.2f}% across available 7-day snapshots. "
            f"Dominant rule-based signal: {dominant}. "
            f"{len(alerts)} logged anomalies. No trading performance is implied."
        ),
    }
