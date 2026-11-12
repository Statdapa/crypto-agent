"""Auditable multi-coin demo performance analytics from the existing SQLite ledger.

Closed-trade results are observed PAPER fills only. Equity history records only
marks created after history collection was enabled; no backfilled, synthetic
equity points and no invented win rates. No network or private exchange API.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from statistics import mean, pstdev

from trading_engine import D, utc_time


def pct_or_none(num, den):
    return float(D(num) / D(den)) if den else None


def drawdown_metrics(points):
    """Historical observed equity marks. Do not interpolate unobserved prices."""
    if not points:
        return {"max_drawdown_fraction": None, "drawdown_usdt": None,
                "max_drawdown_at": None, "equity_points": 0}
    peak = None
    maximum = D(0)
    maximum_abs = D(0)
    worst_at = None
    for point in points:
        eq = D(point["equity"])
        if eq <= 0:
            raise ValueError("Equity mark cannot be zero or negative")
        peak = eq if peak is None else max(peak, eq)
        abs_dd = peak-eq
        frac = abs_dd/peak
        if frac > maximum:
            maximum = frac
            maximum_abs = abs_dd
            worst_at = point["at"]
    return {
        "max_drawdown_fraction": float(maximum),
        "drawdown_usdt": str(maximum_abs),
        "max_drawdown_at": worst_at,
        "equity_points": len(points),
    }


def closed_trade_rows(fills):
    """Match exactly one BUY to each SELL by entry_id, validating integrity."""
    by_id = {}
    for fill in fills:
        fid = int(fill["id"])
        if fid in by_id:
            raise ValueError("Duplicate fill ID")
        by_id[fid] = fill
    trades = []
    open_entries = set()
    for fill in sorted(fills, key=lambda x:int(x["id"])):
        side = fill["side"]
        if side == "BUY":
            if fill["entry_id"] is not None or fill["pnl"] is not None:
                raise ValueError("Malformed BUY")
            open_entries.add(int(fill["id"]))
            continue
        if side != "SELL":
            raise ValueError("Unknown fill side")
        entry_id = fill["entry_id"]
        if entry_id is None or int(entry_id) not in open_entries:
            raise ValueError("SELL has no unmatched BUY entry")
        entry_id = int(entry_id)
        buy = by_id[entry_id]
        if buy["symbol"] != fill["symbol"] or D(buy["qty"]) != D(fill["qty"]):
            raise ValueError("Mismatched paired fills")
        if fill["pnl"] is None:
            raise ValueError("Missing realized PnL")
        pnl = D(fill["pnl"])
        entry_cost = D(buy["price"]) * D(buy["qty"]) + D(buy["fee"])
        trades.append({
            "entry_id":entry_id,
            "exit_id":int(fill["id"]),
            "symbol":fill["symbol"],
            "opened_at":buy["at"], "closed_at":fill["at"],
            "entry_price":str(buy["price"]),"exit_price":str(fill["price"]),
            "quantity":str(fill["qty"]),
            "entry_fee":str(buy["fee"]),"exit_fee":str(fill["fee"]),
            "net_pnl":str(pnl),
            "return_on_entry_notional_fraction":pct_or_none(pnl,entry_cost),
            "reason":fill["reason"],
        })
        open_entries.remove(entry_id)
    return trades, len(open_entries)


def outcome_summary(trades):
    profits = [D(x["net_pnl"]) for x in trades]
    count = len(profits)
    wins = [p for p in profits if p>0]
    losses = [p for p in profits if p<0]
    flat = count-len(wins)-len(losses)
    gw = sum(wins,D(0))
    gl = -sum(losses,D(0))
    return {
        "trades":count,
        "wins":len(wins),
        "losses":len(losses),
        "breakeven":flat,
        "win_rate":len(wins)/count if count else None,
        "net_pnl_usdt":str(sum(profits,D(0))),
        "gross_winning_pnl_usdt":str(gw),
        "gross_losing_pnl_usdt":str(gl),
        "profit_factor":float(gw/gl) if gl>0 else None,
        "average_win_usdt":str(gw/len(wins)) if wins else None,
        "average_loss_usdt":str(-gl/len(losses)) if losses else None,
        "expectancy_usdt_per_closed_trade":str(sum(profits,D(0))/count) if count else None,
        "median_trade_return_fraction": (
            sorted([x["return_on_entry_notional_fraction"] for x in trades])[(count-1)//2]
            if count % 2 and count else (
                sum(sorted([x["return_on_entry_notional_fraction"] for x in trades])[count//2-1:count//2+1])/2
                if count else None
            )
        ),
    }


def build_report(fills, marks, current_status, initial_cash):
    trades, unclosed = closed_trade_rows(fills)
    grouped = defaultdict(list)
    for trade in trades:
        grouped[trade["symbol"]].append(trade)
    breakdown = {symbol:outcome_summary(items) for symbol,items in sorted(grouped.items())}
    dd = drawdown_metrics(marks)
    summary = outcome_summary(trades)
    # A realized close is historical. Open estimated PnL is NOT counted as realized.
    mark_times = [utc_time(row["at"]) for row in marks]
    time_coverage = {
        "first_mark":min(mark_times).isoformat() if mark_times else None,
        "last_mark":max(mark_times).isoformat() if mark_times else None,
        "equity_history_partial":True,
        "reason":"Marks are stored only after equity-history feature was installed; prior prices were not reconstructed.",
    }
    summary.update({
        "initial_cash_usdt":str(initial_cash),
        "current_cash_usdt":str(current_status["cash"]),
        "current_marked_equity_usdt":str(current_status["equity"]),
        "current_unrealized_net_pnl_estimate_usdt":str(
            current_status["unrealized_net_pnl_estimate"]
        ),
        "currently_open_positions":len(current_status["positions"]),
        "unclosed_entries_in_ledger":unclosed,
        "marked_equity_return_fraction": (
            float((D(current_status["equity"])-D(initial_cash))/D(initial_cash))
            if D(initial_cash)>0 else None
        ),
    })
    return {
        "mode":"MULTI_PAPER_ONLY",
        "summary":summary,
        "drawdown_observed_only":dd,
        "equity_coverage":time_coverage,
        "by_coin":breakdown,
        "closed_trades":trades,
        "equity_curve_observed":marks,
        "caveats":[
            "Observed paper fills and marks, not real exchange performance or predictive accuracy.",
            "Drawdown excludes prices not polled and history before tracking started.",
            "No annualized Sharpe ratio: irregular/asynchronous marks are not uniform time-period returns.",
            "The scanner dynamically selects candidates; historical screening biases must be modeled separately.",
            "Stop/take fills occur at observed quotes and may miss intraperiod or gap-through prices.",
        ],
    }


def read_ledger(path):
    if not Path(path).is_file():
        raise FileNotFoundError("Paper ledger not found: "+str(path))
    with closing(sqlite3.connect("file:"+str(Path(path).resolve())+"?mode=ro",uri=True)) as db:
        db.row_factory=sqlite3.Row
        needed={"portfolio_fills","portfolio_account","portfolio_config"}
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not needed.issubset(tables):
            raise ValueError("Missing multi-portfolio tables")
        fills=[dict(r) for r in db.execute("SELECT * FROM portfolio_fills ORDER BY id")]
        marks = (
            [dict(r) for r in db.execute(
                "SELECT at,event,symbol,equity,cash,open_positions "
                "FROM portfolio_equity_history ORDER BY id")]
            if "portfolio_equity_history" in tables else []
        )
        config=json.loads(db.execute(
            "SELECT settings FROM portfolio_config WHERE id=1"
        ).fetchone()[0])
    return fills,marks,D(config["initial_cash"])


def report_from_ledger(path):
    from multi_portfolio import MultiPaperPortfolio
    trader=MultiPaperPortfolio(path)
    fills,marks,initial=read_ledger(path)
    return build_report(fills,marks,trader.status(),initial)


def main(argv=None):
    parser=argparse.ArgumentParser(description="Multi-paper performance report; no exchange orders.")
    parser.add_argument("--ledger",default="data/multi_paper.db")
    parser.add_argument("--output",default="data/reports/multi_performance.json")
    parser.add_argument("--trades-csv",default="data/reports/multi_closed_trades.csv")
    parser.add_argument("--equity-csv",default="data/reports/multi_observed_equity.csv")
    args=parser.parse_args(argv)
    report=report_from_ledger(args.ledger)
    for name in ("output","trades_csv","equity_csv"):
        path=Path(getattr(args,name))
        path.parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    for filename,rows,keys in (
        (args.trades_csv,report["closed_trades"],
         ("entry_id","exit_id","symbol","opened_at","closed_at","entry_price","exit_price",
          "quantity","entry_fee","exit_fee","net_pnl","return_on_entry_notional_fraction","reason")),
        (args.equity_csv,report["equity_curve_observed"],
         ("at","event","symbol","equity","cash","open_positions")),
    ):
        with open(filename,"w",newline="",encoding="utf-8") as file:
            writer=csv.DictWriter(file,fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({
        "summary":report["summary"],
        "drawdown_observed_only":report["drawdown_observed_only"],
        "reports":{"json":args.output,"trades_csv":args.trades_csv,"equity_csv":args.equity_csv},
    },indent=2))


if __name__=="__main__":
    main()
