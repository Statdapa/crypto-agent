"""Stage 13: paired time-block inference and untouched holdout safety."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import numpy as np

from backtest_baseline import Candle
from feature_ablation import development_samples
from quant_robustness import (
    _block_indices,_net_at_cost,_nonoverlapping_entries,
    paired_block_bootstrap, robustness_report, VARIANTS,
)


def synthetic(n=730, offset=0):
    start=datetime(2024,1,1,tzinfo=timezone.utc)
    values=[]
    for i in range(n):
        price=100+offset+.03*i+3*np.sin(i/8)+.5*np.cos(i/3)
        open_=price+.1
        close=price-.15
        values.append(Candle(
            start+timedelta(hours=4*i),
            Decimal(str(open_)),
            Decimal(str(max(open_,close)+.7)),
            Decimal(str(min(open_,close)-.7)),
            Decimal(str(close)),
        ))
    return values


class TimeBlockInferenceTests(unittest.TestCase):
    def test_same_seed_same_resampled_indices(self):
        a=_block_indices(np.random.default_rng(17),120,12)
        b=_block_indices(np.random.default_rng(17),120,12)
        np.testing.assert_array_equal(a,b)
        self.assertEqual(len(a),120)
        self.assertTrue(np.all(a>=0))
        self.assertTrue(np.all(a<120))
        for k in range(0,len(a),12):
            chunk=a[k:k+12]
            self.assertTrue(np.all((np.diff(chunk)%120)==1))

    def test_block_time_clusters_synchronous_models(self):
        # Constant positive superiority should have lower bound positive.
        rng=np.random.default_rng(14)
        arr=np.column_stack((.04+rng.normal(0,.003,150),
                             -.035+rng.normal(0,.004,150)))
        r=paired_block_bootstrap(["strong","weak"],[arr],iterations=220,
                                 block=10,seed=3)
        self.assertEqual(r["number_of_time_clusters"],150)
        self.assertTrue(r["models"]["strong"]["significant_positive_at_familywise_5pct"])
        self.assertFalse(r["models"]["weak"]["significant_positive_at_familywise_5pct"])
        self.assertGreater(
            r["models"]["strong"]["simultaneous_one_sided_lower_95"],0)
        self.assertLessEqual(r["models"]["strong"]["one_sided_maxT_p_adjusted"],.05)
        self.assertEqual(
            r,paired_block_bootstrap(["strong","weak"],[arr],iterations=220,
                                      block=10,seed=3))

    def test_invalid_small_blocks_and_many_iterations(self):
        arr=np.zeros((30,2))
        with self.assertRaisesRegex(ValueError,"too long"):
            paired_block_bootstrap(["a","b"],[arr],block=18)
        with self.assertRaises(ValueError):
            paired_block_bootstrap(["a","b"],[arr],iterations=20)

    def test_cost_exact_roundtrip(self):
        gross=1.025
        fee=.001
        slip=.0005
        net=gross*(1-slip)/(1+slip)*(1-fee)/(1+fee)-1
        self.assertAlmostEqual(_net_at_cost(net,fee,slip,0,0),gross-1)
        self.assertAlmostEqual(_net_at_cost(net,fee,slip,fee,slip),net)
        self.assertLess(_net_at_cost(net,fee,slip,.003,.0015),net)

    def test_per_coin_sequential_nonoverlap(self):
        rows=[]
        for i in range(20):
            for coin in ("BTC/USDT","SOL/USDT"):
                rows.append({"symbol":coin,"i":i,"probability_win":.8})
        selected=_nonoverlapping_entries(rows,.65,6)
        self.assertEqual([x["i"] for x in selected if
                          x["symbol"]=="BTC/USDT"],[0,7,14])
        self.assertEqual(len(selected),6)

    def test_holdout_future_prices_do_not_change_development_inference(self):
        history={"BTC/USDT":synthetic(),"SOL/USDT":synthetic(offset=40)}
        original=robustness_report(history,iterations=100,block=12)
        boundary=original["holdout_first_index"]
        altered={sym:list(bars) for sym,bars in history.items()}
        # Modify ONLY prices in the held-out region, leave OHLC valid.
        for sym,bars in altered.items():
            for i in range(boundary,len(bars)):
                b=bars[i]
                bars[i]=Candle(b.timestamp,Decimal("999"),
                               Decimal("1000"),Decimal("998"),Decimal("999"))
        changed=robustness_report(altered,iterations=100,block=12)
        self.assertEqual(original,changed)
        self.assertTrue(original["holdout_unscored"])
        self.assertFalse(original["holdout_labels_computed"])
        self.assertLess(original["development_label_last_index"],boundary)
        self.assertFalse(original["promotion_to_paper_execution"])
        self.assertEqual(len(original["bootstrap"]["models"]),6)
        self.assertEqual(len(original["prespecified_model_by_coin_regimes"]),2)

    def test_future_labels_not_materialized_by_development_samples(self):
        data={"BTC/USDT":synthetic(),"SOL/USDT":synthetic(offset=40)}
        dev,hold=development_samples(data)
        self.assertLess(max(x["i"]+6 for x in dev),hold["holdout_first_feature_index"])
        self.assertEqual(len(VARIANTS),6)


if __name__=="__main__":
    unittest.main()
