"""Ablation diagnostics on early historical data; last 20% remains untouched.

Stage 12. NO model promotion or live/paper execution. Candidate comparisons
only on development folds; final holdout has no labels/predictions emitted.
Ablations compare groups of existing 9 causal features, not future features.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from backtest_baseline import read_candles
from quant_research import (
    FEATURES, LOOKBACK, make_samples, validate_history, _fit_logistic, _sigmoid, _scores,
)

GROUPS={
    "momentum":("return_4h","return_24h","return_72h"),
    "volatility":("realized_vol_20","candle_range"),
    "trend":("ma_spread_10_30","distance_high_20"),
    "oscillators":("close_location","rsi_14"),
}
# Disjoint chronological development validation windows, final 20% never scored.
DEV_FOLDS=((.47,.59),(.59,.70),(.70,.80))
HOLDOUT_START=.80


def _auc(y,prob):
    """Pooled rank AUC, diagnostic only; overlapping outcomes not independent."""
    positives=np.asarray(prob)[np.asarray(y)==1]
    negatives=np.asarray(prob)[np.asarray(y)==0]
    if not len(positives) or not len(negatives):
        return None
    ranked=(positives[:,None]>negatives[None,:]).sum()
    ties=(positives[:,None]==negatives[None,:]).sum()
    return float((ranked+.5*ties)/(len(positives)*len(negatives)))


def _evaluate(dev, chosen, horizon, folds=DEV_FOLDS, return_predictions=False):
    """Strictly chronological fitting, calibration and development scoring."""
    idx=sorted({s["i"] for s in dev})
    n=len(idx)
    names=[FEATURES.index(k) for k in chosen]
    reports=[]
    for first,last in folds:
        left=idx[int(n*first)]
        right=idx[min(int(n*last),n)-1]
        cal_first=idx[int(n*first*.75)]
        train=[s for s in dev if s["i"]+horizon<cal_first]
        cal=[s for s in dev if cal_first<=s["i"] and s["i"]+horizon<left]
        test=[s for s in dev if left<=s["i"]<=right]
        if min(len(train),len(cal),len(test))<30:
            raise ValueError("Insufficient development samples for purged fold")
        y_train=np.array([s["label"] for s in train])
        y_cal=np.array([s["label"] for s in cal])
        y_test=np.array([s["label"] for s in test])
        x_train=np.array([s["x"][names] for s in train])
        x_cal=np.array([s["x"][names] for s in cal])
        x_test=np.array([s["x"][names] for s in test])
        scale=x_train.std(axis=0)
        scale=np.where(scale<1e-8,1.0,scale)
        center=x_train.mean(axis=0)
        transform=lambda x:np.clip((x-center)/scale,-12,12)
        weights=_fit_logistic(transform(x_train),y_train,penalty=5)
        cal_logits=np.column_stack((np.ones(len(cal)),transform(x_cal)))@weights
        # Calibration strictly earlier than the validation observations.
        cal_weights=_fit_logistic(cal_logits.reshape(-1,1),y_cal,
                                  penalty=15,prior=np.array([0.,1.]))
        logits=np.column_stack((np.ones(len(test)),transform(x_test)))@weights
        pred=_sigmoid(cal_weights[0]+cal_weights[1]*logits)
        base=np.full(len(y_test),float(y_train.mean()))
        m=_scores(y_test,pred)
        b=_scores(y_test,base)
        result={
            "development_start_at":test[0]["at"],
            "development_end_at":test[-1]["at"],
            "train_n":len(train),"calibration_n":len(cal),"validation_n":len(test),
            "train_label_last_index":max(s["i"]+horizon for s in train),
            "calibration_start_index":cal_first,
            "calibration_label_last_index":max(s["i"]+horizon for s in cal),
            "validation_start_index":left,
            "model_brier":m["brier"],
            "baseline_brier":b["brier"],
            "brier_delta_baseline_minus_model":b["brier"]-m["brier"],
            "model_auc":_auc(y_test,pred),
            "positive_rate":float(y_test.mean()),
        }
        if return_predictions:
            # Regime labels are determined from *features at signal time*.
            # The volatility split is determined from the earlier fit set.
            median_vol=float(np.median([
                s["x"][FEATURES.index("realized_vol_20")] for s in train
            ]))
            predictions=[]
            for s,p,bp in zip(test,pred,base):
                spread=float(s["x"][FEATURES.index("ma_spread_10_30")])
                direction=("UP" if spread>.005 else
                           "DOWN" if spread<-.005 else "SIDEWAYS")
                high_vol=bool(s["x"][FEATURES.index("realized_vol_20")]>median_vol)
                predictions.append({
                    "at":s["at"],"i":s["i"],"symbol":s["symbol"],
                    "label":s["label"],"probability_win":float(p),
                    "baseline_probability":float(bp),
                    "net_return_fraction":float(s["future_net_return"]),
                    "regime":direction+("_HIGH_VOL" if high_vol else "_LOW_VOL"),
                    "trend":direction,"high_vol":high_vol,
                })
            result["predictions"]=predictions
        reports.append(result)
    return reports


def development_samples(candle_map,horizon=6,fee_bps=10,slippage_bps=5):
    """Produce *only* pre-holdout training/validation labels.

    No holdout label is computed at all, including in temporary arrays.
    """
    times=validate_history(candle_map,horizon)
    eligible=len(times)-horizon-LOOKBACK
    boundary=LOOKBACK+int(eligible*HOLDOUT_START)
    if boundary<300 or boundary-horizon-LOOKBACK<200:
        raise ValueError("Need more data for diagnostics. Download >=1000 bars per coin.")
    # make_samples accesses only data strictly before holdout boundary.
    earlier={symbol:bars[:boundary] for symbol,bars in candle_map.items()}
    dev=make_samples(earlier,horizon,fee_bps,slippage_bps)
    if max(s["i"]+horizon for s in dev)>=boundary:
        raise AssertionError("Holdout purge failed")
    return dev, {
        "holdout_first_feature_index":boundary,
        "held_out_first_timestamp":times[boundary].isoformat(),
        "held_out_observations_reserved":(eligible-int(eligible*HOLDOUT_START))*len(candle_map),
    }


def ablation_diagnostics(candle_map,horizon=6,fee_bps=10,slippage_bps=5):
    dev,holdout=development_samples(candle_map,horizon,fee_bps,slippage_bps)
    configurations={"all_features":list(FEATURES)}
    for group,features in GROUPS.items():
        configurations["without_"+group]=[f for f in FEATURES if f not in features]
    configurations["only_momentum"]=list(GROUPS["momentum"])
    results={}
    for name,features in configurations.items():
        results[name]=_evaluate(dev,features,horizon)
    comparison={}
    for name,folds in results.items():
        deltas=[f["brier_delta_baseline_minus_model"] for f in folds]
        comparison[name]={
            "mean_development_brier_improvement":float(np.mean(deltas)),
            "min_fold_brier_improvement":float(np.min(deltas)),
            "positive_fold_count":sum(x>0 for x in deltas),
            "fold_count":len(deltas),
            "features":configurations[name],
        }
    return {
        "mode":"ABLATION_DIAGNOSTICS_RESEARCH_ONLY",
        "held_out_test_scored":False,
        "held_out_observations_reserved":holdout["held_out_observations_reserved"],
        "held_out_first_timestamp":holdout["held_out_first_timestamp"],
        "development_label_last_index":max(s["i"]+horizon for s in dev),
        "holdout_first_feature_index":holdout["holdout_first_feature_index"],
        "development_observations":len(dev),
        "horizon_bars":horizon,
        "candidate_comparison":comparison,
        "development_folds":results,
        "promotion_to_paper_execution":False,
        "warnings":[
            "These are development comparisons. The last 20% has NO revealed predictions or labels in this report.",
            "Do not select variants using the final holdout, rerun repeatedly, or claim statistical significance from overlapping labels.",
            "Ablation is diagnostic; a higher score does not imply robust real trading edge.",
            "Current fixed BTC/SOL universe omits delisted symbols and historical market selection.",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="Stage 12 quant feature ablation; no trade execution")
    p.add_argument("--csv",nargs="+",required=True,help="SYMBOL=PATH")
    p.add_argument("--horizon-bars",type=int,default=6)
    p.add_argument("--output",default="data/reports/feature_ablation.json")
    args=p.parse_args(argv)
    candles={}
    for assignment in args.csv:
        if "=" not in assignment:
            p.error("Use SYMBOL=PATH")
        symbol,path=assignment.split("=",1)
        if not Path(path).is_file():
            p.error("Missing "+path+"; use download_historical_data.py")
        sym=symbol.upper()
        if sym in candles:
            p.error("Duplicate coin "+sym)
        candles[sym]=read_candles(path)
    report=ablation_diagnostics(candles,args.horizon_bars)
    out=Path(args.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({
        "mode":report["mode"],
        "held_out_test_scored":False,
        "held_out_observations_reserved":report["held_out_observations_reserved"],
        "holdout_first_timestamp":report["held_out_first_timestamp"],
        "candidate_comparison":report["candidate_comparison"],
        "saved":str(out),
    },indent=2))


if __name__=="__main__":
    main()
