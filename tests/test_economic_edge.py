"""Offline Stage 14 economic edge, causality and financial accounting tests."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np

from backtest_baseline import Candle
from economic_edge import (
    concentration_audit,simulate_shared_portfolio,economic_edge_report,
)


def candles(n=740,price=100):
    origin=datetime(2024,1,1,tzinfo=timezone.utc)
    return [
        Candle(origin+timedelta(hours=4*i),
               Decimal(str(price)),Decimal(str(price+1)),
               Decimal(str(price-1)),Decimal(str(price)))
        for i in range(n)
    ]


def model_data(n=740):
    # Nontrivial price pattern so calibrated models have both label classes.
    result={}
    for sym,offset in (("BTC/USDT",0),("SOL/USDT",35)):
        items=[]
        t0=datetime(2024,1,1,tzinfo=timezone.utc)
        for i in range(n):
            mid=100+offset+.019*i+2.8*np.sin(i/8)+.7*np.cos(i/3)
            op=mid+.1
            cl=mid-.1
            items.append(Candle(
                t0+timedelta(hours=4*i),
                Decimal(str(op)),Decimal(str(max(op,cl)+.8)),
                Decimal(str(min(op,cl)-.8)),Decimal(str(cl)),
            ))
        result[sym]=items
    return result


def pred(bars,sym,i,p=.8,regime="DOWN_HIGH_VOL"):
    return {
        "symbol":sym,"i":i,"at":bars[i].timestamp.isoformat(),
        "probability_win":p,"regime":regime,
    }


def changed_bar(bars,idx,o,h,l,c):
    b=bars[idx]
    bars[idx]=Candle(
        b.timestamp,Decimal(str(o)),Decimal(str(h)),
        Decimal(str(l)),Decimal(str(c)),
    )


class ConcentrationTests(unittest.TestCase):
    def test_leave_best_two_exposes_outlier_dependence(self):
        rows=[
            {"at":f"t{i}","symbol":"SOL/USDT","regime":"DOWN_HIGH_VOL",
             "development_fold":2,"probability_win":.7,"net_return_fraction":x}
            for i,x in enumerate((-.06,-.02,-.01,.00,.1157,.1164))
        ]
        r=concentration_audit(rows)
        self.assertEqual(r["fixed_horizon_selected"],6)
        self.assertAlmostEqual(r["overall"]["mean_return_fraction"],
                               sum(x["net_return_fraction"] for x in rows)/6)
        self.assertLess(r["mean_without_best_two"],0)
        self.assertEqual(len(r["leave_one_out"]),6)
        self.assertGreater(r["best_two_share_of_net_unit_return_sum"],1)
        self.assertEqual(r["by_coin"]["SOL/USDT"]["count"],6)

    def test_empty_robustness_does_not_fabricate_profit(self):
        r=concentration_audit([])
        self.assertIsNone(r["overall"]["win_rate"])
        self.assertIsNone(r["mean_without_best_two"])


class PortfolioProxyTests(unittest.TestCase):
    def test_stop_first_even_if_take_also_hit_same_bar(self):
        series=candles()
        changed_bar(series,101,100,112,94,104)
        result=simulate_shared_portfolio(
            {"BTC/USDT":series},[pred(series,"BTC/USDT",100)],
            boundary=500
        )
        self.assertEqual(result["closed_trades"],1)
        self.assertEqual(result["trades"][0]["exit_reason"],"stop_first")
        self.assertLess(result["net_return_fraction"],0)
        self.assertAlmostEqual(
            Decimal(result["initial_cash_usdt"])+
            Decimal(result["total_realized_net_pnl_usdt"]),
            Decimal(result["ending_cash_usdt"]),
        )

    def test_next_bar_open_and_stop_gap(self):
        series=candles()
        changed_bar(series,112,90,92,89,91)
        result=simulate_shared_portfolio(
            {"SOL/USDT":series},[pred(series,"SOL/USDT",110)],
            boundary=500
        )
        self.assertEqual(result["entries"],1)
        t=result["trades"][0]
        self.assertEqual(t["opened_at"],series[111].timestamp.isoformat())
        self.assertEqual(t["closed_at"],series[112].timestamp.isoformat())
        self.assertEqual(t["exit_reason"],"stop_gap")
        self.assertLess(result["net_return_fraction"],-.01)

    def test_takeprofit_and_horizon_exit_distinct(self):
        series=candles()
        changed_bar(series,131,100,110,99,108)
        take=simulate_shared_portfolio(
            {"BTC/USDT":series},[pred(series,"BTC/USDT",130)],
            boundary=500
        )
        self.assertEqual(take["trades"][0]["exit_reason"],"take_profit")
        self.assertGreater(take["net_return_fraction"],0)
        flat=simulate_shared_portfolio(
            {"BTC/USDT":candles()},
            [pred(candles(),"BTC/USDT",130)],boundary=500
        )
        self.assertEqual(flat["trades"][0]["exit_reason"],"horizon_close")
        self.assertLess(flat["net_return_fraction"],0)

    def test_shared_portfolio_limits_on_same_bar(self):
        data={sym:candles() for sym in
              ("BTC/USDT","SOL/USDT","ETH/USDT","XRP/USDT")}
        signals=[pred(data[sym],sym,150,.8+i*.01)
                 for i,sym in enumerate(data)]
        r=simulate_shared_portfolio(data,signals,boundary=500)
        self.assertEqual(r["entries"],3)
        self.assertEqual(r["closed_trades"],3)
        self.assertEqual(r["rejection_counts"]["max_positions_or_total_exposure"],1)
        self.assertGreater(float(r["ending_cash_usdt"]),0)
        self.assertLess(float(r["ending_cash_usdt"]),1000)

    def test_no_prediction_above_threshold_no_orders(self):
        data={"BTC/USDT":candles()}
        r=simulate_shared_portfolio(
            data,[pred(data["BTC/USDT"],"BTC/USDT",120,.6)],
            boundary=500
        )
        self.assertEqual(r["entries"],0)
        self.assertEqual(r["closed_trades"],0)
        self.assertEqual(float(r["ending_cash_usdt"]),1000)

    def test_rejects_prediction_reaching_holdout(self):
        data={"BTC/USDT":candles()}
        with self.assertRaisesRegex(ValueError,"holdout"):
            simulate_shared_portfolio(
                data,[pred(data["BTC/USDT"],"BTC/USDT",495)],
                boundary=500
            )

    def test_heldout_candles_never_impact_stage14_report(self):
        original=model_data()
        first=economic_edge_report(original)
        boundary=original["BTC/USDT"].index(
            next(x for x in original["BTC/USDT"]
                 if x.timestamp.isoformat()==first["holdout_first_timestamp"])
        )
        changed={sym:list(bars) for sym,bars in original.items()}
        for sym,series in changed.items():
            for i in range(boundary,len(series)):
                changed_bar(series,i,1000,1001,999,1000)
        second=economic_edge_report(changed)
        self.assertEqual(first,second)
        self.assertLess(first["development_label_last_index"],boundary)
        self.assertFalse(first["holdout_evaluated"])
        self.assertFalse(first["holdout_labels_computed"])
        self.assertFalse(first["promotion_to_paper_execution"])
        self.assertFalse(first["decision_gate"]["eligible_for_live_trading"])


if __name__=="__main__":
    unittest.main()
