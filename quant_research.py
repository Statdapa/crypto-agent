"""Research-only statistical learning for Binance 4h spot candles.

Quant discipline: causal feature extraction, next-bar entry, future label horizon,
purged expanding-window walk-forward, train-only scaling, held-out calibration,
net round-trip costs, non-overlapping research trades, baseline and per-regime
attribution. No online orders, no Telegram execution or model promotion.

Research proxy: fixed coin universe with 4h bar-close predictions held for a
fixed horizon. NOT a replay of the dynamic Binance scanner, current SL/TP,
or historical Fear & Greed. Performance estimates are not forecasts.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np

from backtest_baseline import read_candles

FEATURES = (
    "return_4h", "return_24h", "return_72h",
    "realized_vol_20", "ma_spread_10_30",
    "candle_range", "close_location",
    "rsi_14", "distance_high_20",
)
LOOKBACK = 40
FOLDS = ((0.60, 0.73), (0.73, 0.86), (0.86, 1.00))


def _sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -35.0, 35.0)))


def _fit_logistic(x, y, penalty=4.0, prior=None, max_iter=80):
    """Numerically stable L2 logistic regression; intercept unpenalized."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.ndim != 2 or x.shape[0] != len(y) or len(y) < 20:
        raise ValueError("Insufficient aligned training observations")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("Training features contain NaN or infinite values")
    if set(y.tolist()) - {0.0, 1.0} or min(np.count_nonzero(y), np.count_nonzero(1-y)) < 8:
        raise ValueError("Training needs at least eight examples of each outcome")
    z = np.column_stack((np.ones(len(x)), x))
    theta = np.zeros(z.shape[1]) if prior is None else np.asarray(prior, dtype=float).copy()
    if len(theta) != z.shape[1]:
        raise ValueError("Invalid coefficient prior")
    ridge = np.eye(z.shape[1]) * float(penalty)
    ridge[0, 0] = 1e-5
    for _ in range(max_iter):
        probs = _sigmoid(z @ theta)
        weights = np.maximum(probs * (1 - probs), 1e-7)
        gradient = z.T @ (probs - y) + ridge @ (theta - (np.zeros_like(theta) if prior is None else prior))
        hessian = z.T @ (weights[:, None] * z) + ridge
        step = np.linalg.solve(hessian, gradient)
        theta -= np.clip(step, -3.0, 3.0)
        if np.max(np.abs(step)) < 1e-8:
            break
    if not np.isfinite(theta).all():
        raise ValueError("Logistic coefficients not finite")
    return theta


def _scores(y, p):
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    if not len(y):
        return {"observations": 0, "brier": None, "log_loss": None, "accuracy": None}
    p = np.clip(p, 1e-8, 1 - 1e-8)
    return {
        "observations": int(len(y)),
        "positive_rate": float(np.mean(y)),
        "brier": float(np.mean((p-y)**2)),
        "log_loss": float(np.mean(-y*np.log(p)-(1-y)*np.log1p(-p))),
        "accuracy_at_0_5": float(np.mean((p>=0.5)==y)),
    }


def _fixed_features(bars, i):
    """Only candles 0..i are used. i corresponds to a fully CLOSED candle."""
    close = np.array([float(b.close) for b in bars[i-LOOKBACK:i+1]], dtype=float)
    highs = np.array([float(b.high) for b in bars[i-19:i+1]], dtype=float)
    now = bars[i]
    c = float(now.close)
    if not np.isfinite(close).all() or np.any(close<=0) or c<=0:
        raise ValueError("Invalid historical close values")
    log_returns = np.diff(np.log(close))
    close_prev = close[-2]
    low,high = float(now.low),float(now.high)
    if not (0<low<=c<=high):
        raise ValueError("Historical OHLC identity mismatch")
    gain = np.maximum(log_returns[-14:],0).sum()
    loss = -np.minimum(log_returns[-14:],0).sum()
    rsi = 1.0 if loss == 0 and gain > 0 else (
        0.5 if gain == 0 and loss == 0 else gain/(gain+loss)
    )
    ma_spread = float(np.mean(close[-10:])/np.mean(close[-30:])-1)
    vol = float(np.std(log_returns[-20:]))
    result = (
        float(math.log(c/close_prev)),
        float(math.log(c/close[-7])),
        float(math.log(c/close[-19])),
        vol, ma_spread,
        (high-low)/c,
        (c-low)/(high-low)-0.5 if high>low else 0.0,
        rsi,
        c/float(np.max(highs))-1.0,
    )
    if not np.isfinite(result).all():
        raise ValueError("Nonfinite research feature")
    return np.array(result, dtype=float)


def validate_history(candle_map, horizon):
    if not 1 <= horizon <= 24:
        raise ValueError("Horizon must be from 1 to 24 completed 4h bars")
    if not candle_map:
        raise ValueError("No candle datasets supplied")
    times=None
    for symbol,bars in sorted(candle_map.items()):
        if not symbol.endswith("/USDT") or len(bars)<300:
            raise ValueError(symbol+" needs at least 300 completed 4h spot candles")
        this=[b.timestamp for b in bars]
        if any(b.timestamp.tzinfo is None for b in bars):
            raise ValueError("Missing timezone-aware timestamp")
        if any(b-a != timedelta(hours=4) for a,b in zip(this,this[1:])):
            raise ValueError(symbol+" has 4h timestamp gaps or duplicates")
        if times is None:
            times=this
        elif this!=times:
            raise ValueError("All researched symbols must share identical 4h timestamps and lengths")
    return times


def make_samples(candle_map, horizon=6, fee_bps=10, slippage_bps=5):
    """Labels use next bar open -> horizon bar close (fees both sides).

    Feature at bar i, entry at i+1 OPEN, exit at i+horizon CLOSE.
    Labels are not the running bot's intrabar stop/take outcomes.
    """
    timestamps=validate_history(candle_map,horizon)
    fee=float(fee_bps)/10000
    slip=float(slippage_bps)/10000
    if not (0<=fee<.02 and 0<=slip<.02):
        raise ValueError("Fee/slippage assumptions outside research bounds")
    data=[]
    for symbol,bars in sorted(candle_map.items()):
        for i in range(LOOKBACK,len(bars)-horizon):
            features=_fixed_features(bars,i)
            entry=float(bars[i+1].open)*(1+slip)
            exit_price=float(bars[i+horizon].close)*(1-slip)
            net=exit_price*(1-fee)/(entry*(1+fee))-1
            if not math.isfinite(net) or entry<=0:
                raise ValueError("Invalid cost-adjusted future return")
            data.append({
                "symbol":symbol,"i":i,
                "at":timestamps[i].isoformat(),
                "x":features,
                "future_net_return":float(net),
                "label":int(net>0),
            })
    return data


def _train_and_score(data, fold, horizon, threshold):
    samples=sorted(data,key=lambda s:(s["i"],s["symbol"]))
    indices=sorted({s["i"] for s in samples})
    n=len(indices)
    start=int(n*fold[0]); end=min(n,int(n*fold[1]))
    test_start=indices[start]
    test_end=indices[end-1]
    cal_start=indices[int(start*.80)]
    # Strict purge: a sample's LAST label candle must be earlier than
    # the first observation of the next evaluation partition.
    fit=[s for s in samples if s["i"]+horizon<cal_start]
    cal=[s for s in samples if cal_start<=s["i"] and s["i"]+horizon<test_start]
    test=[s for s in samples if test_start<=s["i"]<=test_end]
    if len(fit)<80 or len(cal)<30 or len(test)<30:
        raise ValueError("Fold lacks sufficient purged train/calibration/test data")
    x_fit=np.array([s["x"] for s in fit])
    y_fit=np.array([s["label"] for s in fit],dtype=float)
    x_cal=np.array([s["x"] for s in cal])
    y_cal=np.array([s["label"] for s in cal],dtype=float)
    x_test=np.array([s["x"] for s in test])
    y_test=np.array([s["label"] for s in test],dtype=float)
    scale_mean=x_fit.mean(axis=0)
    scale_std=x_fit.std(axis=0)
    scale_std=np.where(scale_std<1e-8,1.0,scale_std)
    norm=lambda x: np.clip((x-scale_mean)/scale_std,-12,12)
    fit_scaled=norm(x_fit)
    weights=_fit_logistic(fit_scaled,y_fit,penalty=5)
    cal_raw=np.column_stack((np.ones(len(x_cal)),norm(x_cal)))@weights
    # Calibration is learned from earlier validation data; never test.
    calibrator=_fit_logistic(cal_raw.reshape(-1,1),y_cal,
                             penalty=15,prior=np.array([0.0,1.0]))
    test_raw=np.column_stack((np.ones(len(x_test)),norm(x_test)))@weights
    probabilities=_sigmoid(calibrator[0]+calibrator[1]*test_raw)
    baseline=np.full(len(y_test),y_fit.mean())
    model_scores=_scores(y_test,probabilities)
    baseline_scores=_scores(y_test,baseline)
    thresholds=np.quantile(x_fit[:,3],[.5])
    # The high-vol threshold comes solely from train features.
    train_vol_median=float(thresholds[0])
    predictions=[]
    for s,p in zip(test,probabilities):
        ret=s["future_net_return"]
        direction=( "UP" if s["x"][4]>.01 else
                    "DOWN" if s["x"][4]<-.01 else "SIDEWAYS")
        regime=direction+("_HIGH_VOL" if s["x"][3]>train_vol_median
                          else "_LOW_VOL")
        predictions.append({
            "at":s["at"],"index":s["i"],"symbol":s["symbol"],
            "probability_win":float(p),
            "net_return_fraction":ret,
            "label":s["label"],"regime":regime,
        })
    selected=[]
    last_exit={}
    for prediction in sorted(predictions,key=lambda p:(p["index"],p["symbol"])):
        sym=prediction["symbol"]
        if prediction["probability_win"] < threshold:
            continue
        if prediction["index"] <= last_exit.get(sym,-1):
            continue
        last_exit[sym]=prediction["index"]+horizon
        selected.append(prediction)
    by_symbol=defaultdict(list)
    by_regime=defaultdict(list)
    for p in selected:
        by_symbol[p["symbol"]].append(p["net_return_fraction"])
        by_regime[p["regime"]].append(p["net_return_fraction"])
    summarize=lambda a:{
        "trades":len(a),
        "wins":sum(r>0 for r in a),
        "win_rate":sum(r>0 for r in a)/len(a) if a else None,
        "mean_net_return_fraction":float(np.mean(a)) if a else None,
        "sum_net_returns_per_one_unit_notional":float(np.sum(a)) if a else 0.0,
    }
    returns=[p["net_return_fraction"] for p in selected]
    return {
        "test_at_start":test[0]["at"],"test_at_end":test[-1]["at"],
        "fit_observations":len(fit),"calibration_observations":len(cal),
        "test_observations":len(test),
        "max_fit_label_index":max(s["i"]+horizon for s in fit),
        "first_calibration_feature_index":cal_start,
        "max_calibration_label_index":max(s["i"]+horizon for s in cal),
        "first_test_feature_index":test_start,
        "train_positive_rate":float(y_fit.mean()),
        "model":model_scores,"constant_baseline":baseline_scores,
        "brier_improvement_vs_baseline":baseline_scores["brier"]-model_scores["brier"],
        "selected":summarize(returns),
        "by_coin":{k:summarize(v) for k,v in sorted(by_symbol.items())},
        "by_regime":{k:summarize(v) for k,v in sorted(by_regime.items())},
        "coefficients_standardized":{k:float(v) for k,v in zip(FEATURES,weights[1:])},
        "calibration_intercept":float(calibrator[0]),
        "calibration_slope":float(calibrator[1]),
        "predictions":predictions,
        "accepted_trades":selected,
    }


def train_walk_forward(candle_map,horizon=6,threshold=.65,fee_bps=10,slippage_bps=5):
    if not .5<=threshold<1:
        raise ValueError("Probability threshold must be from 0.50 to <1")
    data=make_samples(candle_map,horizon,fee_bps,slippage_bps)
    folds=[_train_and_score(data,fold,horizon,threshold) for fold in FOLDS]
    all_test=[p for fold in folds for p in fold["predictions"]]
    all_selected=[p for fold in folds for p in fold["accepted_trades"]]
    # Different folds are sequential, but selected trades near boundaries
    # can overlap: discard overlapping accepted positions per symbol globally.
    global_selected=[]
    last_exit={}
    for p in sorted(all_selected,key=lambda q:(q["index"],q["symbol"])):
        if p["index"]<=last_exit.get(p["symbol"],-1):
            continue
        global_selected.append(p)
        last_exit[p["symbol"]]=p["index"]+horizon
    actual=np.array([p["label"] for p in all_test],dtype=float)
    prob=np.array([p["probability_win"] for p in all_test])
    # Baseline is the train-only rate from EACH respective fold.
    base=np.array([b["train_positive_rate"]
                   for b in folds
                   for _ in b["predictions"]])
    chosen=[p["net_return_fraction"] for p in global_selected]
    has_holdout_edge=bool(
        len(global_selected)>=30 and
        all(fold["selected"]["trades"]>=8 and
            fold["selected"]["mean_net_return_fraction"] is not None and
            fold["selected"]["mean_net_return_fraction"]>0 and
            fold["brier_improvement_vs_baseline"]>0 for fold in folds)
        and np.mean(chosen)>0
    )
    return {
        "mode":"RESEARCH_ONLY_NEVER_AUTO_TRADE",
        "symbols":sorted(candle_map),
        "features":FEATURES,
        "horizon_4h_bars":horizon,
        "probability_entry_threshold":threshold,
        "fee_bps_each_side":fee_bps,
        "slippage_bps_each_side":slippage_bps,
        "sample_count":len(data),
        "folds":folds,
        "out_of_sample": {
            "model":_scores(actual,prob),
            "constant_baseline":_scores(actual,base),
            "brier_improvement_vs_baseline":float(np.mean((base-actual)**2)-np.mean((prob-actual)**2)),
            "non_overlapping_selected_trades":len(global_selected),
            "selected_win_rate":sum(r>0 for r in chosen)/len(chosen) if chosen else None,
            "selected_mean_net_return_fraction":float(np.mean(chosen)) if chosen else None,
        },
        "research_gate_passed_not_live_ready":has_holdout_edge,
        "promotion_to_paper_execution":False,
        "caveats":[
            "Training and probabilities cover a fixed historical universe; delisted markets excluded.",
            "Historical Binance-wide scanner, full spread/order book, funding and Fear & Greed are absent.",
            "Fixed-horizon net returns are NOT the running paper engine's 3% SL/5% TP trades.",
            "Some adjacent overlapping labels remain in test metrics; sample count overstates effective independence.",
            "Selected returns are fractions per 1 USDT notional, NOT compounded portfolio PnL.",
            "Threshold is prespecified, not selected by observing held-out test results.",
            "Passing the research gate is not sufficient to turn on trading; paper forward validation is mandatory.",
        ],
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description="Causal ML research walk-forward (no trades)")
    parser.add_argument("--csv",required=True,nargs="+",
                        help="SYMBOL=PATH, e.g. BTC/USDT=data/raw/BTC_USDT_4h.csv")
    parser.add_argument("--horizon-bars",type=int,default=6)
    parser.add_argument("--threshold",type=float,default=.65)
    parser.add_argument("--fee-bps",type=float,default=10)
    parser.add_argument("--slippage-bps",type=float,default=5)
    parser.add_argument("--output",default="data/reports/quant_research.json")
    args=parser.parse_args(argv)
    candles={}
    for pair in args.csv:
        if "=" not in pair:
            parser.error("Use SYMBOL=PATH")
        symbol,path=pair.split("=",1)
        symbol=symbol.upper()
        if symbol in candles:
            parser.error("Duplicate symbol")
        if not Path(path).exists():
            parser.error("Missing candle file "+path+
                         ". Run: python download_backtest_data.py "+
                         symbol.split("/")[0]+" --limit 500")
        candles[symbol]=read_candles(path)
    report=train_walk_forward(candles,args.horizon_bars,args.threshold,
                              args.fee_bps,args.slippage_bps)
    destination=Path(args.output)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({
        "mode":report["mode"],
        "symbols":report["symbols"],
        "sample_count":report["sample_count"],
        "folds":[{
            "test_start":f["test_at_start"],"test_end":f["test_at_end"],
            "test_observations":f["test_observations"],
            "brier_improvement_vs_baseline":f["brier_improvement_vs_baseline"],
            "selected":f["selected"],
        } for f in report["folds"]],
        "out_of_sample":report["out_of_sample"],
        "research_gate_passed_not_live_ready":report["research_gate_passed_not_live_ready"],
        "promotion_to_paper_execution":False,
        "saved":str(destination),
    },indent=2))


if __name__=="__main__":
    main()
