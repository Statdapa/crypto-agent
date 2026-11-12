"""Stage 17 — read-only monitoring of actually timestamped SHADOW predictions.

Never places orders, changes the shadow/paper ledger, evaluates the reserved
historical holdout, refits an ML model or retrospectively generates signals.
Insufficient prospective outcomes are reported as insufficient, not an edge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import numpy as np

from shadow_research import _read_json, BAR_DURATION, HORIZON, THRESHOLD
from research_governance import _shadow

MODE="STAGE17_FORWARD_MONITOR_RESEARCH_ONLY"
MIN_SETTLED_TIME_CLUSTERS=100
MIN_SELECTED_TIME_CLUSTERS=30
PROBABILITY_BINS=((0.,.2),(.2,.4),(.4,.6),(.6,.8),(.8,1.000001))
FOUR_HOURS=int(BAR_DURATION.total_seconds())


def _open_read_only(path):
    file=Path(path)
    uri="file:"+quote(file.resolve().as_posix(),safe="/:")+"?mode=ro"
    connection=sqlite3.connect(uri,uri=True,timeout=5)
    connection.row_factory=sqlite3.Row
    return connection


def _empty_metrics():
    return {
        "settled":0,"distinct_signal_times":0,
        "positive_rate":None,"mean_predicted_probability":None,
        "mean_frozen_baseline":None,
        "brier_model":None,"brier_frozen_baseline":None,
        "brier_improvement":None,"log_loss_model":None,
        "calibration_gap_prediction_minus_actual":None,
    }


def _metrics(rows):
    if not rows:
        return _empty_metrics()
    y=np.asarray([r["positive"] for r in rows],dtype=float)
    prob=np.asarray([r["probability"] for r in rows],dtype=float)
    base=np.asarray([r["baseline"] for r in rows],dtype=float)
    clipped=np.clip(prob,1e-8,1-1e-8)
    return {
        "settled":len(rows),
        "distinct_signal_times":len({r["signal_open"] for r in rows}),
        "positive_rate":float(np.mean(y)),
        "mean_predicted_probability":float(np.mean(prob)),
        "mean_frozen_baseline":float(np.mean(base)),
        "brier_model":float(np.mean((prob-y)**2)),
        "brier_frozen_baseline":float(np.mean((base-y)**2)),
        "brier_improvement":float(np.mean((base-y)**2-(prob-y)**2)),
        "log_loss_model":float(np.mean(-y*np.log(clipped)-(1-y)*np.log1p(-clipped))),
        "calibration_gap_prediction_minus_actual":float(np.mean(prob)-np.mean(y)),
    }


def _calibration_bins(rows):
    bins=[]
    for left,right in PROBABILITY_BINS:
        group=[p for p in rows if left<=p["probability"]<right]
        bins.append({
            "range_from_inclusive":left,
            "range_to_exclusive":min(right,1.),
            "observations":len(group),
            "mean_predicted_probability":(
                float(np.mean([x["probability"] for x in group])) if group else None
            ),
            "actual_positive_rate":(
                float(np.mean([x["positive"] for x in group])) if group else None
            ),
        })
    return bins


def _select_without_hindsight(rows):
    """Choose on ALL predictions (pending included), then filter to settled."""
    picked=[]
    last_signal_by_coin={}
    for r in sorted(rows,key=lambda item:(item["signal_open"],item["symbol"])):
        if r["probability"]<THRESHOLD:
            continue
        last=last_signal_by_coin.get(r["symbol"])
        signal=datetime.fromisoformat(r["signal_open"])
        if last is not None and signal-last<timedelta(hours=4*(HORIZON+1)):
            continue
        picked.append(r)
        last_signal_by_coin[r["symbol"]]=signal
    return picked


def _verified_rows(ledger_path):
    """Read in one snapshot. No SQL writes or local market-data downloads."""
    conn=_open_read_only(ledger_path)
    try:
        cursor=conn.execute("""
            SELECT o.model_sha,o.symbol,o.signal_open,o.observed_at,
                   o.probability,o.baseline,
                   s.settled_at,s.entry_open,s.exit_open,s.net_return,s.positive
            FROM observations o LEFT JOIN settlements s
            ON (o.model_sha=s.model_sha AND o.symbol=s.symbol
                AND o.signal_open=s.signal_open)
            ORDER BY o.signal_open,o.symbol
        """)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def _verify_settlement_chronology(row):
    observed=datetime.fromisoformat(row["observed_at"])
    signal=datetime.fromisoformat(row["signal_open"])
    if observed.tzinfo is None or signal.tzinfo is None:
        raise ValueError("Shadow observation timestamps must have timezone")
    if int(signal.timestamp())%FOUR_HOURS!=0:
        raise ValueError("Shadow 4h signal is not UTC aligned")
    if row["positive"] is None:
        if any(row.get(k) is not None for k in
               ("settled_at","entry_open","exit_open","net_return")):
            raise ValueError("Partially settled observation")
        return
    for k in ("entry_open","exit_open","settled_at"):
        if row.get(k) is None:
            raise ValueError("Missing shadow settlement timestamp "+k)
    entry=datetime.fromisoformat(row["entry_open"])
    exit_=datetime.fromisoformat(row["exit_open"])
    settlement=datetime.fromisoformat(row["settled_at"])
    if any(t.tzinfo is None for t in (entry,exit_,settlement)):
        raise ValueError("Shadow settlement timestamps require UTC offset")
    # First future candle OPEN strictly after observation, not at a bar
    # that started before the real prediction was recorded.
    expected=datetime.fromtimestamp(
        ((math.floor(observed.timestamp()/FOUR_HOURS))+1)*FOUR_HOURS,
        tz=timezone.utc,
    )
    if entry!=expected:
        raise ValueError("Shadow entry is not the FIRST future open after observation")
    if exit_!=entry+(HORIZON-1)*BAR_DURATION:
        raise ValueError("Shadow settlement must exit at sixth future bar")
    if settlement<exit_+BAR_DURATION:
        raise ValueError("Shadow outcome settled before exit candle completed")


def _rolling_windows(rows):
    """Disjoint early/late chronological descriptions, NOT significance tests."""
    settled=sorted([r for r in rows if r["positive"] is not None],
                   key=lambda r:(r["signal_open"],r["symbol"]))
    times=sorted({r["signal_open"] for r in settled})
    if len(times)<2*MIN_SETTLED_TIME_CLUSTERS:
        return {
            "available":False,"reason":"Need >=200 distinct settled signal times before descriptive early/late drift comparison",
            "distinct_settled_signal_times":len(times),
        }
    midpoint=times[len(times)//2]
    early=[r for r in settled if r["signal_open"]<midpoint]
    late=[r for r in settled if r["signal_open"]>=midpoint]
    early_m=_metrics(early)
    late_m=_metrics(late)
    return {
        "available":True,
        "method":"disjoint_early_late_calendar_order_not_significance",
        "boundary_signal_open":midpoint,
        "early":early_m,
        "late":late_m,
        "change_brier_improvement_late_minus_early":
            late_m["brier_improvement"]-early_m["brier_improvement"],
        "change_positive_rate_late_minus_early":
            late_m["positive_rate"]-early_m["positive_rate"],
        "warning":"Descriptive drift only. No p-value, independence or market regime inferred.",
    }


def forward_monitor(*,model_path="data/shadow/frozen_model.json",
                    ledger_path="data/shadow/observations.db"):
    model_file=Path(model_path)
    ledger_file=Path(ledger_path)
    if not model_file.exists():
        if ledger_file.exists():
            raise ValueError("Shadow ledger exists but frozen model is missing")
        return {
            "mode":MODE,"state":"NOT_STARTED",
            "decision":"INSUFFICIENT_PROSPECTIVE_EVIDENCE",
            "reason":"Run Stage 15 freeze first; never backfill historical forecasts",
            "observations":0,"settled":0,
            "model_promoted":False,
            "paper_automation_enabled":False,"live_trading_enabled":False,
        }
    model=_read_json(model_file)
    if model.get("promotion_to_paper_execution") is not False or model.get("eligible_for_live_trading") is not False:
        raise ValueError("Frozen ML snapshot is not research-only")
    if not ledger_file.is_file():
        return {
            "mode":MODE,"state":"FROZEN_AWAITING_OBSERVATIONS",
            "decision":"INSUFFICIENT_PROSPECTIVE_EVIDENCE",
            "reason":"Model frozen; run Stage 15 observe on NEW fully closed bars",
            "model_sha256":model["sha256"],"observations":0,"settled":0,
            "model_promoted":False,
            "paper_automation_enabled":False,"live_trading_enabled":False,
        }
    # First run existing Stage16 integrity checks; they verify the
    # immutable model fingerprint and each observation's timestamp.
    integrity=_shadow(model_file,ledger_file)
    rows=_verified_rows(ledger_file)
    if any(r["model_sha"]!=model["sha256"] for r in rows):
        raise ValueError("Shadow ledger has mixed model versions")
    for row in rows:
        _verify_settlement_chronology(row)
    settled=[r for r in rows if r["positive"] is not None]
    picked=_select_without_hindsight(rows)
    chosen=[p for p in picked if p["positive"] is not None]
    groups=defaultdict(list)
    for r in settled:
        groups[r["symbol"]].append(r)
    selected_ret=[p["net_return"] for p in chosen]
    distinct_settled=len({p["signal_open"] for p in settled})
    distinct_picked=len({p["signal_open"] for p in chosen})
    enough_predictive=distinct_settled>=MIN_SETTLED_TIME_CLUSTERS
    enough_selected=(len(chosen)>=MIN_SELECTED_TIME_CLUSTERS and
                     distinct_picked>=MIN_SELECTED_TIME_CLUSTERS)
    base=_metrics(settled)
    selected_return=(float(np.mean(selected_ret)) if chosen else None)
    can_review=(enough_predictive and enough_selected and
                base["brier_improvement"] is not None and
                base["brier_improvement"]>0 and selected_return is not None and
                selected_return>0)
    return {
        "mode":MODE,
        "state":"NO_SETTLED_OUTCOMES" if not settled else "MONITORING",
        "decision":("ELIGIBLE_FOR_INDEPENDENT_SHADOW_REVIEW_ONLY" if can_review
                    else "INSUFFICIENT_PROSPECTIVE_EVIDENCE"),
        "model_sha256":model["sha256"],
        "frozen_at":model["frozen_at"],
        "observations":len(rows),
        "settled":len(settled),
        "pending":len(rows)-len(settled),
        "completed_time_clusters":distinct_settled,
        "overall":base,
        "by_coin":{k:_metrics(v) for k,v in sorted(groups.items())},
        "probability_calibration_bins":_calibration_bins(settled),
        "selected_trades":{
            "selection":"prespecified probability >=0.65, time-disjoint per coin, including pending at selection",
            "all_selected":len(picked),
            "settled_selected":len(chosen),
            "distinct_settled_signal_times":distinct_picked,
            "positive_rate":(
                float(np.mean([r["positive"] for r in chosen])) if chosen else None
            ),
            "mean_net_return_fraction":selected_return,
            "top_two_wins_share_of_positive_return_sum":(
                float(sum(sorted((r for r in selected_ret if r>0),reverse=True)[:2]) /
                      sum(r for r in selected_ret if r>0))
                if any(r>0 for r in selected_ret) else None
            ),
        },
        "chronological_comparison":_rolling_windows(rows),
        "evidence_thresholds":{
            "min_settled_distinct_signal_times":MIN_SETTLED_TIME_CLUSTERS,
            "min_nonoverlap_selected_distinct_signal_times":MIN_SELECTED_TIME_CLUSTERS,
            "met_settled":enough_predictive,
            "met_selected":enough_selected,
        },
        "stage16_integrity_check":"PASSED",
        "model_promoted":False,
        "paper_automation_enabled":False,
        "live_trading_enabled":False,
        "warnings":[
            "No statistical significance claim; thresholds are screening heuristics, not a power calculation.",
            "Shadow outcomes use delayed-entry target DIFFERENT from historical ML training labels.",
            "Coin returns remain cross-correlated; event counts overestimate effective independence.",
            "Performance of selected forecasts is NOT a cash-constrained portfolio backtest.",
            "No continuous background service or auto-monitoring is installed.",
            "Local time and SQLite can be manipulated and are not independently notarized.",
            "Even ELIGIBLE review status never authorizes any order execution.",
        ],
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description="Read-only Stage17 prospective shadow performance monitor")
    parser.add_argument("--model",default="data/shadow/frozen_model.json")
    parser.add_argument("--ledger",default="data/shadow/observations.db")
    parser.add_argument("--output",default="data/reports/shadow_monitor.json")
    args=parser.parse_args(argv)
    result=forward_monitor(model_path=args.model,ledger_path=args.ledger)
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    summary={
        "mode":result["mode"],"state":result["state"],
        "decision":result["decision"],
        "observations":result["observations"],"settled":result["settled"],
        "overall":result.get("overall"),
        "selected_trades":result.get("selected_trades"),
        "evidence_thresholds":result.get("evidence_thresholds"),
        "model_promoted":False,"paper_automation_enabled":False,
        "live_trading_enabled":False,
        "saved":str(output),
    }
    print(json.dumps(summary,indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()
