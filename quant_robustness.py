"""Stage 13 — leakage-conscious development robustness diagnostics.

This is RESEARCH ONLY, never a paper/live trade signal. Cross-symbol pairs
at identical timestamps are resampled together. Consecutive 4h timestamps
within each validation fold are resampled in moving circular blocks. The
familywise max-T test is evaluated across all SIX predeclared ablations;
scores and labels of the final 20% holdout are NEVER materialized.

Moving-block bootstrap is approximate: folds are different market periods,
the finite sample, overlapping labels and nonstationarity limit inference.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from backtest_baseline import read_candles
from feature_ablation import (
    FEATURES, GROUPS, _evaluate, development_samples,
)

VARIANTS = {"all_features":list(FEATURES)}
for _group,_names in GROUPS.items():
    VARIANTS["without_"+_group]=[f for f in FEATURES if f not in _names]
VARIANTS["only_momentum"]=list(GROUPS["momentum"])


def build_development_results(candle_map,horizon=6,fee_bps=10,slippage_bps=5):
    dev,holdout=development_samples(candle_map,horizon,fee_bps,slippage_bps)
    details={name:_evaluate(dev,features,horizon,return_predictions=True)
             for name,features in VARIANTS.items()}
    # Alignment is strict: all variants must be tested against EXACT same
    # outcomes and timestamps. Fold-specific baseline is train-only.
    common=[]
    for fi in range(len(next(iter(details.values())))):
        standard=details["all_features"][fi]["predictions"]
        standard_keys=[(p["i"],p["symbol"],p["label"]) for p in standard]
        for name,folds in details.items():
            items=folds[fi]["predictions"]
            if [(p["i"],p["symbol"],p["label"]) for p in items]!=standard_keys:
                raise ValueError("Ablation predictions are not paired")
        common.append(standard_keys)
    return dev,holdout,details,common


def _per_time_differences(details):
    names=list(details)
    blocks=[]
    # Fold-specific date boundaries must not be bridged by bootstrap blocks.
    for fold_idx in range(len(details[names[0]])):
        reference=details[names[0]][fold_idx]["predictions"]
        by_i=defaultdict(list)
        for j,p in enumerate(reference):
            by_i[p["i"]].append(j)
        timestamps=sorted(by_i)
        if any(b!=a+1 for a,b in zip(timestamps,timestamps[1:])):
            raise ValueError("Development fold has missing 4h timestamps")
        arr=np.empty((len(timestamps),len(names)),dtype=float)
        for col,name in enumerate(names):
            predictions=details[name][fold_idx]["predictions"]
            for row,i in enumerate(timestamps):
                values=[]
                for k in by_i[i]:
                    item=predictions[k]
                    y=item["label"]
                    b=item["baseline_probability"]
                    p=item["probability_win"]
                    values.append((b-y)**2-(p-y)**2)
                arr[row,col]=float(np.mean(values))
        blocks.append(arr)
    return names,blocks


def _block_indices(rng,n,block):
    if not isinstance(block,int) or block<1:
        raise ValueError("Block length must be positive")
    starts=rng.integers(0,n,size=(n+block-1)//block)
    idx=(starts[:,None]+np.arange(block)[None,:])%n
    return idx.ravel()[:n]


def paired_block_bootstrap(names,fold_arrays,iterations=600,block=18,seed=2026):
    """Single-step max-T correction for the 6 predeclared model comparisons.

    Bootstrap is centered at each candidate's observed mean to estimate
    the one-sided global null distribution. Time indices in a fold are
    shared across symbols and every model. No independent coin shuffling.
    """
    if not 100<=iterations<=10000:
        raise ValueError("Require 100-10000 block bootstrap iterations")
    if not isinstance(seed,int):
        raise ValueError("Seed must be an integer")
    if any(a.ndim!=2 or a.shape[1]!=len(names) or not np.isfinite(a).all()
           for a in fold_arrays):
        raise ValueError("Invalid paired development deltas")
    if min(a.shape[0] for a in fold_arrays)<block*2:
        raise ValueError("Bootstrap blocks too long for folds")
    rng=np.random.default_rng(seed)
    count=sum(a.shape[0] for a in fold_arrays)
    observed=sum((a.sum(axis=0) for a in fold_arrays),
                 np.zeros(len(names)))/count
    sampled=np.empty((iterations,len(names)),dtype=float)
    for rep in range(iterations):
        total=np.zeros(len(names))
        for arr in fold_arrays:
            idx=_block_indices(rng,len(arr),block)
            total+=arr[idx].sum(axis=0)
        sampled[rep]=total/count
    centered=sampled-observed
    max_null=np.max(centered,axis=1)
    # Simultaneous lower 95% bounds and multiplicity-adjusted p-values
    # use the common maximum null across ALL six feature variants.
    q95=float(np.quantile(max_null,.95))
    per={}
    for j,name in enumerate(names):
        ci=np.quantile(sampled[:,j],[.025,.975])
        per[name]={
            "mean_brier_improvement":float(observed[j]),
            "moving_block_ci_95":[float(ci[0]),float(ci[1])],
            "simultaneous_one_sided_lower_95":float(observed[j]-q95),
            "one_sided_maxT_p_adjusted":float(
                (1+np.count_nonzero(max_null>=observed[j]))/(iterations+1)
            ),
            "significant_positive_at_familywise_5pct":bool(observed[j]-q95>0),
        }
    return {
        "method":"paired_circular_moving_block_bootstrap_maxT",
        "seed":seed,"iterations":iterations,
        "block_4h_bars":block,
        "number_of_models":len(names),
        "number_of_time_clusters":count,
        "fold_time_clusters":[int(x.shape[0]) for x in fold_arrays],
        "models":per,
        "warning":"Approximate confidence intervals; dependent labels, correlated coins and changing market regimes invalidate naive IID inference.",
    }


def _regime_attribution(predictions):
    groups=defaultdict(list)
    for item in predictions:
        groups[item["regime"]].append(item)
    result={}
    for name,items in sorted(groups.items()):
        y=np.array([p["label"] for p in items])
        model=np.array([p["probability_win"] for p in items])
        base=np.array([p["baseline_probability"] for p in items])
        result[name]={
            "observations":len(items),
            "actual_positive_rate":float(y.mean()),
            "mean_predicted_probability":float(model.mean()),
            "brier_improvement":float(np.mean((base-y)**2-(model-y)**2)),
            "mean_label_net_return_fraction":float(np.mean(
                [p["net_return_fraction"] for p in items])),
            "warning":"Labels overlap in time and markets, not independent trade returns.",
        }
    return result


def _nonoverlapping_entries(predictions,threshold,horizon):
    if not 0.5<=threshold<1:
        raise ValueError("Fixed threshold invalid")
    selected=[]
    last_exit={}
    for item in sorted(predictions,key=lambda p:(p["i"],p["symbol"])):
        if item["probability_win"]<threshold:
            continue
        symbol=item["symbol"]
        if item["i"]<=last_exit.get(symbol,-1):
            continue
        selected.append(item)
        last_exit[symbol]=item["i"]+horizon
    return selected


def _net_at_cost(net, old_fee,old_slip,new_fee,new_slip):
    """Convert original modeled net price ratio back to gross, then re-cost.

    IMPORTANT: signal and label are held fixed; no re-training or re-labelling.
    This is a selected-trade *sensitivity*, not a re-optimized backtest.
    """
    original_ratio=(1+net)*(1+old_fee)/(1-old_fee)*(1+old_slip)/(1-old_slip)
    return (original_ratio*(1-new_slip)/(1+new_slip)*
            (1-new_fee)/(1+new_fee)-1)


def _cost_sensitivity(predictions,horizon,baseline_fee_bps,
                      baseline_slip_bps,threshold=.65):
    selected=_nonoverlapping_entries(predictions,threshold,horizon)
    scenarios={
        "zero_cost":(0,0),
        "baseline":(baseline_fee_bps,baseline_slip_bps),
        "double_baseline":(2*baseline_fee_bps,2*baseline_slip_bps),
        "stress_3x":(3*baseline_fee_bps,3*baseline_slip_bps),
    }
    original_fee=baseline_fee_bps/10000
    original_slip=baseline_slip_bps/10000
    results={}
    for name,(fee_bps,slip_bps) in scenarios.items():
        if fee_bps>=200 or slip_bps>=200:
            raise ValueError("Cost sensitivity exceeds supported range")
        trades=[_net_at_cost(p["net_return_fraction"],
                            original_fee,original_slip,
                            fee_bps/10000,slip_bps/10000)
                for p in selected]
        results[name]={
            "fee_bps_per_side":fee_bps,
            "slippage_bps_per_side":slip_bps,
            "trades":len(trades),
            "win_rate":float(np.mean(np.asarray(trades)>0)) if trades else None,
            "mean_net_return_fraction":float(np.mean(trades)) if trades else None,
            "median_net_return_fraction":float(np.median(trades)) if trades else None,
        }
    return {
        "model":"all_features_prespecified_reference",
        "entry_probability_threshold":threshold,
        "nonoverlap":"per coin; consecutive fixed-horizon signals never overlap",
        "cost_scenarios":results,
        "caution":"This is research unit-notional return, NOT actual portfolio PnL. Price impact and spreads are not observed.",
    }


def robustness_report(candle_map,*,horizon=6,fee_bps=10,slippage_bps=5,
                      iterations=600,block=18,seed=2026):
    if not 0<=fee_bps<100 or not 0<=slippage_bps<100:
        raise ValueError("Fee and slippage bps must be in [0,100)")
    dev,holdout,details,_=build_development_results(
        candle_map,horizon,fee_bps,slippage_bps)
    names,arrays=_per_time_differences(details)
    bootstrap=paired_block_bootstrap(
        names,arrays,iterations=iterations,block=block,seed=seed)
    # For regime and cost, the prespecified all-features model is used.
    # Do NOT choose the best feature variant based on the same windows.
    baseline=details["all_features"]
    predictions=[p for fold in baseline for p in fold["predictions"]]
    per_coin={}
    for coin in sorted(candle_map):
        selected=[p for p in predictions if p["symbol"]==coin]
        per_coin[coin]=_regime_attribution(selected)
    # Holdout barriers are tested without calculating a future holdout label.
    if max(s["i"]+horizon for s in dev)>=holdout["holdout_first_feature_index"]:
        raise AssertionError("Final 20% holdout was not purged")
    return {
        "mode":"STAGE13_ROBUSTNESS_RESEARCH_ONLY",
        "source":"Real user-supplied CSV when CLI used; no market data invented",
        "holdout_unscored":True,
        "holdout_labels_computed":False,
        "holdout_first_timestamp":holdout["held_out_first_timestamp"],
        "holdout_first_index":holdout["holdout_first_feature_index"],
        "holdout_reserved_observations":holdout["held_out_observations_reserved"],
        "development_label_last_index":max(s["i"]+horizon for s in dev),
        "development_observations":len(dev),
        "bootstrap":bootstrap,
        "prespecified_model_regimes":_regime_attribution(predictions),
        "prespecified_model_by_coin_regimes":per_coin,
        "cost_sensitivity":_cost_sensitivity(predictions,horizon,
                                              fee_bps,slippage_bps),
        "research_only":True,
        "promotion_to_paper_execution":False,
        "caveats":[
            "Six candidate variants are assessed together using a common max-statistic; no post-selection false confidence.",
            "Confidence intervals/p-values are exploratory and approximate due to autocorrelation, label overlap, cross-coin correlation and nonstationarity.",
            "Regime buckets use *only signal-time features*; volatility threshold learned on training fit data.",
            "No execution simulation, risk-adjusted capital deployment, exchange spread or book depth has been modeled.",
            "No test-set predictions, labels or training on the final 20% were calculated.",
            "The previous development periods were explored before this run; results are not preregistered or definitive statistical proof.",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="Stage13 robustness diagnostics: no trading")
    p.add_argument("--csv",nargs="+",required=True,help="SYMBOL=PATH")
    p.add_argument("--horizon-bars",type=int,default=6)
    p.add_argument("--bootstrap-iterations",type=int,default=600)
    p.add_argument("--block-bars",type=int,default=18)
    p.add_argument("--seed",type=int,default=2026)
    p.add_argument("--fee-bps",type=float,default=10)
    p.add_argument("--slippage-bps",type=float,default=5)
    p.add_argument("--output",default="data/reports/quant_robustness.json")
    args=p.parse_args(argv)
    candles={}
    for assignment in args.csv:
        if "=" not in assignment:
            p.error("Use SYMBOL=PATH")
        symbol,path=assignment.split("=",1)
        symbol=symbol.upper().strip()
        if symbol in candles:
            p.error("Duplicate symbol "+symbol)
        if not Path(path).is_file():
            p.error("Missing CSV "+path+"; download historical data first")
        candles[symbol]=read_candles(path)
    report=robustness_report(
        candles,horizon=args.horizon_bars,
        iterations=args.bootstrap_iterations,block=args.block_bars,
        seed=args.seed,fee_bps=args.fee_bps,slippage_bps=args.slippage_bps,
    )
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    summary={
        "mode":report["mode"],
        "holdout_unscored":report["holdout_unscored"],
        "holdout_labels_computed":report["holdout_labels_computed"],
        "holdout_first_timestamp":report["holdout_first_timestamp"],
        "development_observations":report["development_observations"],
        "bootstrap":report["bootstrap"],
        "cost_sensitivity":report["cost_sensitivity"],
        "research_only":True,
        "promotion_to_paper_execution":False,
        "saved":str(output),
    }
    print(json.dumps(summary,indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()
