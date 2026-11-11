"""Fail-closed, spot-only paper execution and risk controls.

This module NEVER sends exchange orders. Prices are observed snapshots, not fills.
Real funds require a separately reviewed live execution and reconciliation layer.
"""
from __future__ import annotations

import math
import os
import sqlite3
from contextlib import contextmanager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Optional


def D(value) -> Decimal:
    """Convert finite numbers safely, without passing floats into arithmetic."""
    if isinstance(value, bool):
        raise ValueError("Boolean is not a price or amount")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError("Invalid numeric input") from exc
    if not result.is_finite():
        raise ValueError("Numeric inputs must be finite")
    return result


def utc_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        raise ValueError("Signal timestamp must include a timezone")
    return dt.astimezone(timezone.utc)


def ts(dt: datetime) -> str:
    return utc_time(dt).isoformat()


def floor_quantity(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)


@dataclass(frozen=True)
class MarketSignal:
    symbol: str
    price: Decimal
    action: str
    observed_at: datetime

    def __post_init__(self):
        symbol = self.symbol.upper().strip()
        if not symbol or "/" not in symbol or ":" in symbol:
            raise ValueError("Use exchange-formatted spot symbol, e.g. BTC/USDT")
        if self.action.upper() not in {"BUY", "SELL", "HOLD"}:
            raise ValueError("Signal must be BUY, SELL, or HOLD")
        if D(self.price) <= 0:
            raise ValueError("Price must be strictly positive")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "price", D(self.price))
        object.__setattr__(self, "action", self.action.upper())
        object.__setattr__(self, "observed_at", utc_time(self.observed_at))

    @property
    def key(self):
        return f"{self.symbol}:{ts(self.observed_at)}"


@dataclass(frozen=True)
class RiskLimits:
    initial_cash: Decimal = Decimal("1000")
    allowed_symbol: str = "BTC/USDT"
    max_signal_age_minutes: int = 45
    max_risk_fraction: Decimal = Decimal("0.01")
    max_position_fraction: Decimal = Decimal("0.20")
    max_daily_loss_fraction: Decimal = Decimal("0.03")
    max_drawdown_fraction: Decimal = Decimal("0.10")
    stop_fraction: Decimal = Decimal("0.03")
    take_fraction: Decimal = Decimal("0.05")
    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    max_entries_per_day: int = 3
    min_notional: Decimal = Decimal("10")

    def __post_init__(self):
        for key in ("initial_cash", "max_risk_fraction", "max_position_fraction",
                    "max_daily_loss_fraction", "max_drawdown_fraction",
                    "stop_fraction", "take_fraction", "fee_bps",
                    "slippage_bps", "min_notional"):
            object.__setattr__(self, key, D(getattr(self, key)))
        if self.initial_cash <= 0 or self.min_notional <= 0:
            raise ValueError("Initial capital and minimum notional must be positive")
        for key in ("max_risk_fraction", "max_position_fraction",
                    "max_daily_loss_fraction", "max_drawdown_fraction",
                    "stop_fraction", "take_fraction"):
            if not (0 < getattr(self, key) < 1):
                raise ValueError(f"{key} must be between zero and one")
        if not (0 <= self.fee_bps <= 100 and 0 <= self.slippage_bps <= 100):
            raise ValueError("Fee/slippage settings are invalid")
        if self.fee_bps < 0 or self.slippage_bps < 0:
            raise ValueError("Fee/slippage cannot be negative")
        if (isinstance(self.max_signal_age_minutes, bool) or
            isinstance(self.max_entries_per_day, bool) or
            not isinstance(self.max_signal_age_minutes, int) or
            not isinstance(self.max_entries_per_day, int) or
            self.max_signal_age_minutes < 1 or self.max_entries_per_day < 1):
            raise ValueError("Age and trade-count limits must be positive")
        if not self.allowed_symbol or "/" not in self.allowed_symbol or ":" in self.allowed_symbol:
            raise ValueError("allowed_symbol must be a spot trading pair")
        object.__setattr__(self, "allowed_symbol", self.allowed_symbol.upper())

    @property
    def fee_rate(self):
        return self.fee_bps / D("10000")

    @property
    def slip_rate(self):
        return self.slippage_bps / D("10000")


class PaperTrader:
    def __init__(self, ledger_path: str, limits: RiskLimits = RiskLimits()):
        self.path = str(ledger_path)
        self.limits = limits
        if self.path == ":memory:":
            raise ValueError("Use a persistent on-disk ledger, not an in-memory database")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        """Transaction-safe SQLite connection that always closes its file handle.

        sqlite3.Connection.__exit__ commits/rolls back but does *not* close.
        Explicit close is required on Windows to release a temporary database.
        """
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("PRAGMA journal_mode = WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS paper_account (
                    id INTEGER PRIMARY KEY CHECK (id=1), cash TEXT NOT NULL,
                    peak_equity TEXT NOT NULL, daily_start_equity TEXT NOT NULL,
                    day TEXT NOT NULL, halted INTEGER NOT NULL DEFAULT 0,
                    last_price TEXT, last_at TEXT
                );
                CREATE TABLE IF NOT EXISTS paper_position (
                    symbol TEXT PRIMARY KEY, qty TEXT NOT NULL,
                    entry_price TEXT NOT NULL, entry_fee TEXT NOT NULL,
                    stop_price TEXT NOT NULL, take_price TEXT NOT NULL,
                    opened_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL, signal_key TEXT NOT NULL, symbol TEXT NOT NULL,
                    side TEXT NOT NULL, qty TEXT NOT NULL, price TEXT NOT NULL,
                    fee TEXT NOT NULL, realized_pnl TEXT, reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_decisions (
                    signal_key TEXT PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_settings (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    symbol TEXT NOT NULL, initial_cash TEXT NOT NULL,
                    max_risk_fraction TEXT NOT NULL,
                    max_position_fraction TEXT NOT NULL,
                    stop_fraction TEXT NOT NULL, take_fraction TEXT NOT NULL,
                    fee_bps TEXT NOT NULL, slippage_bps TEXT NOT NULL,
                    max_daily_loss_fraction TEXT NOT NULL,
                    max_drawdown_fraction TEXT NOT NULL,
                    max_entries_per_day INTEGER NOT NULL
                );
            """)
            settings = (
                self.limits.allowed_symbol, str(self.limits.initial_cash),
                str(self.limits.max_risk_fraction),
                str(self.limits.max_position_fraction),
                str(self.limits.stop_fraction), str(self.limits.take_fraction),
                str(self.limits.fee_bps), str(self.limits.slippage_bps),
                str(self.limits.max_daily_loss_fraction),
                str(self.limits.max_drawdown_fraction),
                self.limits.max_entries_per_day,
            )
            existing = db.execute("SELECT * FROM paper_settings WHERE id=1").fetchone()
            if existing:
                if tuple(existing)[1:] != settings:
                    raise ValueError("Paper ledger risk configuration differs from initial settings; use a new ledger")
            else:
                db.execute(
                    "INSERT INTO paper_settings VALUES(1,?,?,?,?,?,?,?,?,?,?,?)", settings
                )
            present = db.execute("SELECT 1 FROM paper_account WHERE id=1").fetchone()
            if not present:
                cash = str(self.limits.initial_cash)
                db.execute(
                    "INSERT INTO paper_account(id,cash,peak_equity,daily_start_equity,day,halted) "
                    "VALUES(1,?,?,?, ?,0)",
                    (cash, cash, cash, datetime.now(timezone.utc).date().isoformat()),
                )

    def _equity(self, db, mark: Decimal) -> Decimal:
        account = db.execute("SELECT cash FROM paper_account WHERE id=1").fetchone()
        position = db.execute("SELECT qty FROM paper_position WHERE symbol=?",
                              (self.limits.allowed_symbol,)).fetchone()
        return D(account["cash"]) + (D(position["qty"]) * mark if position else D(0))

    def status(self):
        with self._connect() as db:
            account = dict(db.execute("SELECT * FROM paper_account WHERE id=1").fetchone())
            pos = db.execute("SELECT * FROM paper_position").fetchone()
            mark = D(account["last_price"] or 0)
            equity = self._equity(db, mark)
            fills = db.execute("SELECT COUNT(*) AS n FROM paper_fills").fetchone()["n"]
            return {
                "mode": "PAPER_ONLY",
                "cash": str(account["cash"]),
                "equity": str(equity),
                "halted": bool(account["halted"]),
                "peak_equity": account["peak_equity"],
                "daily_start_equity": account["daily_start_equity"],
                "last_price": account["last_price"],
                "last_at": account["last_at"],
                "position": dict(pos) if pos else None,
                "fill_count": fills,
            }

    def halt(self):
        """Persist the kill switch. Existing paper positions exit on next fresh tick."""
        with self._connect() as db:
            db.execute("UPDATE paper_account SET halted=1 WHERE id=1")

    def resume(self):
        """Explicit restart after manual review; never automated."""
        with self._connect() as db:
            db.execute("UPDATE paper_account SET halted=0 WHERE id=1")

    def fills(self, limit: int = 100):
        with self._connect() as db:
            rows = db.execute("SELECT * FROM paper_fills ORDER BY id DESC LIMIT ?",
                              (min(max(1,limit),1000),)).fetchall()
            return [dict(r) for r in rows]

    def stats(self):
        """Realized performance of CLOSED paper trades, not predictive win rate."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT realized_pnl, fee FROM paper_fills WHERE side='SELL' ORDER BY id"
            ).fetchall()
            buys = db.execute(
                "SELECT COUNT(*) FROM paper_fills WHERE side='BUY'"
            ).fetchone()[0]
        profits = [D(row["realized_pnl"]) for row in rows]
        total = sum(profits, D(0))
        wins = sum(p > 0 for p in profits)
        losses = sum(p < 0 for p in profits)
        gross_winners = sum((p for p in profits if p > 0), D(0))
        gross_losers = -sum((p for p in profits if p < 0), D(0))
        return {
            "mode": "PAPER_ONLY",
            "open_or_unclosed_entries": buys - len(rows),
            "closed_trades": len(profits),
            "wins": wins,
            "losses": losses,
            "breakeven": len(profits) - wins - losses,
            "win_rate": float(D(wins) / D(len(profits))) if profits else None,
            "realized_net_pnl": str(total),
            "profit_factor": (float(gross_winners / gross_losers)
                              if gross_losers else None),
            "warning": "Observed paper fills only; no predictive accuracy or live execution guarantee",
        }

    def _entry_count(self, db, day: str) -> int:
        return db.execute(
            "SELECT COUNT(*) FROM paper_fills WHERE side='BUY' AND substr(at,1,10)=?",
            (day,),
        ).fetchone()[0]

    def _buy(self, db, signal: MarketSignal, equity: Decimal, cash: Decimal, now: datetime):
        limits = self.limits
        fill_price = signal.price * (D(1) + limits.slip_rate)
        budget_risk = equity * limits.max_risk_fraction / limits.stop_fraction
        budget_exposure = equity * limits.max_position_fraction
        budget_cash = cash / (D(1) + limits.fee_rate)
        budget = min(budget_risk, budget_exposure, budget_cash)
        qty = floor_quantity(budget / fill_price)
        notional = fill_price * qty
        fee = notional * limits.fee_rate
        if qty <= 0 or notional < limits.min_notional or notional + fee > cash:
            return "SKIP", "insufficient_cash_or_below_min_notional"
        db.execute(
            "INSERT INTO paper_position VALUES(?,?,?,?,?,?,?)",
            (signal.symbol, str(qty), str(fill_price), str(fee),
             str(fill_price * (D(1) - limits.stop_fraction)),
             str(fill_price * (D(1) + limits.take_fraction)), ts(now)),
        )
        db.execute("UPDATE paper_account SET cash=? WHERE id=1",
                   (str(cash - notional - fee),))
        db.execute(
            "INSERT INTO paper_fills(at,signal_key,symbol,side,qty,price,fee,realized_pnl,reason) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (ts(now), signal.key, signal.symbol, "BUY", str(qty),
             str(fill_price), str(fee), None, "rule_signal"),
        )
        return "BUY", "paper_entry"

    def _sell(self, db, signal: MarketSignal, position, cash: Decimal,
              now: datetime, reason: str):
        qty = D(position["qty"])
        price = signal.price * (D(1) - self.limits.slip_rate)
        notional = qty * price
        fee = notional * self.limits.fee_rate
        pnl = ((price - D(position["entry_price"])) * qty -
               D(position["entry_fee"]) - fee)
        db.execute("DELETE FROM paper_position WHERE symbol=?", (signal.symbol,))
        db.execute("UPDATE paper_account SET cash=? WHERE id=1",
                   (str(cash + notional - fee),))
        db.execute(
            "INSERT INTO paper_fills(at,signal_key,symbol,side,qty,price,fee,realized_pnl,reason) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (ts(now), signal.key, signal.symbol, "SELL", str(qty),
             str(price), str(fee), str(pnl), reason),
        )
        return "SELL", reason

    def tick(self, signal: MarketSignal, now: Optional[datetime] = None):
        """One atomic, idempotent decision. Only paper fills are recorded.

        Quotes are assumed available at the snapshot price. Real execution can
        differ materially, especially across gaps or during high volatility.
        """
        now = utc_time(now or datetime.now(timezone.utc))
        if signal.symbol != self.limits.allowed_symbol:
            return {"action": "REJECT", "reason": "symbol_not_allowed"}
        age = (now - signal.observed_at).total_seconds()
        if age < -60:
            return {"action": "REJECT", "reason": "signal_from_future"}
        if age > self.limits.max_signal_age_minutes * 60:
            return {"action": "REJECT", "reason": "stale_signal"}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM paper_decisions WHERE signal_key=?",
                          (signal.key,)).fetchone():
                return {"action": "DUPLICATE", "reason": "signal_already_processed"}
            acc = db.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
            if acc["last_at"] and signal.observed_at <= utc_time(acc["last_at"]):
                return {"action": "REJECT", "reason": "out_of_order_snapshot"}
            cash = D(acc["cash"])
            position = db.execute("SELECT * FROM paper_position WHERE symbol=?",
                                  (signal.symbol,)).fetchone()
            equity = cash + (D(position["qty"]) * signal.price if position else D(0))
            day = now.date().isoformat()
            start_equity = D(acc["daily_start_equity"])
            if day != acc["day"]:
                start_equity = equity
                db.execute("UPDATE paper_account SET day=?,daily_start_equity=? WHERE id=1",
                           (day, str(start_equity)))
            peak = max(D(acc["peak_equity"]), equity)
            drawdown = (peak - equity) / peak if peak > 0 else D(1)
            daily_loss = (start_equity - equity) / start_equity if start_equity > 0 else D(1)
            breached = (drawdown >= self.limits.max_drawdown_fraction or
                        daily_loss >= self.limits.max_daily_loss_fraction)
            halted = bool(acc["halted"]) or breached
            if halted:
                db.execute("UPDATE paper_account SET halted=1 WHERE id=1")
            db.execute(
                "UPDATE paper_account SET last_price=?, last_at=?, peak_equity=? WHERE id=1",
                (str(signal.price), ts(now), str(peak)),
            )
            action, reason = "HOLD", "no_action"
            if position:
                if halted:
                    action, reason = self._sell(db, signal, position, cash, now, "risk_or_manual_kill")
                elif signal.price <= D(position["stop_price"]):
                    action, reason = self._sell(db, signal, position, cash, now, "stop_triggered")
                elif signal.price >= D(position["take_price"]):
                    action, reason = self._sell(db, signal, position, cash, now, "take_profit_triggered")
                elif signal.action == "SELL":
                    action, reason = self._sell(db, signal, position, cash, now, "rule_signal")
            elif halted:
                action, reason = "BLOCK", "kill_switch_or_loss_limit"
            elif signal.action == "BUY":
                if self._entry_count(db, day) >= self.limits.max_entries_per_day:
                    action, reason = "BLOCK", "daily_entry_limit"
                else:
                    action, reason = self._buy(db, signal, equity, cash, now)
            db.execute(
                "INSERT INTO paper_decisions(signal_key,at,action,reason) VALUES(?,?,?,?)",
                (signal.key, ts(now), action, reason),
            )
            return {"action": action, "reason": reason,
                    "signal_key": signal.key, "equity_before": str(equity),
                    "halted": halted}


def load_latest_agent_signal(db_path: str, symbol: str) -> Optional[MarketSignal]:
    """Read existing agent snapshot, without modifying the agent database."""
    if not os.path.isfile(db_path):
        return None
    coin = symbol.split("/")[0].upper()
    with closing(sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "market_snapshots" not in tables:
            return None
        row = db.execute(
            "SELECT price, signal, created_at FROM market_snapshots WHERE coin=? "
            "ORDER BY created_at DESC,id DESC LIMIT 1", (coin,),
        ).fetchone()
    if not row or row[1] not in ("BUY", "SELL", "HOLD"):
        return None
    return MarketSignal(symbol, D(row[0]), row[1], utc_time(row[2]))
