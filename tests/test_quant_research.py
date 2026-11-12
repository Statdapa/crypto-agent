"""Quant research model: causal features, purged folds, fees, OOS and failure tests."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np

from backtest_baseline import Candle
from quant_research import (
    _fixed_features, _fit_logistic, _scores, _train_and_score, make_samples,
    train_walk_forward, validate_history, FOLDS,
)


def candles(n=500,offset=0):
    t0=datetime(2025,1,1,tzinfo=timezone.utc)
    result=[]
    for i in range(n):
        # Deterministic but nontrivial trend, oscillations and volatility.
        level=90+offset + i*0.033 + 3.6*np.sin(i/9) + .8*np.sin(i*1.31)
        op=level + .2*np.sin(i/3.7)
        cl=level + .3*np.cos(i/5.2)
        h=max(op,cl)+.5
        l=min(op,cl)-.5
        result.append(Candle(
            t0+timedelta(hours=4*i),
            Decimal(str(op)),Decimal(str(h)),
            Decimal(str(l)),Decimal(str(cl)),
        ))
    return result


class QuantResearchTests(unittest.TestCase):
    def setUp(self):
        self.multi={"BTC/USDT":candles(500),"SOL/USDT":candles(500,offset=40)}

    def test_features_are_strictly_causal(self):
        original=candles(500)
        mutated=original.copy()
        x=_fixed_features(original,160)
        future=mutated[180]
        mutated[180]=Candle(future.timestamp,Decimal("500"),
                           Decimal("510"),Decimal("490"),Decimal("505"))
        np.testing.assert_array_equal(x,_fixed_features(mutated,160))

    def test_label_uses_future_after_signal_and_respects_cost(self):
        free=make_samples({"BTC/USDT":candles(350)},horizon=6,
                          fee_bps=0,slippage_bps=0)
        expensive=make_samples({"BTC/USDT":candles(350)},horizon=6,
                               fee_bps=10,slippage_bps=5)
        self.assertLess(expensive[0]["future_net_return"],
                        free[0]["future_net_return"])
        self.assertEqual(expensive[0]["i"],40)
        self.assertEqual(expensive[0]["symbol"],"BTC/USDT")

    def test_multi_data_must_align(self):
        truncated=candles(500)[1:]
        with self.assertRaisesRegex(ValueError,"identical"):
            validate_history({"BTC/USDT":candles(500),
                              "SOL/USDT":truncated},6)

    def test_gaps_and_short_history_fail(self):
        damaged=candles(500)
        damaged[100]=Candle(damaged[100].timestamp+timedelta(hours=1),
                           damaged[100].open,damaged[100].high,
                           damaged[100].low,damaged[100].close)
        with self.assertRaisesRegex(ValueError,"gaps"):
            validate_history({"BTC/USDT":damaged},6)
        with self.assertRaisesRegex(ValueError,"at least 300"):
            validate_history({"BTC/USDT":candles(299)},6)

    def test_walk_forward_purge_calibration_no_overlap(self):
        report=train_walk_forward(self.multi,horizon=6,threshold=.65)
        self.assertEqual(len(report["folds"]),3)
        self.assertEqual(report["mode"],"RESEARCH_ONLY_NEVER_AUTO_TRADE")
        self.assertFalse(report["promotion_to_paper_execution"])
        for fold in report["folds"]:
            self.assertLess(fold["max_fit_label_index"],
                            fold["first_calibration_feature_index"])
            self.assertLess(fold["max_calibration_label_index"],
                            fold["first_test_feature_index"])
            self.assertGreater(fold["test_observations"],0)
            self.assertGreaterEqual(fold["model"]["brier"],0)
            self.assertLessEqual(fold["model"]["brier"],1)
            self.assertGreater(fold["fit_observations"],80)

    def test_model_is_not_guaranteed_superior(self):
        report=train_walk_forward(self.multi,horizon=6)
        self.assertIn("brier_improvement_vs_baseline",report["out_of_sample"])
        self.assertIsInstance(report["research_gate_passed_not_live_ready"],bool)
        self.assertFalse(report["promotion_to_paper_execution"])

    def test_negative_threshold_and_horizon(self):
        with self.assertRaises(ValueError):
            train_walk_forward(self.multi,threshold=.3)
        with self.assertRaises(ValueError):
            make_samples(self.multi,horizon=25)
        with self.assertRaises(ValueError):
            make_samples(self.multi,fee_bps=1000)

    def test_no_single_class_logistic_fit(self):
        with self.assertRaisesRegex(ValueError,"eight examples"):
            _fit_logistic(np.ones((25,2)),np.ones(25))

    def test_scoring_no_invented_results(self):
        self.assertIsNone(_scores([],[])["brier"])
        out=_scores([0,1],[.25,.75])
        self.assertLess(out["brier"],.1)


if __name__=="__main__":
    unittest.main()
