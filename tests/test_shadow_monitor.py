"""Stage17 prospective shadow monitor: offline tests; zero orders."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research_governance import _shadow
from shadow_monitor import (
    MODE,_metrics,_calibration_bins,_select_without_hindsight,
    _verify_settlement_chronology,forward_monitor,
)
from shadow_research import _db,_fingerprint


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)
        self.model=self.path/"frozen.json"
        self.ledger=self.path/"prospective.db"

    def create_model(self):
        doc={
            "mode":"FROZEN_SHADOW_RESEARCH_ONLY_NEVER_ORDER",
            "frozen_at":"2026-11-11T16:00:00+00:00",
            "symbols":["BTC/USDT","SOL/USDT"],
            "threshold":.65,"horizon_bars":6,
            "fee_bps_per_side":10,"slippage_bps_per_side":5,
            "fit_positive_rate":.31,
            "promotion_to_paper_execution":False,
            "eligible_for_live_trading":False,
        }
        doc["sha256"]=_fingerprint(doc)
        self.model.write_text(json.dumps(doc),encoding="utf-8")
        return doc

    def insert(self,doc,*,signal="2026-11-12T04:00:00+00:00",
               observed="2026-11-12T08:05:00+00:00",
               coin="BTC/USDT",prob=.72,settled=True,
               entry="2026-11-12T12:00:00+00:00",
               exit_="2026-11-13T08:00:00+00:00",
               outcome=.02,settled_at="2026-11-13T12:05:00+00:00"):
        db=_db(self.ledger)
        with db:
            db.execute(
                "INSERT INTO observations "
                "(model_sha,symbol,signal_open,observed_at,probability,baseline) "
                "VALUES (?,?,?,?,?,?)",
                (doc["sha256"],coin,signal,observed,prob,.31)
            )
            if settled:
                db.execute(
                    "INSERT INTO settlements "
                    "(model_sha,symbol,signal_open,settled_at,entry_open,"
                    "exit_open,net_return,positive) VALUES (?,?,?,?,?,?,?,?)",
                    (doc["sha256"],coin,signal,settled_at,entry,exit_,
                     outcome,int(outcome>0))
                )
        db.close()

    def test_unstarted_is_not_traded_or_fabricated(self):
        r=forward_monitor(model_path=self.model,ledger_path=self.ledger)
        self.assertEqual(r["state"],"NOT_STARTED")
        self.assertEqual(r["decision"],"INSUFFICIENT_PROSPECTIVE_EVIDENCE")
        self.assertFalse(r["live_trading_enabled"])
        self.assertFalse(self.model.exists())
        self.assertFalse(self.ledger.exists())

    def test_frozen_without_observations_has_no_created_ledger(self):
        doc=self.create_model()
        r=forward_monitor(model_path=self.model,ledger_path=self.ledger)
        self.assertEqual(r["state"],"FROZEN_AWAITING_OBSERVATIONS")
        self.assertEqual(r["model_sha256"],doc["sha256"])
        self.assertFalse(self.ledger.exists())

    def test_one_real_settled_and_one_pending(self):
        doc=self.create_model()
        self.insert(doc)
        self.insert(doc,coin="SOL/USDT",settled=False,prob=.30)
        before=self.ledger.read_bytes()
        r=forward_monitor(model_path=self.model,ledger_path=self.ledger)
        self.assertEqual(r["state"],"MONITORING")
        self.assertEqual(r["observations"],2)
        self.assertEqual(r["settled"],1)
        self.assertEqual(r["pending"],1)
        self.assertEqual(r["overall"]["positive_rate"],1.)
        self.assertAlmostEqual(r["overall"]["brier_model"],(.72-1)**2)
        self.assertEqual(r["selected_trades"]["settled_selected"],1)
        self.assertEqual(r["selected_trades"]["distinct_settled_signal_times"],1)
        self.assertIsNone(r["probability_calibration_bins"][0]["actual_positive_rate"])
        self.assertEqual(r["chronological_comparison"]["available"],False)
        self.assertEqual(r["decision"],"INSUFFICIENT_PROSPECTIVE_EVIDENCE")
        self.assertFalse(r["paper_automation_enabled"])
        self.assertFalse(r["live_trading_enabled"])
        self.assertEqual(self.ledger.read_bytes(),before)

    def test_hindsight_entry_not_earliest_future_open_rejected(self):
        doc=self.create_model()
        # Valid chronological order but not the first open after 08:05 UTC.
        self.insert(doc,entry="2026-11-12T16:00:00+00:00",
                    exit_="2026-11-13T12:00:00+00:00",
                    settled_at="2026-11-13T16:05:00+00:00")
        with self.assertRaisesRegex(ValueError,"FIRST future open"):
            forward_monitor(model_path=self.model,ledger_path=self.ledger)

    def test_modified_frozen_model_fails_closed(self):
        doc=self.create_model()
        doc["fit_positive_rate"]=.99
        self.model.write_text(json.dumps(doc),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"modified"):
            forward_monitor(model_path=self.model,ledger_path=self.ledger)

    def test_outcome_independent_nonoverlap_selection_includes_pending(self):
        rows=[
            {"symbol":"BTC/USDT","signal_open":"2026-11-12T04:00:00+00:00",
             "probability":.75,"positive":None},
            {"symbol":"BTC/USDT","signal_open":"2026-11-12T08:00:00+00:00",
             "probability":.80,"positive":1},
            {"symbol":"SOL/USDT","signal_open":"2026-11-12T08:00:00+00:00",
             "probability":.70,"positive":1},
        ]
        selected=_select_without_hindsight(rows)
        self.assertEqual(len(selected),2)
        self.assertEqual(len([x for x in selected if x["positive"] is not None]),1)

    def test_empty_metrics_reveal_no_fake_win_rate(self):
        self.assertIsNone(_metrics([])["positive_rate"])
        self.assertEqual(sum(x["observations"] for x in _calibration_bins([])),0)

    def test_missing_frozen_model_but_existing_ledger_fails(self):
        self.ledger.write_text("not a legitimate ledger")
        with self.assertRaisesRegex(ValueError,"missing"):
            forward_monitor(model_path=self.model,ledger_path=self.ledger)


if __name__=="__main__":
    unittest.main()
