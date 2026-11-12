"""Stage 16: conservative, read-only research evidence gate and provenance.

Audit Stage 13 robustness, Stage 14 economic proxy and Stage 15 timestamped
forward shadow observations. Never query markets, fit a model, write exchange
orders, open historical holdout, or modify paper/shadow ledger.
A research-review pass can NEVER authorize live or automated paper trading.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from shadow_research import _read_json, HORIZON, THRESHOLD, BAR_DURATION

EXPECTED_MODELS={
    "all_features","without_momentum","without_volatility",
    "without_trend","without_oscillators","only_momentum",
}
MIN_INDEPENDENT_SELECTED=30
MAX_TOP_TWO_POSITIVE_SHARE=.75
MAX_PROXY_DRAWDOWN=.10


def _finite_number(val,name):
    if isinstance(val,bool) or not isinstance(val,(float,int)) or not math.isfinite(val):
        raise ValueError(name+" must be a finite number")
    return float(val)


def _load_report(path,expected_mode):
    file=Path(path)
    if not file.is_file():
        raise FileNotFoundError(
            str(file)+" is missing. Run the corresponding earlier research stage first."
        )
    raw=file.read_bytes()
    report=json.loads(raw)
    if not isinstance(report,dict) or report.get("mode")!=expected_mode:
        raise ValueError("Unexpected report mode/schema: "+str(file))
    return report,hashlib.sha256(raw).hexdigest()


def _check(items,name,predicate,observed):
    items.append({
        "id":name,
        "pass":bool(predicate),
        "observed":observed,
    })


def _shadow(model_path,ledger_path):
    model_file=Path(model_path)
    ledger_file=Path(ledger_path)
    if not model_file.exists() and not ledger_file.exists():
        return {
            "state":"NOT_STARTED",
            "observations":0,"settled":0,"selected_nonoverlapping_settled":0,
            "brier_improvement":None,
            "mean_selected_net_return_fraction":None,
            "model_sha256":None,
            "warning":"Stage 15 needs fresh, prospectively recorded forecasts. Never backfill forecasts.",
        }
    if not model_file.is_file() or not ledger_file.is_file():
        raise ValueError("Stage 15 has only one of its model/ledger files; incomplete shadow record")
    model=_read_json(model_file)
    if model.get("promotion_to_paper_execution") is not False or model.get("eligible_for_live_trading") is not False:
        raise ValueError("Frozen model must explicitly prohibit execution")
    if (model.get("threshold")!=THRESHOLD or
            model.get("horizon_bars")!=HORIZON or
            model.get("fee_bps_per_side")!=10 or
            model.get("slippage_bps_per_side")!=5):
        raise ValueError("Frozen shadow specification differs from predeclared research protocol")
    sha=model["sha256"]
    # Read-only SQLite connection: auditing must not create or alter a ledger.
    uri="file:"+quote(ledger_file.resolve().as_posix(),safe="/:")+"?mode=ro"
    connection=sqlite3.connect(uri,uri=True,timeout=5)
    connection.row_factory=sqlite3.Row
    try:
        observations=connection.execute(
            "SELECT o.model_sha,o.symbol,o.signal_open,o.observed_at,"
            "o.probability,o.baseline,s.settled_at,s.entry_open,s.exit_open,s.net_return,s.positive "
            "FROM observations o LEFT JOIN settlements s "
            "ON o.model_sha=s.model_sha AND o.symbol=s.symbol "
            "AND o.signal_open=s.signal_open "
            "ORDER BY o.signal_open,o.symbol"
        ).fetchall()
    finally:
        connection.close()
    all_rows=[]
    freeze_at=datetime.fromisoformat(model["frozen_at"])
    for row in observations:
        if row["model_sha"]!=sha:
            raise ValueError("Shadow DB contains observations for a different model freeze")
        if row["symbol"] not in model["symbols"]:
            raise ValueError("Shadow row symbol is not in frozen universe")
        signal=datetime.fromisoformat(row["signal_open"])
        observed=datetime.fromisoformat(row["observed_at"])
        if signal.tzinfo is None or observed.tzinfo is None:
            raise ValueError("Missing timezone in shadow timestamp")
        if not freeze_at<observed or not freeze_at<signal+BAR_DURATION or not signal+BAR_DURATION<=observed<=signal+BAR_DURATION+timedelta(minutes=30):
            raise ValueError("Shadow observation does not satisfy prospective timestamp window")
        p=_finite_number(row["probability"],"shadow probability")
        b=_finite_number(row["baseline"],"shadow baseline")
        if not 0<=p<=1 or not 0<=b<=1:
            raise ValueError("Shadow probabilities outside unit interval")
        if abs(b-float(model["fit_positive_rate"]))>1e-8:
            raise ValueError("Shadow baseline differs from frozen fit prior")
        settled=row["positive"] is not None
        if settled:
            if not row["settled_at"] or not row["entry_open"] or not row["exit_open"]:
                raise ValueError("Missing prospective settlement timestamps")
            entry=datetime.fromisoformat(row["entry_open"])
            exit_=datetime.fromisoformat(row["exit_open"])
            settled_at=datetime.fromisoformat(row["settled_at"])
            if any(x.tzinfo is None for x in (entry,exit_,settled_at)):
                raise ValueError("Naive prospective settlement timestamps")
            if not (observed<entry and
                    exit_==entry+(HORIZON-1)*BAR_DURATION and
                    settled_at>=exit_+BAR_DURATION):
                raise ValueError("Shadow settlement violates delayed-entry timeline")
            ret=_finite_number(row["net_return"],"shadow net return")
            if ret<=-1 or int(ret>0)!=row["positive"]:
                raise ValueError("Shadow settlement outcome inconsistent")
        elif row["net_return"] is not None or row["settled_at"] is not None:
            raise ValueError("Incomplete shadow settlement")
        all_rows.append({
            "symbol":row["symbol"],"signal":signal,
            "prob":p,"baseline":b,"settled":settled,
            "positive":int(row["positive"]) if settled else None,
            "return":ret if settled else None,
        })
    settled=[x for x in all_rows if x["settled"]]
    brier_delta=(sum((x["baseline"]-x["positive"])**2 -
                     (x["prob"]-x["positive"])**2 for x in settled)/len(settled)
                 if settled else None)
    # Select all prospective signals first, BEFORE seeing if settlement exists.
    # Cross-symbol correlation remains; minimum time clusters enforced later.
    chosen=[]
    last_selected={}
    for row in all_rows:
        if row["prob"]<THRESHOLD:
            continue
        prev=last_selected.get(row["symbol"])
        if prev is not None and row["signal"]-prev<timedelta(hours=4*(HORIZON+1)):
            continue
        chosen.append(row)
        last_selected[row["symbol"]]=row["signal"]
    completed=[x for x in chosen if x["settled"]]
    events=len(set(x["signal"] for x in completed))
    avg=(sum(x["return"] for x in completed)/len(completed)
         if completed else None)
    return {
        "state":"STARTED",
        "model_sha256":sha,
        "frozen_at":model["frozen_at"],
        "observations":len(all_rows),
        "settled":len(settled),
        "pending":len(all_rows)-len(settled),
        "selected_nonoverlapping_all":len(chosen),
        "selected_nonoverlapping_settled":len(completed),
        "selected_distinct_signal_times":events,
        "brier_improvement":brier_delta,
        "mean_selected_net_return_fraction":avg,
        "warning":"Nonoverlap is per symbol; BTC and SOL remain correlated. Local timestamps and SQLite are not independent attestations.",
    }


def audit(stage13,stage14,shadow):
    """Strict fail-closed, observational criteria; never an execution flag."""
    checks=[]
    if stage13.get("holdout_unscored") is not True or stage13.get("holdout_labels_computed") is not False:
        raise ValueError("Stage 13 already exposed reserved holdout")
    if stage14.get("holdout_evaluated") is not False or stage14.get("holdout_labels_computed") is not False:
        raise ValueError("Stage 14 already exposed reserved holdout")
    if stage13.get("holdout_first_timestamp")!=stage14.get("holdout_first_timestamp"):
        raise ValueError("Stage 13 and 14 used different historical input/holdout boundary")
    if stage13.get("promotion_to_paper_execution") is not False or stage14.get("promotion_to_paper_execution") is not False:
        raise ValueError("Research output attempted model promotion")
    models=stage13.get("bootstrap",{}).get("models",{})
    if not isinstance(models,dict) or set(models)!=EXPECTED_MODELS:
        raise ValueError("Stage 13 must contain exactly the six declared model variants")
    eligible=[]
    for name,row in sorted(models.items()):
        mean=_finite_number(row.get("mean_brier_improvement"),name+" Brier")
        lower=_finite_number(row.get("simultaneous_one_sided_lower_95"),name+" lower confidence bound")
        p=_finite_number(row.get("one_sided_maxT_p_adjusted"),name+" p-value")
        if p<0 or p>1:
            raise ValueError(name+" has invalid p-value")
        if row.get("significant_positive_at_familywise_5pct") is not (lower>0):
            raise ValueError(name+" significance flag does not match lower bound")
        if mean>0 and lower>0 and p<=.05:
            eligible.append(name)
    _check(checks,"stage13_familywise_predictive_evidence",
           bool(eligible),{"candidates_passing":eligible,"models_evaluated":len(models)})
    concentration=stage14.get("concentration",{})
    portfolio=stage14.get("portfolio_proxy",{})
    n=concentration.get("fixed_horizon_selected")
    n_port=portfolio.get("closed_trades")
    if not isinstance(n,int) or isinstance(n,bool) or n<0:
        raise ValueError("Stage 14 selected trade count invalid")
    if not isinstance(n_port,int) or isinstance(n_port,bool) or n_port<0:
        raise ValueError("Stage 14 portfolio trade count invalid")
    _check(checks,"stage14_fixed_horizon_sample",
           n>=MIN_INDEPENDENT_SELECTED,{"selected":n,"minimum":MIN_INDEPENDENT_SELECTED})
    _check(checks,"stage14_portfolio_sample",
           n_port>=MIN_INDEPENDENT_SELECTED,{"completed":n_port,"minimum":MIN_INDEPENDENT_SELECTED})
    best2=concentration.get("best_two_share_of_positive_return_sum")
    if best2 is not None:
        best2=_finite_number(best2,"top two positive return share")
    _check(checks,"stage14_profit_concentration",
           best2 is not None and best2<=MAX_TOP_TWO_POSITIVE_SHARE,
           {"best_two_share_of_positive_return_sum":best2,"maximum":MAX_TOP_TWO_POSITIVE_SHARE})
    remaining=concentration.get("mean_without_best_two")
    if remaining is not None:
        remaining=_finite_number(remaining,"mean without two best")
    _check(checks,"stage14_without_top_two",
           remaining is not None and remaining>0,
           {"mean_without_best_two":remaining})
    net=_finite_number(portfolio.get("net_return_fraction"),"portfolio net return")
    dd=_finite_number(portfolio.get("max_close_to_close_drawdown_fraction"),"portfolio drawdown")
    if dd<0 or dd>1:
        raise ValueError("Invalid portfolio drawdown fraction")
    _check(checks,"stage14_portfolio_positive_risk_bounded",
           net>0 and dd<=MAX_PROXY_DRAWDOWN and portfolio.get("halted") is False,
           {"return_fraction":net,"drawdown_fraction":dd,
            "halted":portfolio.get("halted")})
    shadow_n=shadow.get("selected_nonoverlapping_settled",0)
    distinct=shadow.get("selected_distinct_signal_times",0)
    _check(checks,"stage15_prospective_sample",
           shadow_n>=MIN_INDEPENDENT_SELECTED and distinct>=MIN_INDEPENDENT_SELECTED,
           {"nonoverlapping_selected_settled":shadow_n,
            "distinct_signal_times":distinct,"minimum":MIN_INDEPENDENT_SELECTED})
    brier=shadow.get("brier_improvement")
    ret=shadow.get("mean_selected_net_return_fraction")
    _check(checks,"stage15_prospective_positive_metrics",
           brier is not None and ret is not None and brier>0 and ret>0,
           {"brier_improvement":brier,"mean_selected_net_return_fraction":ret})
    failures=[x["id"] for x in checks if not x["pass"]]
    return {
        "mode":"STAGE16_EVIDENCE_REVIEW_NO_ORDERS",
        "decision":"RESEARCH_NO_GO" if failures else "ELIGIBLE_FOR_INDEPENDENT_RESEARCH_REVIEW_ONLY",
        "failed_checks":failures,"checks":checks,
        "holdout_scored":False,
        "model_promoted":False,
        "paper_automation_enabled":False,
        "live_trading_enabled":False,
        "limitations":[
            "Even passing this conservative, exploratory checklist NEVER enables execution.",
            "Original development windows were already inspected; significance is exploratory, not preregistered.",
            "Stage 14 portfolio is an OHLC proxy, not the running Telegram paper agent or actual fills.",
            "Stage 15 timestamps and database are locally controlled, not independent audit attestations.",
            "Financial samples remain serially dependent and cross-coin correlated; count threshold alone is not statistical power.",
            "Reserved holdout remains untouched and must not be repeatedly optimized.",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="Stage 16 read-only research evidence auditor")
    p.add_argument("--stage13",default="data/reports/quant_robustness.json")
    p.add_argument("--stage14",default="data/reports/economic_edge.json")
    p.add_argument("--shadow-model",default="data/shadow/frozen_model.json")
    p.add_argument("--shadow-ledger",default="data/shadow/observations.db")
    p.add_argument("--output",default="data/reports/evidence_review.json")
    args=p.parse_args(argv)
    report13,sha13=_load_report(args.stage13,"STAGE13_ROBUSTNESS_RESEARCH_ONLY")
    report14,sha14=_load_report(args.stage14,"STAGE14_ECONOMIC_EDGE_RESEARCH_ONLY")
    shadow=_shadow(args.shadow_model,args.shadow_ledger)
    result=audit(report13,report14,shadow)
    result["sources"]={
        "stage13_sha256":sha13,
        "stage14_sha256":sha14,
        "shadow_model_sha256":shadow.get("model_sha256"),
    }
    result["prospective_shadow"]=shadow
    destination=Path(args.output)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(
        json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+"\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "mode":result["mode"],
        "decision":result["decision"],
        "failed_checks":result["failed_checks"],
        "prospective_shadow":shadow,
        "holdout_scored":False,
        "paper_automation_enabled":False,
        "live_trading_enabled":False,
        "saved":str(destination),
    },indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()
