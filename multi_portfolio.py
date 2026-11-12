"""Multi-asset, SPOT USDT, shared-cash PAPER portfolio. No real exchange API.

Virtual stop/take orders are only evaluated at incoming verified quote marks.
A gap, outage, or latency can cause exits beyond the configured stop.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from trading_engine import D, floor_quantity, ts, utc_time


@dataclass(frozen=True)
class PortfolioLimits:
    initial_cash: Decimal = Decimal("1000")
    max_positions: int = 3
    max_position_fraction: Decimal = Decimal("0.15")
    max_total_fraction: Decimal = Decimal("0.45")
    risk_per_trade: Decimal = Decimal("0.01")
    stop_fraction: Decimal = Decimal("0.03")
    take_fraction: Decimal = Decimal("0.05")
    daily_loss_fraction: Decimal = Decimal("0.03")
    max_drawdown_fraction: Decimal = Decimal("0.10")
    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    max_entries_daily: int = 5
    min_notional: Decimal = Decimal("10")
    signal_age_minutes: int = 45
    mark_age_seconds: int = 120
    max_other_mark_age_seconds: int = 300
    cooldown_minutes: int = 60

    def __post_init__(self):
        for k, v in asdict(self).items():
            if isinstance(v, bool):
                raise ValueError("Boolean risk setting")
            if isinstance(v, Decimal):
                val = D(v)
                if k in ("initial_cash", "min_notional") and val <= 0:
                    raise ValueError(k + " must be positive")
                if "fraction" in k or k == "risk_per_trade":
                    if not 0 < val < 1:
                        raise ValueError(k + " must be in (0,1)")
                if k in ("fee_bps", "slippage_bps") and not 0 <= val <= 100:
                    raise ValueError(k + " invalid")
            elif isinstance(v, int) and v <= 0:
                raise ValueError(k + " must be positive")
        if self.max_position_fraction > self.max_total_fraction:
            raise ValueError("Position allocation cannot exceed total allocation")
        if self.max_positions * self.max_position_fraction < self.max_total_fraction:
            # It's valid to set a looser total ceiling; not invalid.
            pass

    @property
    def fee_rate(self):
        return self.fee_bps / D("10000")

    @property
    def slippage_rate(self):
        return self.slippage_bps / D("10000")


class MultiPaperPortfolio:
    def __init__(self, path="data/multi_paper.db", limits=None):
        self.path = str(path)
        self.limits = limits or PortfolioLimits()
        if self.path == ":memory:":
            raise ValueError("Portfolio must have a persistent ledger")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self):
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS portfolio_config (
                    id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_account (
                    id INTEGER PRIMARY KEY CHECK(id=1), cash TEXT NOT NULL,
                    peak_equity TEXT NOT NULL, daily_equity TEXT NOT NULL,
                    day TEXT NOT NULL, halted INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_quotes (
                    symbol TEXT PRIMARY KEY, price TEXT NOT NULL, at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_positions (
                    symbol TEXT PRIMARY KEY, qty TEXT NOT NULL,
                    entry_price TEXT NOT NULL, entry_fee TEXT NOT NULL,
                    stop_price TEXT NOT NULL, take_price TEXT NOT NULL,
                    entry_id INTEGER NOT NULL UNIQUE, opened_at TEXT NOT NULL,
                    mark_price TEXT NOT NULL, mark_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    symbol TEXT NOT NULL, side TEXT NOT NULL,
                    qty TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL,
                    pnl TEXT, reason TEXT NOT NULL, entry_id INTEGER,
                    stop_price TEXT, take_price TEXT
                );
                CREATE TABLE IF NOT EXISTS portfolio_decisions (
                    signal_key TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                    observed_at TEXT NOT NULL, action TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_alerts (
                    fill_id INTEGER PRIMARY KEY, delivered_at TEXT NOT NULL,
                    FOREIGN KEY(fill_id) REFERENCES portfolio_fills(id)
                );
            """)
            settings = json.dumps(
                {k: str(v) for k, v in asdict(self.limits).items()},
                sort_keys=True
            )
            old = db.execute("SELECT settings FROM portfolio_config WHERE id=1").fetchone()
            if old is not None and old["settings"] != settings:
                raise ValueError("Ledger risk profile differs; choose a new ledger")
            if old is None:
                db.execute("INSERT INTO portfolio_config VALUES (1,?)", (settings,))
            if not db.execute("SELECT 1 FROM portfolio_account WHERE id=1").fetchone():
                initial = str(self.limits.initial_cash)
                db.execute(
                    "INSERT INTO portfolio_account VALUES (1,?,?,?,?,0)",
                    (initial, initial, initial, datetime.now(timezone.utc).date().isoformat()),
                )

    @staticmethod
    def _symbol(symbol):
        symbol = str(symbol).upper().strip()
        if (not symbol.endswith("/USDT") or len(symbol) < 6 or
                not symbol[:-5].isalnum() or len(symbol[:-5]) > 24):
            raise ValueError("Expected Binance spot BASE/USDT symbol")
        return symbol

    def _positions(self, db):
        return [dict(row) for row in db.execute(
            "SELECT * FROM portfolio_positions ORDER BY opened_at,symbol"
        ).fetchall()]

    def _equity(self, db):
        account = db.execute("SELECT cash FROM portfolio_account WHERE id=1").fetchone()
        positions = self._positions(db)
        return D(account["cash"]) + sum(
            (D(p["qty"]) * D(p["mark_price"]) for p in positions), D(0)
        )

    def _risk(self, db, observed_at):
        acc = db.execute("SELECT * FROM portfolio_account WHERE id=1").fetchone()
        equity = self._equity(db)
        today = observed_at.date().isoformat()
        daily = D(acc["daily_equity"])
        if acc["day"] != today:
            daily = equity
            db.execute("UPDATE portfolio_account SET day=?,daily_equity=? WHERE id=1",
                       (today, str(daily)))
        peak = max(D(acc["peak_equity"]), equity)
        triggered = (
            peak > 0 and (peak-equity) / peak >= self.limits.max_drawdown_fraction
        ) or (
            daily > 0 and (daily-equity) / daily >= self.limits.daily_loss_fraction
        )
        halted = bool(acc["halted"]) or triggered
        db.execute("UPDATE portfolio_account SET peak_equity=?, halted=? WHERE id=1",
                   (str(peak), int(halted)))
        return equity, halted

    def status(self):
        with self._db() as db:
            acc = db.execute("SELECT * FROM portfolio_account WHERE id=1").fetchone()
            positions = self._positions(db)
            pnl = D(0)
            for p in positions:
                qty = D(p["qty"])
                mark = D(p["mark_price"])
                exit_price = mark * (D(1) - self.limits.slippage_rate)
                pnl += ((exit_price - D(p["entry_price"])) * qty -
                        D(p["entry_fee"]) - exit_price * qty * self.limits.fee_rate)
            return {
                "mode": "MULTI_PAPER_ONLY",
                "cash": str(acc["cash"]), "equity": str(self._equity(db)),
                "halted": bool(acc["halted"]),
                "peak_equity": acc["peak_equity"],
                "daily_start_equity": acc["daily_equity"],
                "positions": positions,
                "open_positions": len(positions),
                "unrealized_net_pnl_estimate": str(pnl),
                "total_marked_exposure": str(sum(
                    (D(p["qty"])*D(p["mark_price"]) for p in positions), D(0)
                )),
            }

    def halt(self):
        with self._db() as db:
            db.execute("UPDATE portfolio_account SET halted=1 WHERE id=1")

    def resume(self):
        with self._db() as db:
            db.execute("UPDATE portfolio_account SET halted=0 WHERE id=1")

    def _sell(self, db, position, market_price, when, reason):
        symbol = position["symbol"]
        qty = D(position["qty"])
        fill = market_price * (D(1) - self.limits.slippage_rate)
        fee = qty * fill * self.limits.fee_rate
        pnl = ((fill - D(position["entry_price"])) * qty -
               D(position["entry_fee"]) - fee)
        cash = D(db.execute(
            "SELECT cash FROM portfolio_account WHERE id=1"
        ).fetchone()["cash"])
        db.execute("DELETE FROM portfolio_positions WHERE symbol=?", (symbol,))
        db.execute("UPDATE portfolio_account SET cash=? WHERE id=1",
                   (str(cash + qty*fill-fee),))
        db.execute(
            "INSERT INTO portfolio_fills(at,symbol,side,qty,price,fee,pnl,reason,entry_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ts(when),symbol,"SELL",str(qty),str(fill),str(fee),str(pnl),
             reason,position["entry_id"]),
        )
        return {"action":"SELL","symbol":symbol,"reason":reason,
                "realized_pnl":str(pnl),"fill_price":str(fill)}

    def mark(self, symbol, price, observed_at, now=None):
        """Process a fresh verified spot quote. Never opens a position."""
        symbol = self._symbol(symbol)
        price = D(price)
        if price <= 0:
            return {"action":"REJECT","reason":"invalid_quote"}
        observed_at = utc_time(observed_at)
        now = utc_time(now or datetime.now(timezone.utc))
        delay = (now-observed_at).total_seconds()
        if delay > self.limits.mark_age_seconds or delay < -60:
            return {"action":"REJECT","reason":"stale_or_future_quote"}
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT at FROM portfolio_quotes WHERE symbol=?",
                             (symbol,)).fetchone()
            if old and utc_time(old["at"]) >= observed_at:
                return {"action":"SKIP","reason":"duplicate_or_old_quote"}
            db.execute(
                "INSERT INTO portfolio_quotes VALUES(?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET price=excluded.price,at=excluded.at",
                (symbol,str(price),ts(observed_at)),
            )
            p = db.execute(
                "SELECT * FROM portfolio_positions WHERE symbol=?", (symbol,)
            ).fetchone()
            if p is not None:
                db.execute(
                    "UPDATE portfolio_positions SET mark_price=?,mark_at=? WHERE symbol=?",
                    (str(price),ts(observed_at),symbol),
                )
            _,halted = self._risk(db, now)
            if p is None:
                return {"action":"HOLD","reason":"no_open_position","halted":halted}
            if halted:
                reason = "risk_or_manual_kill"
            elif price <= D(p["stop_price"]):
                reason = "stop_triggered"
            elif price >= D(p["take_price"]):
                reason = "take_profit_triggered"
            else:
                return {"action":"HOLD","reason":"position_open","halted":False}
            return self._sell(db,p,price,now,reason)

    def on_signal(self, symbol, action, observed_at, now=None):
        """Process real saved strategy decisions using a verified, fresh mark."""
        symbol = self._symbol(symbol)
        action = str(action).upper()
        if action not in ("BUY","SELL","HOLD"):
            raise ValueError("Unknown signal action")
        when = utc_time(now or datetime.now(timezone.utc))
        observed_at = utc_time(observed_at)
        seconds = (when-observed_at).total_seconds()
        if seconds < -60 or seconds > self.limits.signal_age_minutes*60:
            return {"action":"REJECT","reason":"stale_or_future_signal"}
        key = symbol+":"+ts(observed_at)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM portfolio_decisions WHERE signal_key=?", (key,)
            ).fetchone():
                return {"action":"DUPLICATE","reason":"already_processed"}
            previous = db.execute(
                "SELECT MAX(observed_at) FROM portfolio_decisions WHERE symbol=?",
                (symbol,),
            ).fetchone()[0]
            if previous and observed_at <= utc_time(previous):
                return {"action":"REJECT","reason":"out_of_order_signal"}
            quote = db.execute(
                "SELECT * FROM portfolio_quotes WHERE symbol=?", (symbol,)
            ).fetchone()
            if not quote or abs((when-utc_time(quote["at"])).total_seconds()) > self.limits.mark_age_seconds:
                return {"action":"REJECT","reason":"no_fresh_verified_mark"}
            if observed_at < utc_time(quote["at"]) - timedelta(minutes=3):
                return {"action":"REJECT","reason":"signal_older_than_market_mark"}
            price = D(quote["price"])
            equity, halted = self._risk(db,when)
            pos = db.execute(
                "SELECT * FROM portfolio_positions WHERE symbol=?",(symbol,)
            ).fetchone()
            result = {"action":"HOLD","reason":"no_action","symbol":symbol}
            if pos is not None and (action == "SELL" or halted):
                reason = "risk_or_manual_kill" if halted else "rule_signal"
                result = self._sell(db,pos,price,when,reason)
            elif pos is not None:
                result = {"action":"HOLD","reason":"already_open","symbol":symbol}
            elif halted:
                result = {"action":"BLOCK","reason":"kill_switch","symbol":symbol}
            elif action == "BUY":
                result = self._buy(db,symbol,price,equity,when)
            db.execute(
                "INSERT INTO portfolio_decisions VALUES(?,?,?,?,?)",
                (key,symbol,ts(observed_at),result["action"],result["reason"]),
            )
            return result

    def _buy(self, db, symbol, price, equity, when):
        positions = self._positions(db)
        limits = self.limits
        if len(positions) >= limits.max_positions:
            return {"action":"BLOCK","reason":"max_open_positions","symbol":symbol}
        for p in positions:
            if (when-utc_time(p["mark_at"])).total_seconds() > limits.max_other_mark_age_seconds:
                return {"action":"BLOCK","reason":"stale_other_position_mark","symbol":symbol}
        today = when.date().isoformat()
        n = db.execute(
            "SELECT COUNT(*) FROM portfolio_fills WHERE side='BUY' AND substr(at,1,10)=?",
            (today,),
        ).fetchone()[0]
        if n >= limits.max_entries_daily:
            return {"action":"BLOCK","reason":"daily_entry_limit","symbol":symbol}
        closed = db.execute(
            "SELECT at FROM portfolio_fills WHERE side='SELL' AND symbol=? "
            "ORDER BY id DESC LIMIT 1",(symbol,),
        ).fetchone()
        if closed and (when-utc_time(closed["at"])).total_seconds() < limits.cooldown_minutes*60:
            return {"action":"BLOCK","reason":"symbol_cooldown","symbol":symbol}
        exposure = sum((D(p["qty"])*D(p["mark_price"]) for p in positions),D(0))
        available_total = equity*limits.max_total_fraction-exposure
        cash = D(db.execute(
            "SELECT cash FROM portfolio_account WHERE id=1"
        ).fetchone()["cash"])
        budget = min(
            equity*limits.max_position_fraction,
            equity*limits.risk_per_trade/limits.stop_fraction,
            available_total,
            cash/(D(1)+limits.fee_rate),
        )
        fill = price*(D(1)+limits.slippage_rate)
        qty = floor_quantity(budget/fill) if budget > 0 else D(0)
        notional = qty*fill
        fee = notional*limits.fee_rate
        if qty <= 0 or notional < limits.min_notional or notional+fee > cash:
            return {"action":"BLOCK","reason":"insufficient_cash_or_exposure","symbol":symbol}
        stop = fill*(D(1)-limits.stop_fraction)
        take = fill*(D(1)+limits.take_fraction)
        cursor = db.execute(
            "INSERT INTO portfolio_fills(at,symbol,side,qty,price,fee,pnl,reason,stop_price,take_price) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts(when),symbol,"BUY",str(qty),str(fill),str(fee),None,
             "rule_signal",str(stop),str(take)),
        )
        db.execute(
            "INSERT INTO portfolio_positions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (symbol,str(qty),str(fill),str(fee),str(stop),str(take),
             cursor.lastrowid,ts(when),str(price),ts(when)),
        )
        db.execute("UPDATE portfolio_account SET cash=? WHERE id=1",
                   (str(cash-notional-fee),))
        return {"action":"BUY","reason":"paper_entry","symbol":symbol,
                "fill_price":str(fill),"quantity":str(qty)}

    def fills(self, limit=50):
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM portfolio_fills ORDER BY id DESC LIMIT ?",
                (min(max(int(limit),1),200),),
            ).fetchall()]

    def entry_fill(self, fill_id):
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM portfolio_fills WHERE id=? AND side='BUY'",
                (int(fill_id),),
            ).fetchone()
            return dict(row) if row else None

    def pending_alerts(self, limit=50):
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT f.* FROM portfolio_fills f "
                "LEFT JOIN portfolio_alerts a ON a.fill_id=f.id "
                "WHERE a.fill_id IS NULL ORDER BY f.id LIMIT ?",
                (min(max(int(limit),1),100),),
            ).fetchall()]

    def acknowledge_alert(self, fill_id):
        with self._db() as db:
            if not db.execute("SELECT 1 FROM portfolio_fills WHERE id=?",
                              (fill_id,)).fetchone():
                raise ValueError("Fill not found")
            db.execute(
                "INSERT OR IGNORE INTO portfolio_alerts VALUES (?,?)",
                (fill_id,ts(datetime.now(timezone.utc))),
            )

    def stats(self):
        fills = self.fills(200)
        closed = [D(f["pnl"]) for f in fills if f["side"]=="SELL"]
        wins = sum(p>0 for p in closed)
        losses = sum(p<0 for p in closed)
        gross_wins = sum((x for x in closed if x>0),D(0))
        gross_loss = -sum((x for x in closed if x<0),D(0))
        return {
            "closed_trades":len(closed),"wins":wins,"losses":losses,
            "win_rate":float(D(wins)/D(len(closed))) if closed else None,
            "realized_net_pnl":str(sum(closed,D(0))),
            "profit_factor":float(gross_wins/gross_loss) if gross_loss else None,
            "warning":"Paper fills only. Not live trading performance.",
        }
