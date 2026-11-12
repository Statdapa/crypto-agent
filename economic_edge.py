"""Stage 14 — Economic Edge Validation: historical research only.

No order execution, Telegram notifications, writes to the live paper SQLite
ledger, or ML promotion. Uses only Stage 13's prespecified 'all_features'
development predictions; final 20% holdout OHLC is never accessed.

Two DISTINCT experiments:
1. Leave-best-out concentration of Stage 13's six-or-fewer fixed-6-bar
   hypothetical buy/hold signals, each measured per unit notional.
2. Causal long-only OHLC shared-cash portfolio *proxy*, not the live scanner
   or its actual paper quote-driven execution. Uses next-bar open, 3% stop,
   5% take, stop-first OHLC, gap-open exits, fixed horizon exit, 10 bps fee
   and 5 bps slippage each side, no leverage. Original ML fit/calibration
   and threshold remain unchanged. No historical spread/order-book available.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from backtest_baseline import read_candles
from multi_portfolio import PortfolioLimits
from quant_research import validate_history
from quant_robustness import build_development_results, _nonoverlapping_entries
from trading_engine import D, floor_quantity


@dataclass
class HeldPosition:
    symbol: str
    signal_index: int
    opened_at: str
    regime: str
    probability: float
    qty: Decimal
    entry: Decimal
    entry_fee: Decimal
    stop: Decimal
    take: Decimal


def _summarize_numbers(values):
    n=len(values)
    winners=[x for x in values if x>0]
    losers=[x for x in values if x<0]
    return {
        "count":n,
        "wins":len(winners),
        "win_rate":float(len(winners)/n) if n else None,
        "mean_return_fraction":float(np.mean(values)) if n else None,
        "median_return_fraction":float(np.median(values)) if n else None,
        "sum_unit_notional_returns":float(sum(values)),
        "worst_return_fraction":float(min(values)) if n else None,
        "best_return_fraction":float(max(values)) if n else None,
    }


def concentration_audit(selected):
    """Fixed-horizon unit-notional labels. No portfolio compounding."""
    returns=[float(p["net_return_fraction"]) for p in selected]
    if any(not np.isfinite(r) or r<=-1 for r in returns):
        raise ValueError("Invalid selected-trade return")
    ordered=sorted(returns,reverse=True)
    positive=sum(x for x in returns if x>0)
    net=sum(returns)
    safe_share=lambda x,y:float(x/y) if abs(y)>1e-14 else None
    by_coin=defaultdict(list)
    by_regime=defaultdict(list)
    by_fold=defaultdict(list)
    for p in selected:
        by_coin[p["symbol"]].append(float(p["net_return_fraction"]))
        by_regime[p["regime"]].append(float(p["net_return_fraction"]))
        by_fold[str(p.get("development_fold","unknown"))].append(
            float(p["net_return_fraction"])
        )
    leave_one_out=[]
    for j,t in enumerate(selected):
        others=returns[:j]+returns[j+1:]
        leave_one_out.append({
            "removed_at":t["at"],"removed_symbol":t["symbol"],
            "removed_return_fraction":returns[j],
            "remaining_mean_return_fraction":float(np.mean(others)) if others else None,
        })
    return {
        "comparison":"fixed_horizon_research_labels_not_portfolio_pnl",
        "fixed_horizon_selected":len(returns),
        "overall":_summarize_numbers(returns),
        "mean_without_best_one":float(np.mean(ordered[1:])) if len(ordered)>1 else None,
        "mean_without_best_two":float(np.mean(ordered[2:])) if len(ordered)>2 else None,
        "mean_without_worst_one":float(np.mean(ordered[:-1])) if len(ordered)>1 else None,
        "best_two_share_of_positive_return_sum":(
            safe_share(sum(x for x in ordered[:2] if x>0),positive)
            if positive>0 else None
        ),
        "best_two_share_of_net_unit_return_sum":(
            safe_share(sum(ordered[:2]),net) if net>0 else None
        ),
        "leave_one_out":leave_one_out,
        "by_coin":{k:_summarize_numbers(v) for k,v in sorted(by_coin.items())},
        "by_regime":{k:_summarize_numbers(v) for k,v in sorted(by_regime.items())},
        "by_development_fold":{
            k:_summarize_numbers(v) for k,v in sorted(by_fold.items())
        },
        "all_selected":[
            {"at":p["at"],"symbol":p["symbol"],"regime":p["regime"],
             "probability_win":p["probability_win"],
             "net_return_fraction":p["net_return_fraction"],
             "development_fold":p.get("development_fold")}
            for p in selected
        ],
        "caveats":[
            "Six or fewer trades is far too small for reliable inference.",
            "Top-trade removal is an ex-post sensitivity, NOT a live trading filter.",
            "Mean of trade return fractions is NOT compound portfolio return.",
            "A high return in DOWN_HIGH_VOL is a hypothesis, not a validated regime rule.",
        ],
    }


def _liquidation(position,price,cash,fee,slip,when,reason):
    exit_fill=price*(D(1)-slip)
    exit_fee=position.qty*exit_fill*fee
    pnl=((exit_fill-position.entry)*position.qty
         -position.entry_fee-exit_fee)
    cash+=exit_fill*position.qty-exit_fee
    return cash,{
        "symbol":position.symbol,
        "signal_index":position.signal_index,
        "opened_at":position.opened_at,
        "closed_at":when.isoformat(),
        "regime_at_signal":position.regime,
        "probability_at_signal":position.probability,
        "entry_fill":str(position.entry),
        "exit_fill":str(exit_fill),
        "entry_fee_usdt":str(position.entry_fee),
        "exit_fee_usdt":str(exit_fee),
        "qty":str(position.qty),
        "net_pnl_usdt":str(pnl),
        "return_on_entry_notional_fraction":str(
            pnl/(position.entry*position.qty)
        ),
        "exit_reason":reason,
    }


def _exit_reference(position,bar):
    """Price thresholds only; gap behavior first, stop-first if both touched."""
    if bar.open<=position.stop:
        return bar.open,"stop_gap"
    if bar.open>=position.take:
        return bar.open,"take_gap"
    if bar.low<=position.stop:
        return position.stop,"stop_first"
    if bar.high>=position.take:
        return position.take,"take_profit"
    return None,None


def simulate_shared_portfolio(candle_map,predictions,*,boundary,horizon=6,
                              threshold=.65,limits=None):
    """Conservative historical OHLC proxy with one shared, long-only cash book.

    Only predictions from finished development folds can trigger BUY at next
    4h open; for simultaneous signals higher probability is selected first.
    End of simulation is just after last developmental signal's horizon;
    NEVER reads any candle at or after 'boundary' (reserved holdout).
    """
    limits=limits or PortfolioLimits()
    timestamps=validate_history(candle_map,horizon)
    if boundary>len(timestamps) or not 100<boundary<len(timestamps):
        raise ValueError("Invalid reserved holdout boundary")
    if not .5<=threshold<1 or not 1<=horizon<=24:
        raise ValueError("Invalid prespecified threshold/horizon")
    if not predictions:
        raise ValueError("No development predictions to evaluate")
    symbols=sorted(candle_map)
    signals=defaultdict(list)
    for row in predictions:
        symbol=row["symbol"]
        idx=int(row["i"])
        if symbol not in candle_map or idx<0 or idx+horizon>=boundary:
            raise ValueError("Signal reaches reserved holdout/unknown symbol")
        if timestamps[idx].isoformat()!=row["at"]:
            raise ValueError("Signal timestamp mismatches historical candle index")
        if not np.isfinite(row["probability_win"]):
            raise ValueError("Nonfinite signal")
        if row["probability_win"]>=threshold:
            signals[idx+1].append(row)
    max_signal=max(int(p["i"]) for p in predictions)
    first_bar=min(int(p["i"]) for p in predictions)+1
    final_bar=max_signal+horizon
    if final_bar>=boundary:
        raise ValueError("Simulation would read reserved holdout")

    cash=D(limits.initial_cash)
    initial=cash
    fee=limits.fee_rate
    slip=limits.slippage_rate
    positions={}
    completed=[]
    curve=[]
    peak=initial
    max_dd=D(0)
    halted=False
    stop_reason=None
    day=None
    day_start_eq=initial
    daily_entries=0
    last_close={}
    rejected={"insufficient_cash_or_risk_budget":0,"max_positions_or_total_exposure":0,
              "halted":0,"daily_entry_limit":0,"cooldown_or_existing_position":0}
    entries=0
    for idx in range(first_bar,final_bar+1):
        t=timestamps[idx]
        if t.date()!=day:
            day=t.date()
            previous=cash+sum(
                (p.qty*candle_map[s][idx-1].close
                 for s,p in positions.items()),D(0)
            )
            day_start_eq=previous
            daily_entries=0
        # Exit prior positions at the open for gap crossings (or a halt).
        for symbol in sorted(tuple(positions)):
            position=positions[symbol]
            bar=candle_map[symbol][idx]
            opening_reason=("halt_open" if halted else
                            "stop_gap" if bar.open<=position.stop else
                            "take_gap" if bar.open>=position.take else None)
            if opening_reason:
                cash,trade=_liquidation(position,bar.open,cash,fee,slip,t,opening_reason)
                completed.append(trade)
                last_close[symbol]=t
                del positions[symbol]

        # Mark all still-open positions at the current open for sizing/caps.
        opening_equity=cash+sum(
            (p.qty*candle_map[s][idx].open for s,p in positions.items()),D(0)
        )
        exposure=sum(
            (p.qty*candle_map[s][idx].open for s,p in positions.items()),D(0)
        )
        # Kill switches are measured at available bar marks; not intrabar.
        if not halted and (
            (peak-opening_equity)/peak>=limits.max_drawdown_fraction or
            (day_start_eq-opening_equity)/day_start_eq>=limits.daily_loss_fraction
        ):
            halted=True
            stop_reason="loss_limit_open"
        ranked=sorted(signals.get(idx,[]),
                      key=lambda p:(-p["probability_win"],p["symbol"]))
        for signal in ranked:
            symbol=signal["symbol"]
            if halted:
                rejected["halted"]+=1
                continue
            if symbol in positions or (
                symbol in last_close and
                t-last_close[symbol]<timedelta(minutes=limits.cooldown_minutes)
            ):
                rejected["cooldown_or_existing_position"]+=1
                continue
            if daily_entries>=limits.max_entries_daily:
                rejected["daily_entry_limit"]+=1
                continue
            if len(positions)>=limits.max_positions:
                rejected["max_positions_or_total_exposure"]+=1
                continue
            bar=candle_map[symbol][idx]
            allowed_total=opening_equity*limits.max_total_fraction-exposure
            budget=min(
                opening_equity*limits.max_position_fraction,
                opening_equity*limits.risk_per_trade/limits.stop_fraction,
                allowed_total,
                cash/(D(1)+fee),
            )
            fill=bar.open*(D(1)+slip)
            qty=floor_quantity(budget/fill) if budget>0 else D(0)
            notional=fill*qty
            entry_fee=notional*fee
            if qty<=0 or notional<limits.min_notional or notional+entry_fee>cash:
                rejected["insufficient_cash_or_risk_budget"]+=1
                continue
            cash-=notional+entry_fee
            positions[symbol]=HeldPosition(
                symbol=symbol,signal_index=idx-1,opened_at=t.isoformat(),
                regime=signal["regime"],probability=float(signal["probability_win"]),
                qty=qty,entry=fill,entry_fee=entry_fee,
                stop=fill*(D(1)-limits.stop_fraction),
                take=fill*(D(1)+limits.take_fraction),
            )
            exposure+=notional
            daily_entries+=1
            entries+=1
        # All positions now see this bar's complete range; STOP first if both.
        for symbol in sorted(tuple(positions)):
            pos=positions[symbol]
            bar=candle_map[symbol][idx]
            reference,why=_exit_reference(pos,bar)
            if reference is None and idx>=pos.signal_index+horizon:
                reference,why=bar.close,"horizon_close"
            if reference is not None:
                cash,trade=_liquidation(pos,reference,cash,fee,slip,t,why)
                completed.append(trade)
                last_close[symbol]=t
                del positions[symbol]
        close_equity=cash+sum(
            (p.qty*candle_map[s][idx].close for s,p in positions.items()),D(0)
        )
        peak=max(peak,close_equity)
        max_dd=max(max_dd,(peak-close_equity)/peak)
        if not halted and (
            (peak-close_equity)/peak>=limits.max_drawdown_fraction or
            (day_start_eq-close_equity)/day_start_eq>=limits.daily_loss_fraction
        ):
            halted=True
            stop_reason="loss_limit_close"
        curve.append({"at":t.isoformat(),"equity_usdt":str(close_equity)})
    if positions:
        raise AssertionError("Development positions must expire before holdout")
    results=[D(p["net_pnl_usdt"]) for p in completed]
    wins=sum(1 for x in results if x>0)
    gross_win=sum((x for x in results if x>0),D(0))
    gross_loss=-sum((x for x in results if x<0),D(0))
    portfolio_return=(cash/initial)-D(1)
    return {
        "mode":"PRESPECIFIED_ML_OHLC_PORTFOLIO_PROXY_NO_ORDERS",
        "start_at":timestamps[first_bar].isoformat(),
        "end_at":timestamps[final_bar].isoformat(),
        "initial_cash_usdt":str(initial),
        "ending_cash_usdt":str(cash),
        "net_return_fraction":float(portfolio_return),
        "max_close_to_close_drawdown_fraction":float(max_dd),
        "entries":entries,"closed_trades":len(completed),
        "wins":wins,"win_rate":wins/len(completed) if completed else None,
        "mean_net_pnl_usdt_per_closed_trade":(
            float(sum(results,D(0))/len(results)) if results else None
        ),
        "total_realized_net_pnl_usdt":str(sum(results,D(0))),
        "profit_factor":float(gross_win/gross_loss) if gross_loss>0 else None,
        "halted":halted,"halt_reason":stop_reason,
        "rejection_counts":rejected,
        "risk_limits":{
            "max_positions":limits.max_positions,
            "max_position_fraction":str(limits.max_position_fraction),
            "max_total_fraction":str(limits.max_total_fraction),
            "risk_per_trade":str(limits.risk_per_trade),
            "stop_fraction":str(limits.stop_fraction),
            "take_fraction":str(limits.take_fraction),
            "fee_bps_per_side":str(limits.fee_bps),
            "slippage_bps_per_side":str(limits.slippage_bps),
            "max_drawdown_fraction":str(limits.max_drawdown_fraction),
            "daily_loss_fraction":str(limits.daily_loss_fraction),
        },
        "trades":completed,
        "equity_curve":curve,
        "warnings":[
            "PROXY not the live paper engine: historical OHLC lacks quote timing, latency, order book and intra-bar path.",
            "Portfolio uses the prespecified all-features, 65% threshold; no optimization.",
            "Signal evaluated after bar i closes; entry is at i+1 open, never bar i open.",
            "Gap-open exits before new entries; intrabar STOP-first if stop and take both touched.",
            "Risk kill-switch uses open/close equity marks; unobserved intrabar losses may exceed limits.",
            "Fixed original six-bar horizon is a research cap; stops/takes can exit sooner.",
            "This path-dependent portfolio may have a DIFFERENT trade count from the fixed-horizon six-trade analysis.",
            "Fixed BTC/SOL universe, survivorship bias; no historical dynamic scanner replay.",
            "Valuation is unrealized mark-to-market without exit fees; all positions end flat.",
        ],
    }


def _decision_gate(concentration,portfolio):
    """Evidence-only gate, deliberately not an automatic release flag."""
    n=concentration["fixed_horizon_selected"]
    top2=concentration["best_two_share_of_positive_return_sum"]
    fragile=(top2 is not None and top2>.75)
    if n<30:
        reasons=["Fewer than 30 fixed-horizon selected trades"]
    else:
        reasons=[]
    if fragile:
        reasons.append("Top two wins contribute over 75% of positive returns")
    if portfolio["closed_trades"]<30:
        reasons.append("Fewer than 30 portfolio-proxy completed trades")
    if portfolio["net_return_fraction"]<=0:
        reasons.append("Portfolio-proxy net return is nonpositive")
    # Regardless of development performance, passing a gate would require
    # untouched final OOS and prospective forward evaluation, neither done.
    return {
        "stage14_research_go":not bool(reasons),
        "stage14_research_no_go_reasons":reasons,
        "holdout_evaluated":False,
        "eligible_for_paper_automation":False,
        "eligible_for_live_trading":False,
        "note":"This is an exploratory diagnostic gate only, not a trading approval.",
    }


def economic_edge_report(candle_map,*,horizon=6,threshold=.65,
                         fee_bps=10,slippage_bps=5):
    if abs(float(threshold)-.65)>1e-12:
        raise ValueError("Stage 14 threshold fixed at 0.65; no test-set tuning")
    if horizon!=6:
        raise ValueError("Stage 14 horizon fixed at 6 4h bars")
    if float(fee_bps)!=10 or float(slippage_bps)!=5:
        raise ValueError("Stage 14 costs fixed to existing risk profile")
    dev,holdout,detail,_=build_development_results(
        candle_map,horizon,fee_bps,slippage_bps)
    predictions=[]
    for fold_index,fold in enumerate(detail["all_features"],1):
        for pred in fold["predictions"]:
            predictions.append({**pred,"development_fold":fold_index})
    selected=_nonoverlapping_entries(predictions,threshold,horizon)
    concentration=concentration_audit(selected)
    portfolio=simulate_shared_portfolio(
        candle_map,predictions,
        boundary=holdout["holdout_first_feature_index"],
        horizon=horizon,threshold=threshold,limits=PortfolioLimits(),
    )
    if max(s["i"]+horizon for s in dev)>=holdout["holdout_first_feature_index"]:
        raise AssertionError("Reserved holdout breach")
    return {
        "mode":"STAGE14_ECONOMIC_EDGE_RESEARCH_ONLY",
        "symbols":sorted(candle_map),
        "holdout_evaluated":False,
        "holdout_labels_computed":False,
        "holdout_first_timestamp":holdout["held_out_first_timestamp"],
        "holdout_reserved_observations":holdout["held_out_observations_reserved"],
        "development_label_last_index":max(s["i"]+horizon for s in dev),
        "concentration":concentration,
        "portfolio_proxy":portfolio,
        "decision_gate":_decision_gate(concentration,portfolio),
        "promotion_to_paper_execution":False,
        "live_trading_enabled":False,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description="Stage 14 economic edge validation (read-only)")
    parser.add_argument("--csv",nargs="+",required=True,help="SYMBOL=CSV")
    parser.add_argument("--output",default="data/reports/economic_edge.json")
    args=parser.parse_args(argv)
    candles={}
    for pair in args.csv:
        if "=" not in pair:
            parser.error("Use SYMBOL=PATH")
        symbol,path=pair.split("=",1)
        symbol=symbol.strip().upper()
        if symbol in candles:
            parser.error("Duplicate coin "+symbol)
        if not Path(path).is_file():
            parser.error("Missing historical CSV "+path)
        candles[symbol]=read_candles(path)
    report=economic_edge_report(candles)
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    # Concise terminal output; full trade audit and equity curve in JSON.
    print(json.dumps({
        "mode":report["mode"],
        "holdout_evaluated":False,
        "fixed_horizon_concentration":{
            k:v for k,v in report["concentration"].items()
            if k in ("fixed_horizon_selected","overall","mean_without_best_one",
                     "mean_without_best_two","best_two_share_of_positive_return_sum")
        },
        "portfolio_proxy":{
            k:v for k,v in report["portfolio_proxy"].items()
            if k in ("entries","closed_trades","wins","win_rate",
                     "initial_cash_usdt","ending_cash_usdt",
                     "net_return_fraction","max_close_to_close_drawdown_fraction",
                     "halted","total_realized_net_pnl_usdt","profit_factor")
        },
        "decision_gate":report["decision_gate"],
        "promotion_to_paper_execution":False,
        "saved":str(output),
    },indent=2))


if __name__=="__main__":
    main()
