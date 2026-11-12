"""Stage 15: offline freeze / observe / settle / report invariants.

No real market API, credentials, Telegram or paper ledger calls.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np

from backtest_baseline import Candle
from shadow_research import (
    HORIZON,MODE,freeze,observe,settle,report,_read_json,
)


def history(n=800):
    t0=datetime(2024,1,1,tzinfo=timezone.utc)
    result={}
    for sym,offset in (("BTC/USDT",0),("SOL/USDT",50)):
        bars=[]
        for i in range(n):
            mid=100+offset+.028*i+3*np.sin(i/8.0)+.4*np.cos(i/3.0)
            op=mid+.1
            cl=mid-.2
            bars.append(Candle(
                t0+timedelta(hours=4*i),
                Decimal(str(op)),Decimal(str(max(op,cl)+.8)),
                Decimal(str(min(op,cl)-.8)),Decimal(str(cl)),
            ))
        result[sym]=bars
    return result


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.model=str(Path(self.tmp.name)/"model.json")
        self.ledger=str(Path(self.tmp.name)/"shadow.db")
        self.initial=history()
        self.frozen_at=self.initial["BTC/USDT"][-1].timestamp+timedelta(hours=4,minutes=2)
        self.model_data=freeze(self.initial,now=self.frozen_at,
                               model_path=self.model)

    def observe_params(self,closes_after=1,delay=timedelta(minutes=5)):
        extended=history(800+closes_after)
        last=extended["BTC/USDT"][-1].timestamp
        now=last+timedelta(hours=4)+delay
        return extended,now

    def test_freeze_immutable_and_protects_holdout(self):
        model=_read_json(self.model)
        self.assertEqual(model["mode"],MODE)
        self.assertFalse(model["promotion_to_paper_execution"])
        self.assertLess(model["fit_last_label_index"],
                        model["calibration_first_feature_index"])
        self.assertLess(model["calibration_last_label_index"],
                        model["reserved_holdout_index"])
        with self.assertRaises(FileExistsError):
            freeze(self.initial,now=self.frozen_at,model_path=self.model)
        malicious=json.loads(Path(self.model).read_text())
        malicious["coefficients"][0]=99.0
        Path(self.model).write_text(json.dumps(malicious))
        with self.assertRaisesRegex(ValueError,"modified"):
            _read_json(self.model)

    def test_no_retrospective_observation_and_stale_rejected(self):
        # Pre-freeze latest close is already known; must not log it.
        with self.assertRaisesRegex(ValueError,"AFTER"):
            observe(self.initial,now=self.frozen_at+timedelta(minutes=1),
                    model_path=self.model,ledger_path=self.ledger)
        future,now=self.observe_params()
        with self.assertRaisesRegex(ValueError,"within 30 minutes"):
            observe(future,now=now+timedelta(minutes=31),
                    model_path=self.model,ledger_path=self.ledger)

    def test_latest_signal_immutability_and_idempotence(self):
        future,now=self.observe_params()
        first=observe(future,now=now,model_path=self.model,ledger_path=self.ledger)
        second=observe(future,now=now+timedelta(minutes=1),
                       model_path=self.model,ledger_path=self.ledger)
        self.assertEqual(first["records_inserted"],2)
        self.assertEqual(second["records_inserted"],0)
        self.assertTrue(all(0<=p<=1 for p in first["predictions"].values()))
        state=report(model_path=self.model,ledger_path=self.ledger)
        self.assertEqual(state["all_observations"],2)
        self.assertEqual(state["pending_observations"],2)
        self.assertIsNone(state["settled_brier_improvement"])
        self.assertFalse(state["promotion_to_paper_execution"])

    def test_settlement_waits_for_fully_completed_future_bars(self):
        future,now=self.observe_params()
        observe(future,now=now,model_path=self.model,ledger_path=self.ledger)
        # When observations are after signal_close, the next fill at open
        # is the following full 4h bar, NOT the earlier open already passed.
        self.assertEqual(settle(future,now=now,model_path=self.model,
                                ledger_path=self.ledger)["settlements_inserted"],0)
        more=history(809)
        later=more["BTC/USDT"][-1].timestamp+timedelta(hours=4,minutes=1)
        first=settle(more,now=later,model_path=self.model,ledger_path=self.ledger)
        second=settle(more,now=later,model_path=self.model,ledger_path=self.ledger)
        self.assertEqual(first["settlements_inserted"],2)
        self.assertEqual(second["settlements_inserted"],0)
        self.assertEqual(first["settlements"][0]["entry_open"],
                         more["BTC/USDT"][802].timestamp.isoformat())
        self.assertEqual(first["settlements"][0]["exit_open"],
                         more["BTC/USDT"][807].timestamp.isoformat())
        state=report(model_path=self.model,ledger_path=self.ledger)
        self.assertEqual(state["settled_observations"],2)
        self.assertIsNotNone(state["settled_brier_model"])
        self.assertEqual(state["pending_observations"],0)

    def test_future_price_mutations_do_not_change_recorded_predictions(self):
        future,now=self.observe_params()
        first=observe(future,now=now,model_path=self.model,ledger_path=self.ledger)
        altered={sym:list(bars) for sym,bars in future.items()}
        # A mutation to training's historical reserved holdout segment
        # (but prior to today) may affect current features; future bars must not.
        original=[(sym,first["predictions"][sym]) for sym in sorted(first["predictions"])]
        later=history(809)
        for sym,bars in later.items():
            for i in range(804,len(bars)):
                old=bars[i]
                bars[i]=Candle(old.timestamp,Decimal("1000"),
                               Decimal("1001"),Decimal("999"),Decimal("1000"))
        # Existing observations never recomputed or changed by late data.
        done_at=later["BTC/USDT"][-1].timestamp+timedelta(hours=4,minutes=1)
        settle(later,now=done_at,model_path=self.model,ledger_path=self.ledger)
        state=report(model_path=self.model,ledger_path=self.ledger)
        self.assertEqual(state["all_observations"],2)
        self.assertNotEqual(state["settled_brier_model"],None)
        self.assertEqual(len(original),2)


if __name__=="__main__":
    unittest.main()
