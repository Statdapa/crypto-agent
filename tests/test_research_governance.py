"""Stage16 offline governance tests: no real orders, no holdout reading."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research_governance import audit, _load_report, _shadow
from shadow_research import _db, _fingerprint


MODELS=("all_features","without_momentum","without_volatility",
        "without_trend","without_oscillators","only_momentum")


def stage13(positive=False):
    models={}
    for key in MODELS:
        models[key]={
            "mean_brier_improvement":.004 if positive else -.002,
            "simultaneous_one_sided_lower_95":.001 if positive else -.006,
            "one_sided_maxT_p_adjusted":.01 if positive else .8,
            "significant_positive_at_familywise_5pct":positive,
        }
    return {
        "mode":"STAGE13_ROBUSTNESS_RESEARCH_ONLY",
        "holdout_unscored":True,"holdout_labels_computed":False,
        "holdout_first_timestamp":"2026-04-26T16:00:00+00:00",
        "promotion_to_paper_execution":False,
        "bootstrap":{"models":models},
    }


def stage14(good=False):
    return {
        "mode":"STAGE14_ECONOMIC_EDGE_RESEARCH_ONLY",
        "holdout_evaluated":False,"holdout_labels_computed":False,
        "holdout_first_timestamp":"2026-04-26T16:00:00+00:00",
        "promotion_to_paper_execution":False,
        "concentration":{
            "fixed_horizon_selected":34 if good else 6,
            "best_two_share_of_positive_return_sum":.22 if good else .9,
            "mean_without_best_two":.001 if good else -.015,
        },
        "portfolio_proxy":{
            "closed_trades":36 if good else 6,
            "net_return_fraction":.06 if good else -.02,
            "max_close_to_close_drawdown_fraction":.04 if good else .12,
            "halted":False if good else True,
        }
    }


def shadow(good=False):
    return {
        "state":"STARTED",
        "selected_nonoverlapping_settled":35 if good else 4,
        "selected_distinct_signal_times":35 if good else 4,
        "brier_improvement":.012 if good else -.03,
        "mean_selected_net_return_fraction":.004 if good else -.01,
    }


class GovernanceTests(unittest.TestCase):
    def test_negative_results_fail_closed(self):
        r=audit(stage13(),stage14(),shadow())
        self.assertEqual(r["decision"],"RESEARCH_NO_GO")
        self.assertGreaterEqual(len(r["failed_checks"]),7)
        self.assertFalse(r["holdout_scored"])
        self.assertFalse(r["paper_automation_enabled"])
        self.assertFalse(r["live_trading_enabled"])

    def test_even_good_research_evidence_cannot_enable_orders(self):
        r=audit(stage13(True),stage14(True),shadow(True))
        self.assertEqual(r["decision"],"ELIGIBLE_FOR_INDEPENDENT_RESEARCH_REVIEW_ONLY")
        self.assertEqual(r["failed_checks"],[])
        self.assertFalse(r["model_promoted"])
        self.assertFalse(r["paper_automation_enabled"])
        self.assertFalse(r["live_trading_enabled"])

    def test_reject_mismatched_holdout_boundary_and_leakage(self):
        broken=stage14()
        broken["holdout_first_timestamp"]="2026-04-26T20:00:00+00:00"
        with self.assertRaisesRegex(ValueError,"different historical"):
            audit(stage13(),broken,shadow())
        broken=stage14()
        broken["holdout_labels_computed"]=True
        with self.assertRaisesRegex(ValueError,"already exposed"):
            audit(stage13(),broken,shadow())

    def test_malformed_statistical_result_rejected(self):
        a=stage13()
        a["bootstrap"]["models"]["all_features"]["one_sided_maxT_p_adjusted"]=float("nan")
        with self.assertRaisesRegex(ValueError,"finite"):
            audit(a,stage14(),shadow())
        a=stage13()
        a["bootstrap"]["models"]["all_features"]["significant_positive_at_familywise_5pct"]=True
        with self.assertRaisesRegex(ValueError,"flag"):
            audit(a,stage14(),shadow())

    def test_required_report_file_missing_does_not_create_it(self):
        with tempfile.TemporaryDirectory() as root:
            missing=Path(root)/"none.json"
            with self.assertRaises(FileNotFoundError):
                _load_report(missing,"STAGE13_ROBUSTNESS_RESEARCH_ONLY")
            self.assertFalse(missing.exists())

    def test_shadow_missing_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            model=Path(root)/"frozen.json"
            ledger=Path(root)/"ledger.db"
            self.assertEqual(_shadow(model,ledger)["state"],"NOT_STARTED")
            self.assertFalse(model.exists())
            self.assertFalse(ledger.exists())
            model.write_text("{}")
            with self.assertRaisesRegex(ValueError,"only one"):
                _shadow(model,ledger)
            self.assertFalse(ledger.exists())

    def test_shadow_valid_settled_nonoverlap_and_readonly(self):
        with tempfile.TemporaryDirectory() as root:
            model_path=Path(root)/"freeze.json"
            ledger_path=Path(root)/"shadow.db"
            model={
                "frozen_at":"2026-11-11T15:00:00+00:00",
                "symbols":["BTC/USDT","SOL/USDT"],
                "fit_positive_rate":.31,
                "threshold":.65,"horizon_bars":6,
                "fee_bps_per_side":10,"slippage_bps_per_side":5,
                "promotion_to_paper_execution":False,
                "eligible_for_live_trading":False,
            }
            model["sha256"]=_fingerprint(model)
            model_path.write_text(json.dumps(model),encoding="utf-8")
            db=_db(ledger_path)
            with db:
                db.execute(
                    "INSERT INTO observations "
                    "(model_sha,symbol,signal_open,observed_at,probability,baseline) "
                    "VALUES (?,?,?,?,?,?)",
                    (model["sha256"],"BTC/USDT",
                     "2026-11-12T04:00:00+00:00",
                     "2026-11-12T08:05:00+00:00",.72,.31)
                )
                db.execute(
                    "INSERT INTO settlements "
                    "(model_sha,symbol,signal_open,settled_at,"
                    "entry_open,exit_open,net_return,positive) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (model["sha256"],"BTC/USDT",
                     "2026-11-12T04:00:00+00:00",
                     "2026-11-14T00:01:00+00:00",
                     "2026-11-12T12:00:00+00:00",
                     "2026-11-13T08:00:00+00:00",.021,1)
                )
            db.close()
            original=ledger_path.read_bytes()
            r=_shadow(model_path,ledger_path)
            self.assertEqual(r["selected_nonoverlapping_settled"],1)
            self.assertEqual(r["selected_distinct_signal_times"],1)
            self.assertAlmostEqual(r["mean_selected_net_return_fraction"],.021)
            self.assertEqual(ledger_path.read_bytes(),original)

    def test_shadow_hindsight_timestamp_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            model_path=Path(root)/"model.json"
            ledger_path=Path(root)/"obs.db"
            model={
                "frozen_at":"2026-11-11T15:00:00+00:00",
                "symbols":["BTC/USDT"],"fit_positive_rate":.31,
                "threshold":.65,"horizon_bars":6,
                "fee_bps_per_side":10,"slippage_bps_per_side":5,
                "promotion_to_paper_execution":False,
                "eligible_for_live_trading":False,
            }
            model["sha256"]=_fingerprint(model)
            model_path.write_text(json.dumps(model))
            db=_db(ledger_path)
            with db:
                db.execute(
                    "INSERT INTO observations "
                    "(model_sha,symbol,signal_open,observed_at,probability,baseline) "
                    "VALUES (?,?,?,?,?,?)",
                    (model["sha256"],"BTC/USDT",
                     "2026-11-12T04:00:00+00:00",
                     "2026-11-12T09:00:00+00:00",.72,.31)
                )
            db.close()
            with self.assertRaisesRegex(ValueError,"prospective timestamp"):
                _shadow(model_path,ledger_path)


if __name__=="__main__":
    unittest.main()
