"""Stage 15: frozen, prospectively timestamped ML SHADOW observations.

Research-only. NEVER sends orders, changes paper ledger, or queries private
exchange APIs. This lab records predictions soon after the market candle
closes, then separately evaluates them when *future* candles have completed.
No backfilled predictions are accepted as prospective.

The original fitted label (i+1 open -> i+6 close) is NOT executable if
observation happens after i+1 open. Settlement instead measures from the
first future 4h bar OPEN strictly after the actual observation timestamp
through its 6th bar CLOSE. This is deliberately labeled a different economic
proxy; no claim of direct historical benchmark equivalence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from backtest_baseline import read_candles
from feature_ablation import development_samples
from quant_research import (
    FEATURES, LOOKBACK, _fixed_features, _fit_logistic,
    _sigmoid, validate_history,
)

HORIZON=6
THRESHOLD=.65
FEE_BPS=10
SLIPPAGE_BPS=5
BAR_DURATION=timedelta(hours=4)
MAX_OBSERVATION_DELAY=timedelta(minutes=30)
MODE="FROZEN_SHADOW_RESEARCH_ONLY_NEVER_ORDER"


def _utc(value):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value,str):
        value=datetime.fromisoformat(value.replace("Z","+00:00"))
    if value.tzinfo is None:
        raise ValueError("Timestamp must contain a timezone")
    return value.astimezone(timezone.utc)


def _json_bytes(data):
    return json.dumps(data,sort_keys=True,separators=(",",":"),
                      allow_nan=False).encode("utf-8")


def _fingerprint(data):
    return hashlib.sha256(_json_bytes(data)).hexdigest()


def _read_json(path):
    doc=json.loads(Path(path).read_text(encoding="utf-8"))
    fingerprint=doc.pop("sha256",None)
    if not fingerprint or fingerprint!=_fingerprint(doc):
        raise ValueError("Frozen model was modified or is invalid")
    doc["sha256"]=fingerprint
    return doc


def freeze(candle_map,*,now=None,model_path="data/shadow/frozen_model.json"):
    """Fit only pre-holdout labeled observations, store immutable snapshot."""
    now=_utc(now)
    path=Path(model_path)
    if path.exists():
        raise FileExistsError("Frozen model exists. Never overwrite it in a shadow trial: "+str(path))
    timestamps=validate_history(candle_map,HORIZON)
    if timestamps[-1]+BAR_DURATION>now:
        raise ValueError("Latest input contains an unfinished future 4h candle")
    dev,holdout=development_samples(candle_map,HORIZON,FEE_BPS,SLIPPAGE_BPS)
    indices=sorted({s["i"] for s in dev})
    split=indices[int(len(indices)*.8)]
    fit=[s for s in dev if s["i"]+HORIZON<split]
    cal=[s for s in dev if s["i"]>=split]
    if min(len(fit),len(cal))<60:
        raise ValueError("Too little purged training/calibration history")
    x_fit=np.asarray([s["x"] for s in fit],dtype=float)
    y_fit=np.asarray([s["label"] for s in fit],dtype=float)
    x_cal=np.asarray([s["x"] for s in cal],dtype=float)
    y_cal=np.asarray([s["label"] for s in cal],dtype=float)
    mean=x_fit.mean(axis=0)
    std=np.where(x_fit.std(axis=0)<1e-8,1.,x_fit.std(axis=0))
    norm=lambda x:np.clip((x-mean)/std,-12,12)
    weights=_fit_logistic(norm(x_fit),y_fit,penalty=5)
    cal_logits=np.column_stack((np.ones(len(x_cal)),norm(x_cal)))@weights
    cal_weights=_fit_logistic(
        cal_logits.reshape(-1,1),y_cal,penalty=15,
        prior=np.array([0.,1.])
    )
    doc={
        "mode":MODE,"version":1,"frozen_at":now.isoformat(),
        "symbols":sorted(candle_map),"features":list(FEATURES),
        "model":"L2_logistic_then_logistic_calibration",
        "scaling_mean":mean.tolist(),"scaling_std":std.tolist(),
        "coefficients":weights.tolist(),
        "calibration_coefficients":cal_weights.tolist(),
        "fit_positive_rate":float(y_fit.mean()),
        "fit_count":len(fit),"calibration_count":len(cal),
        "fit_last_label_index":max(s["i"]+HORIZON for s in fit),
        "calibration_first_feature_index":split,
        "calibration_last_label_index":max(s["i"]+HORIZON for s in cal),
        "reserved_holdout_from":holdout["held_out_first_timestamp"],
        "reserved_holdout_index":holdout["holdout_first_feature_index"],
        "snapshot_last_completed_open":timestamps[-1].isoformat(),
        "horizon_bars":HORIZON,"threshold":THRESHOLD,
        "fee_bps_per_side":FEE_BPS,
        "slippage_bps_per_side":SLIPPAGE_BPS,
        "training_target":"i+1 OPEN to i+6 CLOSE after modeled costs",
        "shadow_settlement_target":"first bar OPEN strictly AFTER observation to sixth-bar CLOSE; NOT training target",
        "promotion_to_paper_execution":False,
        "eligible_for_live_trading":False,
    }
    if doc["calibration_last_label_index"]>=doc["reserved_holdout_index"]:
        raise AssertionError("Holdout leakage in model freeze")
    doc["sha256"]=_fingerprint(doc)
    path.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive create; never silently replace a pre-existing candidate.
    with path.open("x",encoding="utf-8") as f:
        json.dump(doc,f,indent=2,ensure_ascii=False)
        f.write("\n")
    return doc


def _db(path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(path),timeout=15)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS observations (
            model_sha TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal_open TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            probability REAL NOT NULL,
            baseline REAL NOT NULL,
            PRIMARY KEY(model_sha,symbol,signal_open)
        );
        CREATE TABLE IF NOT EXISTS settlements (
            model_sha TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal_open TEXT NOT NULL,
            settled_at TEXT NOT NULL,
            entry_open TEXT NOT NULL,
            exit_open TEXT NOT NULL,
            net_return REAL NOT NULL,
            positive INTEGER NOT NULL CHECK(positive IN (0,1)),
            PRIMARY KEY(model_sha,symbol,signal_open),
            FOREIGN KEY(model_sha,symbol,signal_open)
                REFERENCES observations(model_sha,symbol,signal_open)
        );
    """)
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _model_probability(model,bars):
    x=_fixed_features(bars,len(bars)-1)
    means=np.asarray(model["scaling_mean"],dtype=float)
    std=np.asarray(model["scaling_std"],dtype=float)
    coefficients=np.asarray(model["coefficients"],dtype=float)
    cal=np.asarray(model["calibration_coefficients"],dtype=float)
    if len(x)!=len(FEATURES) or len(coefficients)!=len(x)+1 or len(cal)!=2:
        raise ValueError("Invalid frozen feature dimensions")
    z=np.clip((x-means)/std,-12,12)
    logit=np.r_[1.,z]@coefficients
    p=float(_sigmoid(cal[0]+cal[1]*logit))
    if not math.isfinite(p) or not 0<=p<=1:
        raise ValueError("Nonfinite frozen probability")
    return p


def observe(candle_map,*,now=None,model_path="data/shadow/frozen_model.json",
            ledger_path="data/shadow/observations.db"):
    now=_utc(now)
    model=_read_json(model_path)
    if now<=_utc(model["frozen_at"]):
        raise ValueError("Cannot observe before model was frozen")
    if sorted(candle_map)!=model["symbols"]:
        raise ValueError("Expected frozen BTC/SOL symbol universe")
    timestamps=validate_history(candle_map,HORIZON)
    signal_open=timestamps[-1]
    signal_close=signal_open+BAR_DURATION
    if signal_close<=_utc(model["frozen_at"]):
        raise ValueError("Latest completed candle must close AFTER model freeze")
    if not signal_close<=now<=signal_close+MAX_OBSERVATION_DELAY:
        raise ValueError("Observation must use latest candle within 30 minutes of its close; refresh completed data")
    if len(timestamps)<=LOOKBACK:
        raise ValueError("Insufficient closed candle history")
    values={}
    for sym,bars in candle_map.items():
        if bars[-1].timestamp!=signal_open:
            raise ValueError("Symbol candle misalignment")
        values[sym]=_model_probability(model,bars)
    db=_db(ledger_path)
    written=0
    try:
        with db:
            for sym,p in sorted(values.items()):
                cur=db.execute("""
                    INSERT OR IGNORE INTO observations
                    (model_sha,symbol,signal_open,observed_at,probability,baseline)
                    VALUES (?,?,?,?,?,?)
                    """,(model["sha256"],sym,signal_open.isoformat(),
                          now.isoformat(),p,model["fit_positive_rate"]))
                written+=cur.rowcount
    finally:
        db.close()
    return {
        "mode":MODE,"signal_open":signal_open.isoformat(),
        "recorded_at":now.isoformat(),"predictions":values,
        "records_inserted":written,"already_recorded":len(values)-written,
        "threshold":THRESHOLD,"paper_or_live_orders":0,
        "warning":"Observed probabilities do NOT submit simulated/real orders. Decision after bar close cannot fill at previous next-open."
    }


def settle(candle_map,*,now=None,model_path="data/shadow/frozen_model.json",
           ledger_path="data/shadow/observations.db"):
    now=_utc(now)
    model=_read_json(model_path)
    if sorted(candle_map)!=model["symbols"]:
        raise ValueError("Frozen symbol mismatch")
    timestamps=validate_history(candle_map,HORIZON)
    last_complete=timestamps[-1]+BAR_DURATION
    if last_complete>now:
        raise ValueError("Future/incomplete historical candle")
    db=_db(ledger_path)
    created=[]
    try:
        pending=db.execute("""
            SELECT o.* FROM observations o
            LEFT JOIN settlements s ON (o.model_sha=s.model_sha
                AND o.symbol=s.symbol AND o.signal_open=s.signal_open)
            WHERE s.model_sha IS NULL AND o.model_sha=?
            ORDER BY o.signal_open,o.symbol
        """,(model["sha256"],)).fetchall()
        bytime={t:i for i,t in enumerate(timestamps)}
        for row in pending:
            observed=_utc(row["observed_at"])
            symbol=row["symbol"]
            if observed>=now:
                continue
            # No hindsight fill: first open STRICTLY AFTER actual recording.
            entry_idx=next((i for i,t in enumerate(timestamps)
                            if t>observed),None)
            if entry_idx is None:
                continue
            exit_idx=entry_idx+HORIZON-1
            if exit_idx>=len(timestamps):
                continue
            if timestamps[exit_idx]+BAR_DURATION>now:
                continue
            bars=candle_map[symbol]
            fee=FEE_BPS/10000.
            slip=SLIPPAGE_BPS/10000.
            entry=float(bars[entry_idx].open)*(1+slip)
            exit_value=float(bars[exit_idx].close)*(1-slip)
            net=exit_value*(1-fee)/(entry*(1+fee))-1.
            if not math.isfinite(net):
                raise ValueError("Nonfinite realized research proxy")
            with db:
                cur=db.execute("""
                    INSERT OR IGNORE INTO settlements
                    (model_sha,symbol,signal_open,settled_at,
                     entry_open,exit_open,net_return,positive)
                    VALUES (?,?,?,?,?,?,?,?)
                """,(model["sha256"],symbol,row["signal_open"],
                      now.isoformat(),timestamps[entry_idx].isoformat(),
                      timestamps[exit_idx].isoformat(),net,int(net>0)))
            if cur.rowcount:
                created.append({"symbol":symbol,"signal_open":row["signal_open"],
                                "entry_open":timestamps[entry_idx].isoformat(),
                                "exit_open":timestamps[exit_idx].isoformat(),
                                "net_return":net})
    finally:
        db.close()
    return {"mode":MODE,"settlements_inserted":len(created),
            "settlements":created,"paper_or_live_orders":0}


def report(*,model_path="data/shadow/frozen_model.json",
           ledger_path="data/shadow/observations.db"):
    model=_read_json(model_path)
    db=_db(ledger_path)
    try:
        rows=db.execute("""
            SELECT o.symbol,o.signal_open,o.probability,o.baseline,
                   o.observed_at,s.net_return,s.positive
            FROM observations o
            LEFT JOIN settlements s ON(o.model_sha=s.model_sha
                AND o.symbol=s.symbol AND o.signal_open=s.signal_open)
            WHERE o.model_sha=? ORDER BY o.signal_open,o.symbol
        """,(model["sha256"],)).fetchall()
    finally:
        db.close()
    done=[r for r in rows if r["positive"] is not None]
    selected=[r for r in done if r["probability"]>=THRESHOLD]
    def brier(a,key):
        return float(np.mean([(r[key]-r["positive"])**2 for r in a])) if a else None
    return {
        "mode":MODE,"model_sha256":model["sha256"],
        "frozen_at":model["frozen_at"],
        "all_observations":len(rows),"settled_observations":len(done),
        "pending_observations":len(rows)-len(done),
        "settled_brier_model":brier(done,"probability"),
        "settled_brier_baseline":brier(done,"baseline"),
        "settled_brier_improvement":(
            brier(done,"baseline")-brier(done,"probability") if done else None
        ),
        "selected_probability_at_least_65pct":len(selected),
        "selected_positive_rate":float(np.mean([r["positive"] for r in selected]))
                                 if selected else None,
        "selected_mean_net_return_fraction":float(np.mean(
            [r["net_return"] for r in selected])) if selected else None,
        "promotion_to_paper_execution":False,
        "research_only":True,
        "limitations":[
            "No model was shown to have statistical/economic edge in Stage 13.",
            "Shadow observations must be recorded within 30 minutes after a closed 4h candle; no retrospective backfilling.",
            "The shadow settlement entry at the FIRST future 4h open is later than the original historical training target; values are not apples-to-apples.",
            "Settled labels may overlap, coin returns correlate, and selected count is not independent trade sample size.",
            "Selected returns are unit-notional proxies, NOT shared-cash portfolio PnL.",
            "A model frozen today cannot retroactively claim prospective performance on older candles.",
        ],
    }


def _candle_inputs(items):
    candle_map={}
    for item in items:
        if "=" not in item:
            raise ValueError("Use SYMBOL=PATH")
        symbol,path=item.split("=",1)
        symbol=symbol.upper().strip()
        if symbol in candle_map:
            raise ValueError("Duplicate symbol")
        if not Path(path).is_file():
            raise FileNotFoundError(path)
        candle_map[symbol]=read_candles(path)
    return candle_map


def main(argv=None):
    parser=argparse.ArgumentParser(description="Stage 15 frozen forward shadow lab: NEVER trades")
    parser.add_argument("action",choices=("freeze","observe","settle","report"))
    parser.add_argument("--csv",nargs="+",help="SYMBOL=CSV_FILE for freeze, observe, settle")
    parser.add_argument("--model",default="data/shadow/frozen_model.json")
    parser.add_argument("--ledger",default="data/shadow/observations.db")
    args=parser.parse_args(argv)
    if args.action!="report" and not args.csv:
        parser.error("--csv BTC/USDT=... SOL/USDT=... required")
    if args.action=="report":
        result=report(model_path=args.model,ledger_path=args.ledger)
    else:
        candles=_candle_inputs(args.csv)
        if args.action=="freeze":
            result=freeze(candles,model_path=args.model)
        elif args.action=="observe":
            result=observe(candles,model_path=args.model,ledger_path=args.ledger)
        else:
            result=settle(candles,model_path=args.model,ledger_path=args.ledger)
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()
