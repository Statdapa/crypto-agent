"""Causal multi-coin RSI/MACD/MA/Bollinger historical research backtest.

Does NOT reproduce historical Binance-wide scanner selection or historical
Fear & Greed (neutral proxy=50). Fixed CSV symbol universe has survivorship
bias. This is not a certified replay of the running portfolio algorithm.

Signals from fully closed 4h bar t-1; entry/exit at next bar t open. Stops/
takes use OHLC bar t, STOP-first if both touched. No forward candle leakage.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path

from backtest_baseline import read_candles
from multi_portfolio import PortfolioLimits
from trading_engine import D, floor_quantity


@dataclass
class SimPosition:
    symbol: str
    qty: Decimal
    entry_price: Decimal
    entry_fee: Decimal
    stop: Decimal
    take: Decimal
    opened_at: str


def history_signal(bars, idx, neutral_fg=50):
    """Closed bars through idx inclusive only. Computes the live signal rules."""
    if idx<40:
        return "HOLD"
    from crypto_intelligence_agent import (
        calculate_ma,calculate_macd,calculate_rsi,calculate_bollinger,
        generate_signal,
    )
    closes=[float(x.close) for x in bars[:idx+1]]
    rsi=calculate_rsi(closes)
    m,ms,_=calculate_macd(closes)
    up,mid,low=calculate_bollinger(closes)
    if any(x is None for x in (rsi,m,ms,up,low)):
        return "HOLD"
    ch24=(float(bars[idx].close/bars[idx-6].close)-1)*100
    signal=generate_signal(
        {"price":float(bars[idx].close),"change_24h":ch24},
        {"rsi":rsi,"macd":m,"macd_signal":ms,
         "bb_upper":up,"bb_lower":low,
         "ma7":calculate_ma(closes,7),"ma14":calculate_ma(closes,14)},
        {"value":neutral_fg},
    )
    return signal["signal"]


def _close(position, open_price, reason, limits, date):
    fill=D(open_price)*(D(1)-limits.slippage_bps/D(10000))
    fee=fill*position.qty*limits.fee_bps/D(10000)
    pnl=(fill-position.entry_price)*position.qty-position.entry_fee-fee
    proceeds=fill*position.qty-fee
    return proceeds,{
        "symbol":position.symbol,"opened_at":position.opened_at,
        "closed_at":date.isoformat(),"entry":str(position.entry_price),
        "exit":str(fill),"qty":str(position.qty),
        "net_pnl":str(pnl),"reason":reason,
    }


def backtest(candle_map,limits=None,train_fraction=.7,neutral_fg=50):
    """Shared-capital test on synchronized 4h CSV bars.

    Historical 24h top movers are unavailable without full archived universe.
    To prevent look-ahead, fixed symbols are always considered in sorted order.
    """
    limits=limits or PortfolioLimits()
    if not .5 <= train_fraction <= .9:
        raise ValueError("Train fraction must be between 0.5 and 0.9")
    if neutral_fg!=50:
        raise ValueError("Only explicit neutral F&G approximation supported")
    if not candle_map:
        raise ValueError("No candle data")
    symbols=sorted(candle_map)
    for sym in symbols:
        if not sym.endswith("/USDT"):
            raise ValueError("Only USDT spot symbols supported")
    common=set(x.timestamp for x in next(iter(candle_map.values())))
    for bars in candle_map.values():
        common &= {x.timestamp for x in bars}
    common=sorted(common)
    if len(common)<100:
        raise ValueError("Need >=100 synchronized, completed 4h candles")
    if any((b-a)!=timedelta(hours=4) for a,b in zip(common,common[1:])):
        raise ValueError("Gaps in synchronized 4h history")
    dataset={}
    for symbol in symbols:
        by_time={x.timestamp:x for x in candle_map[symbol]}
        dataset[symbol]=[by_time[t] for t in common]
    split=int(len(common)*train_fraction)
    cash=limits.initial_cash
    positions={}
    trades=[]
    equity_curve=[]
    peak=cash
    ddmax=D(0)
    day_equity=cash
    day=None
    day_entries=0
    last_close={}
    halted=False
    fees=limits.fee_bps/D(10000)
    slip=limits.slippage_bps/D(10000)

    # First 60 bars warm indicators; each bar receives signal from t-1 close.
    for i in range(60,len(common)):
        date=common[i]
        if date.date()!=day:
            day=date.date()
            day_equity=(cash+sum((p.qty*dataset[s][i-1].close for s,p in positions.items()),D(0)))
            day_entries=0

        # Exit rules established before seeing current bar highs/lows.
        for symbol in sorted(list(positions)):
            pos=positions[symbol]
            bar=dataset[symbol][i]
            signal=history_signal(dataset[symbol],i-1)
            reason=None
            exit_ref=None
            if halted:
                reason="halted"
                exit_ref=bar.open
            elif bar.open<=pos.stop:
                reason="stop_gap"
                exit_ref=bar.open
            elif bar.open>=pos.take:
                reason="take_gap"
                exit_ref=bar.open
            elif signal=="SELL":
                reason="signal_sell"
                exit_ref=bar.open
            elif bar.low<=pos.stop:
                reason="stop_touch"
                exit_ref=pos.stop
            elif bar.high>=pos.take:
                reason="take_touch"
                exit_ref=pos.take
            if reason:
                proceeds,trade=_close(pos,exit_ref,reason,limits,date)
                cash+=proceeds
                trades.append(trade)
                last_close[symbol]=date
                del positions[symbol]

        # New entries are NOT allowed if a loss limit has been breached.
        # Mark existing positions using their current OPEN before entry sizing.
        eq_open=cash+sum((p.qty*dataset[s][i].open for s,p in positions.items()),D(0))
        if peak>0 and (peak-eq_open)/peak >= limits.max_drawdown_fraction:
            halted=True
        if day_equity>0 and (day_equity-eq_open)/day_equity >= limits.daily_loss_fraction:
            halted=True

        for symbol in symbols:
            if halted or symbol in positions or len(positions)>=limits.max_positions:
                continue
            if day_entries>=limits.max_entries_daily:
                break
            if symbol in last_close and date-last_close[symbol]<timedelta(minutes=limits.cooldown_minutes):
                continue
            bar=dataset[symbol][i]
            # Closed bar i-1 is the only source for signal.
            if history_signal(dataset[symbol],i-1)!="BUY":
                continue
            eq_open=cash+sum((p.qty*dataset[s][i].open for s,p in positions.items()),D(0))
            exposure=sum((p.qty*dataset[s][i].open for s,p in positions.items()),D(0))
            available_total=eq_open*limits.max_total_fraction-exposure
            budget=min(eq_open*limits.max_position_fraction,
                       eq_open*limits.risk_per_trade/limits.stop_fraction,
                       available_total,cash/(D(1)+fees))
            fill=bar.open*(D(1)+slip)
            qty=floor_quantity(budget/fill) if budget>0 else D(0)
            notional=fill*qty
            fee=notional*fees
            if qty<=0 or notional<limits.min_notional or notional+fee>cash:
                continue
            cash-=notional+fee
            stop=fill*(D(1)-limits.stop_fraction)
            take=fill*(D(1)+limits.take_fraction)
            pos=SimPosition(symbol,qty,fill,fee,stop,take,date.isoformat())
            positions[symbol]=pos
            day_entries+=1
            # Intrabar stop/target after entry. Stop-first when both touched.
            if bar.low<=stop:
                proceeds,trade=_close(pos,stop,"same_bar_stop",limits,date)
                cash+=proceeds;trades.append(trade);last_close[symbol]=date
                del positions[symbol]
            elif bar.high>=take:
                proceeds,trade=_close(pos,take,"same_bar_take",limits,date)
                cash+=proceeds;trades.append(trade);last_close[symbol]=date
                del positions[symbol]

        eq_close=cash+sum((p.qty*dataset[s][i].close for s,p in positions.items()),D(0))
        peak=max(peak,eq_close)
        dd=(peak-eq_close)/peak if peak>0 else D(1)
        ddmax=max(ddmax,dd)
        equity_curve.append({
            "at":date.isoformat(),"equity":str(eq_close),
            "segment":"train" if i<split else "test",
        })
        if peak>0 and dd>=limits.max_drawdown_fraction:
            halted=True

    segment_trades={"train":[],"test":[]}
    for trade in trades:
        t=datetime.fromisoformat(trade["closed_at"])
        segment_trades["train" if t<common[split] else "test"].append(trade)

    def metrics(rows):
        profits=[D(x["net_pnl"]) for x in rows]
        pos=[x for x in profits if x>0]
        neg=[x for x in profits if x<0]
        gw=sum(pos,D(0));gl=-sum(neg,D(0))
        return {
            "closed_trades":len(rows),
            "wins":len(pos),"losses":len(neg),
            "win_rate":len(pos)/len(rows) if rows else None,
            "net_pnl_usdt":str(sum(profits,D(0))),
            "expectancy_usdt":str(sum(profits,D(0))/len(rows)) if rows else None,
            "profit_factor":float(gw/gl) if gl>0 else None,
        }

    return {
        "mode":"CAUSAL_FIXED_UNIVERSE_RESEARCH_ONLY",
        "symbols":symbols,"start":common[0].isoformat(),
        "end":common[-1].isoformat(),
        "training_ends":common[split-1].isoformat(),
        "testing_starts":common[split].isoformat(),
        "approximation":"Fear & Greed held neutral=50; no historical dynamic Binance scanner candidate ranking",
        "initial_equity_usdt":str(limits.initial_cash),
        "ending_equity_marked_usdt":equity_curve[-1]["equity"],
        "max_drawdown_fraction":float(ddmax),
        "halted":halted,
        "train":metrics(segment_trades["train"]),
        "test":metrics(segment_trades["test"]),
        "all":metrics(trades),
        "open_positions_at_end":len(positions),
        "trades":trades,"equity_curve":equity_curve,
        "caveats":[
            "Fixed symbol sample, NOT historical dynamic scanner replication.",
            "No historical Fear & Greed; assumed neutral rather than using future values.",
            "Same-bar stop/target resolution favors stop. Open-to-stop gaps can exceed SL.",
            "Slippage and fees constant; actual market liquidity and impact omitted.",
            "Position cash/exposure shared; fills hypothetical at bar open or OHLC thresholds.",
            "Train/test temporal split is a REPORTING split, no parameter optimization yet.",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="Causal multi-coin fixed-universe 4h research backtest")
    p.add_argument("--csv",nargs="+",required=True,
                   help="SYMBOL=PATH, e.g. BTC/USDT=data/raw/BTC_USDT_4h.csv")
    p.add_argument("--train-fraction",type=float,default=.7)
    p.add_argument("--output",default="data/reports/multi_backtest.json")
    args=p.parse_args(argv)
    candles={}
    for assignment in args.csv:
        if "=" not in assignment:
            p.error("Use SYMBOL=CSV_PATH")
        symbol,path=assignment.split("=",1)
        symbol=symbol.upper()
        if symbol in candles:
            p.error("Duplicate symbol")
        candles[symbol]=read_candles(path)
    report=backtest(candles,train_fraction=args.train_fraction)
    path=Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({k:report[k] for k in (
        "mode","symbols","training_ends","testing_starts","train","test",
        "ending_equity_marked_usdt","max_drawdown_fraction",
        "open_positions_at_end"
    )},indent=2))
    print("Saved:",path)


if __name__=="__main__":
    main()
