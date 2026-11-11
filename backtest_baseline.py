"""Causal OHLC baseline backtest: SMA(20/50), next-bar-open execution.

This is NOT a backtest of crypto_intelligence_agent's multi-indicator rules.
It is a transparent infrastructure benchmark; there is no return guarantee.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from trading_engine import D, RiskLimits, floor_quantity, utc_time


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    @classmethod
    def from_row(cls, row):
        # Timestamp refers to candle OPEN. Only completed historical candles are accepted.
        time = utc_time(row["timestamp"])
        values = [D(row[key]) for key in ("open", "high", "low", "close")]
        o, h, l, c = values
        if min(values) <= 0 or h < max(o,c,l) or l > min(o,c,h):
            raise ValueError(f"Invalid OHLC data at {time.isoformat()}")
        return cls(time, o,h,l,c)


def read_candles(filename: str):
    with open(filename, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("CSV is empty")
    candles = [Candle.from_row(x) for x in rows]
    if any(a.timestamp >= b.timestamp for a,b in zip(candles,candles[1:])):
        raise ValueError("Timestamps must be strictly increasing (no duplicates)")
    return candles


def _sma(closes, end, period):
    # end is an inclusive historical index. Never reads future bars.
    if end+1 < period:
        return None
    return sum(closes[end-period+1:end+1],D(0)) / D(period)


def backtest_sma(candles, limits: RiskLimits = RiskLimits(), fast=20, slow=50):
    if not (isinstance(fast,int) and isinstance(slow,int) and 1 < fast < slow):
        raise ValueError("Require 1 < fast < slow")
    if len(candles) <= slow+1:
        raise ValueError("Insufficient complete candles")
    dates = [x.timestamp for x in candles]
    if any(a >= b for a,b in zip(dates,dates[1:])):
        raise ValueError("Unsorted or duplicated candles")
    cash=limits.initial_cash
    qty=D(0)
    entry=D(0)
    entry_fee=D(0)
    stop=D(0)
    take=D(0)
    trades=[]
    last_equity=cash
    peak=cash
    max_dd=D(0)
    close_prices=[x.close for x in candles]
    fees=limits.fee_rate
    slip=limits.slip_rate
    for i in range(slow + 1, len(candles)):
        bar=candles[i]
        prev=i-1
        prevprev=i-2
        fast_prev=_sma(close_prices,prev,fast)
        slow_prev=_sma(close_prices,prev,slow)
        fast_older=_sma(close_prices,prevprev,fast)
        slow_older=_sma(close_prices,prevprev,slow)
        buy=(fast_older<=slow_older and fast_prev>slow_prev)
        exit_signal=(fast_older>=slow_older and fast_prev<slow_prev)
        # Signals are known at the PREVIOUS close; fill at THIS open.
        if qty>0 and exit_signal:
            fill=bar.open*(D(1)-slip)
            fee=fill*qty*fees
            pnl=(fill-entry)*qty-entry_fee-fee
            cash+=fill*qty-fee
            trades.append({"exit_at":bar.timestamp.isoformat(),"pnl":str(pnl),
                           "reason":"crossover","exit_price":str(fill)})
            qty=D(0)
        if qty==0 and buy:
            fill=bar.open*(D(1)+slip)
            max_notional=min(cash/(D(1)+fees),
                             cash*limits.max_position_fraction,
                             cash*limits.max_risk_fraction/limits.stop_fraction)
            purchase=floor_quantity(max_notional/fill)
            cost=purchase*fill
            fee=cost*fees
            if cost>=limits.min_notional and cost+fee<=cash and purchase>0:
                cash-=cost+fee
                qty=purchase
                entry=fill
                entry_fee=fee
                stop=entry*(D(1)-limits.stop_fraction)
                take=entry*(D(1)+limits.take_fraction)
        if qty>0:
            # Gap at open executes at open. If stop and take both occur
            # in a single candle, pessimistically choose STOP first.
            if bar.open<=stop:
                trigger=bar.open
                reason="gap_stop"
            elif bar.open>=take:
                trigger=bar.open
                reason="gap_take"
            elif bar.low<=stop:
                trigger=stop
                reason="stop_first"
            elif bar.high>=take:
                trigger=take
                reason="take_profit"
            else:
                trigger=None
                reason=""
            if trigger is not None:
                fill=trigger*(D(1)-slip)
                fee=fill*qty*fees
                pnl=(fill-entry)*qty-entry_fee-fee
                cash+=fill*qty-fee
                trades.append({"exit_at":bar.timestamp.isoformat(),"pnl":str(pnl),
                               "reason":reason,"exit_price":str(fill)})
                qty=D(0)
        equity=cash+qty*bar.close
        peak=max(peak,equity)
        dd=(peak-equity)/peak if peak>0 else D(1)
        max_dd=max(max_dd,dd)
        last_equity=equity
    closed=len(trades)
    wins=sum(D(x["pnl"])>0 for x in trades)
    return {
        "benchmark": f"SMA({fast},{slow}) cross / next-open / conservative stop-first",
        "input_bars":len(candles),
        "processed_bars":len(candles)-slow-1,
        "initial_equity":str(limits.initial_cash),
        "ending_mark_to_market_equity":str(last_equity),
        "net_return_pct":float((last_equity/limits.initial_cash-D(1))*D(100)),
        "max_mark_to_market_drawdown_pct":float(max_dd*D(100)),
        "closed_trades":closed,
        "wins":wins,
        "win_rate_closed":float(D(wins)/D(closed)) if closed else None,
        "open_position_quantity":str(qty),
        "assumptions":{"fee_bps":str(limits.fee_bps),"slippage_bps":str(limits.slippage_bps),
                       "stop_fraction":str(limits.stop_fraction),
                       "take_fraction":str(limits.take_fraction)},
        "trades":trades,
        "disclaimer":"Baseline only; not an agent backtest or evidence of future live results.",
    }


def main():
    parser=argparse.ArgumentParser(description="SMA baseline backtest (no live orders)")
    parser.add_argument("--csv",required=True,help="CSV: timestamp,open,high,low,close. Timestamp must contain timezone.")
    parser.add_argument("--fast",type=int,default=20)
    parser.add_argument("--slow",type=int,default=50)
    parser.add_argument("--output",default="",help="Optional report JSON output")
    args=parser.parse_args()
    result=backtest_sma(read_candles(args.csv),fast=args.fast,slow=args.slow)
    if args.output:
        Path(args.output).parent.mkdir(parents=True,exist_ok=True)
        Path(args.output).write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
