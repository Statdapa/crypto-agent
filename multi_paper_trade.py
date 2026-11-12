"""Fully virtual multi-coin Binance spot scanner and demo executor.

Uses public Binance market data and the existing rule-based indicators;
does not call exchange private endpoints or place exchange orders.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from binance_universe import BinanceSpotUniverse
from multi_portfolio import MultiPaperPortfolio
from trading_engine import D, utc_time

load_dotenv()

SKIP_BASES = {"USDC","USDT","FDUSD","TUSD","USDP","DAI","PAX","BUSD",
              "EUR","TRY","BRL","JPY","GBP","AUD","USTC","WBTC","WBETH",
              "BETH","BTCB","ETHW","XAUT","PAXG"}
SKIP_SUFFIXES = ("UP","DOWN","BULL","BEAR")


def safe_candidate(symbol):
    symbol = str(symbol).upper()
    return (
        symbol.isalnum() and len(symbol) >= 2 and
        symbol not in SKIP_BASES and
        not symbol.endswith(SKIP_SUFFIXES)
    )


def rank_candidates(scan_result, max_candidates=8):
    """Top liquid movers. A mover is NOT a buy signal."""
    eligible = []
    for item in scan_result.get("top_movers", []):
        base = item.get("coin", "")
        if (safe_candidate(base) and item.get("quote_volume", 0) >= 2_000_000
                and abs(float(item.get("change_24h", 0))) <= 40):
            eligible.append(base)
        if len(eligible) >= max_candidates:
            break
    return eligible


def _signal_from_market(universe, base, quote, fg):
    """Compute existing strategy from verified Binance spot quote + closed 4h bars."""
    from crypto_intelligence_agent import (
        get_analysis, generate_signal, save_market_snapshot,
        is_number,
    )
    # The full OHLCV analysis is sourced from Binance. For the smaller
    # candidate set, fail if the indicator set is incomplete.
    analysis = get_analysis(base)
    if not is_number(analysis.get("rsi")) or not is_number(analysis.get("macd")):
        return {"action":"SKIP","reason":"incomplete_indicators","symbol":base}
    result = generate_signal(quote, analysis, fg)
    save_market_snapshot(base, quote, analysis, fg, result)
    return result


def scan_cycle(
    portfolio, universe, source_db=None, max_candidates=8, min_volume=2_000_000,
    scan_all=None, quote_override=None, signal_override=None,
):
    """Mark ALL existing positions before evaluating new entry candidates."""
    now = datetime.now(timezone.utc)
    result = {
        "mode":"MULTI_PAPER_ONLY","at":now.isoformat(),
        "marks":[],"candidates":[],"decisions":[],
        "scan_error":None,
    }
    status = portfolio.status()
    for pos in status["positions"]:
        base = pos["symbol"].split("/")[0]
        try:
            quote = quote_override(base) if quote_override else universe.ticker_24h(base)
            if quote.get("coin") != base or quote.get("pair") != base+"USDT":
                raise ValueError("Quote pair mismatch")
            at = utc_time(quote["fetched_at"])
            mark = portfolio.mark(pos["symbol"], quote["price"], at)
            result["marks"].append(mark)
        except Exception as exc:
            result["marks"].append(
                {"symbol":pos["symbol"],"action":"SKIP",
                 "reason":"quote_unavailable","type":type(exc).__name__}
            )

    try:
        scan = scan_all if scan_all is not None else universe.scan(
            min_quote_volume=min_volume, top=30
        )
        candidates = rank_candidates(scan,max_candidates)
    except Exception as exc:
        result["scan_error"] = type(exc).__name__
        return result

    result["candidates"] = candidates
    for base in candidates:
        try:
            quote = quote_override(base) if quote_override else universe.ticker_24h(base)
            if quote.get("coin") != base or quote.get("pair") != base+"USDT":
                raise ValueError("Wrong market quote")
            observed = utc_time(quote["fetched_at"])
            symbol = base+"/USDT"
            portfolio.mark(symbol,quote["price"],observed)
            if signal_override is not None:
                signal = signal_override(base,quote)
            else:
                # Only a small bounded set of markets fetches detailed 4h data.
                from crypto_intelligence_agent import get_fear_greed
                fg = get_fear_greed()
                signal = _signal_from_market(universe,base,quote,fg)
            if signal.get("signal") not in ("BUY","SELL","HOLD"):
                result["decisions"].append({"symbol":symbol,"action":"SKIP",
                                             "reason":"invalid_or_missing_signal"})
                continue
            decision = portfolio.on_signal(
                symbol,signal["signal"],datetime.now(timezone.utc)
            )
            result["decisions"].append(decision)
        except Exception as exc:
            result["decisions"].append(
                {"symbol":base+"/USDT","action":"SKIP",
                 "reason":"candidate_unavailable","type":type(exc).__name__}
            )
    return result


def _fmt(v):
    return format(D(v),",.4f")


def format_multi_status(portfolio):
    info=portfolio.status()
    stats=portfolio.stats()
    lines=[
        "<b>MULTI-COIN DEMO PORTFOLIO</b>",
        "Mode: PAPER ONLY",
        "Cash: "+_fmt(info["cash"])+" USDT",
        "Equity (marked): "+_fmt(info["equity"])+" USDT",
        "Exposure: "+_fmt(info["total_marked_exposure"])+" USDT",
        "Estimated unrealized net PnL: "+
            format(D(info["unrealized_net_pnl_estimate"]),"+,.4f")+" USDT",
        "Risk halt: "+("ON" if info["halted"] else "OFF"),
        "Open positions: "+str(info["open_positions"]),
    ]
    for p in info["positions"]:
        pnl = ((D(p["mark_price"])*(D("1")-portfolio.limits.slippage_rate)-
                D(p["entry_price"]))*D(p["qty"])-
               D(p["entry_fee"])-
               D(p["mark_price"])*(D("1")-portfolio.limits.slippage_rate)*
               D(p["qty"])*portfolio.limits.fee_rate)
        lines.extend([
            "<b>"+html.escape(p["symbol"])+" — OPEN LONG</b>",
            "Entry: "+_fmt(p["entry_price"])+" | Mark: "+_fmt(p["mark_price"]),
            "SL: "+_fmt(p["stop_price"])+" | TP: "+_fmt(p["take_price"]),
            "Qty: "+html.escape(p["qty"])+" | uPnL: "+
            format(pnl,"+,.4f")+" USDT",
            "Last mark UTC: "+html.escape(p["mark_at"]),
        ])
    lines.extend([
        "Closed: "+str(stats["closed_trades"])+" | W/L: "+
            str(stats["wins"])+"/"+str(stats["losses"]),
        "Realized net PnL: "+format(D(stats["realized_net_pnl"]),"+,.4f")+" USDT",
        "Win rate: "+("N/A" if stats["win_rate"] is None
                     else format(stats["win_rate"]*100,".2f")+"%"),
        "<i>Simulated fills at observed prices; not real exchange orders.</i>",
    ])
    return "\n".join(lines)


def format_multi_positions(portfolio):
    """Explicit view of active positions in the shared multi-coin ledger."""
    info = portfolio.status()
    lines = [
        "<b>MULTI-COIN DEMO — OPEN POSITIONS</b>",
        "Mode: PAPER ONLY",
        "Active: " + str(info["open_positions"]),
        "Marked equity: " + _fmt(info["equity"]) + " USDT",
    ]
    if not info["positions"]:
        lines += [
            "No open positions.",
            "See /multitrades for previous closes or /multi for the account.",
        ]
        return "\n".join(lines)
    for p in info["positions"]:
        entry = D(p["entry_price"])
        qty = D(p["qty"])
        mark = D(p["mark_price"])
        exit_mark = mark * (D(1) - portfolio.limits.slippage_rate)
        est_pnl = ((exit_mark - entry) * qty
                   - D(p["entry_fee"])
                   - exit_mark * qty * portfolio.limits.fee_rate)
        lines += [
            "",
            "<b>OPEN LONG — " + html.escape(p["symbol"]) + "</b>",
            "Entry: " + _fmt(entry) + " USDT",
            "Current mark: " + _fmt(mark) + " USDT",
            "Quantity: " + html.escape(p["qty"]),
            "Stop loss: " + _fmt(p["stop_price"]) + " USDT",
            "Take profit: " + _fmt(p["take_price"]) + " USDT",
            "Unrealized PnL (net estimate): " + format(est_pnl, "+,.4f") + " USDT",
            "Last mark UTC: " + html.escape(p["mark_at"]),
        ]
    lines.append("<i>Virtual positions; marked prices can be stale.</i>")
    return "\n".join(lines)


def format_multi_fills(portfolio):
    rows=portfolio.fills(10)
    if not rows:
        return "MULTI DEMO: belum ada transaksi."
    lines=["<b>MULTI-COIN DEMO FILLS</b>"]
    for r in rows:
        pnl=("" if r["pnl"] is None else
             " | net PnL "+format(D(r["pnl"]),"+,.4f")+" USDT")
        lines.append(
            "#"+str(r["id"])+" "+r["side"]+" "+
            html.escape(r["symbol"])+" @ "+_fmt(r["price"])+
            pnl+" ("+html.escape(r["reason"])+")"
        )
    return "\n".join(lines)


def format_fill(row, portfolio=None):
    lines=[
        "<b>DEMO "+("OPEN LONG" if row["side"]=="BUY" else "CLOSE LONG")+"</b>",
        "Pair: <b>"+html.escape(row["symbol"])+"</b>",
        "Fill: "+_fmt(row["price"])+" USDT",
        "Qty: "+html.escape(row["qty"]),
        "Fee: "+_fmt(row["fee"])+" USDT",
        "Reason: "+html.escape(row["reason"]),
        "UTC: "+html.escape(row["at"]),
    ]
    if row["side"]=="BUY":
        lines += ["SL: "+_fmt(row["stop_price"])+" USDT",
                  "TP: "+_fmt(row["take_price"])+" USDT"]
    else:
        pnl=D(row["pnl"])
        entry = (portfolio.entry_fill(row["entry_id"])
                 if portfolio is not None and row.get("entry_id") else None)
        if entry is not None:
            lines.append("Entry price: "+_fmt(entry["price"])+" USDT")
            lines.append("Original SL: "+_fmt(entry["stop_price"])+" USDT")
            lines.append("Original TP: "+_fmt(entry["take_price"])+" USDT")
            invested = D(entry["price"])*D(entry["qty"])+D(entry["fee"])
            if invested > 0:
                lines.append("Return on invested notional: "+
                             format(pnl/invested*D(100),"+,.2f")+"%")
        lines += ["Realized PnL NET: "+format(pnl,"+,.4f")+" USDT",
                  "Status: "+("PROFIT" if pnl>0 else "LOSS" if pnl<0 else "FLAT")]
    lines.append("<i>Paper simulation only. No live exchange order.</i>")
    return "\n".join(lines)


def send_pending(portfolio, sender=None):
    from paper_demo import telegram_send
    sender=sender or telegram_send
    token=os.getenv("TELEGRAM_TOKEN","")
    chat=os.getenv("TELEGRAM_CHAT_ID","")
    sent=0
    for fill in portfolio.pending_alerts():
        if not token or not chat or not sender(token,chat,format_fill(fill,portfolio)):
            break
        portfolio.acknowledge_alert(fill["id"])
        sent+=1
    return {"sent":sent,"pending":len(portfolio.pending_alerts())}


def cli(argv=None):
    p=argparse.ArgumentParser(description="Shared-wallet MULTI-COIN PAPER ONLY")
    p.add_argument("command",choices=("watch","scan","status","fills","stats","notify","halt","resume"))
    p.add_argument("--ledger",default=os.getenv("MULTI_PAPER_DB_PATH","data/multi_paper.db"))
    p.add_argument("--poll-seconds",type=int,default=60)
    p.add_argument("--max-cycles",type=int,default=0)
    p.add_argument("--max-candidates",type=int,default=8)
    p.add_argument("--ack-resume-risk",action="store_true")
    args=p.parse_args(argv)
    if args.poll_seconds<10 or args.max_cycles<0 or not 1<=args.max_candidates<=12:
        p.error("Invalid watch/candidate limits")
    portfolio=MultiPaperPortfolio(args.ledger)
    if args.command=="status":
        print(format_multi_status(portfolio))
    elif args.command=="fills":
        print(json.dumps(portfolio.fills(),indent=2))
    elif args.command=="stats":
        print(json.dumps(portfolio.stats(),indent=2))
    elif args.command=="notify":
        print(json.dumps(send_pending(portfolio),indent=2))
    elif args.command=="halt":
        portfolio.halt()
        print("Multi demo HALTED; positions will close on next verified quote.")
    elif args.command=="resume":
        if not args.ack_resume_risk:
            p.error("Manual risk acknowledgement required")
        portfolio.resume()
        print("Multi demo resumed")
    else:
        universe=BinanceSpotUniverse()
        if args.command=="scan":
            print(json.dumps(universe.scan(min_quote_volume=2_000_000,top=30),indent=2))
            return
        cycles=0
        print("MULTI PAPER ONLY: no exchange credentials and no live trades.",flush=True)
        try:
            while args.max_cycles==0 or cycles<args.max_cycles:
                outcome=scan_cycle(portfolio,universe,max_candidates=args.max_candidates)
                outcome["telegram"]=send_pending(portfolio)
                print(json.dumps(outcome),flush=True)
                cycles+=1
                if args.max_cycles==0 or cycles<args.max_cycles:
                    time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            print("Multi demo watch stopped")
    return 0


if __name__=="__main__":
    raise SystemExit(cli())
