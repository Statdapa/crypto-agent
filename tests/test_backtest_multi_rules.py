"""Causal multi-asset backtest validations with synthetic candles; offline."""
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from unittest.mock import patch

from backtest_baseline import Candle
from backtest_multi_rules import backtest, history_signal
from multi_portfolio import PortfolioLimits
from trading_engine import D


def bars(n=120,price="100",symbol_idx=0):
    start=datetime(2025,1,1,tzinfo=timezone.utc)
    p=D(price)+D(symbol_idx)
    return [Candle(start+timedelta(hours=4*i),p,p*D("1.07"),p*D("0.93"),p)
            for i in range(n)]


class MultiRulesBacktestTests(unittest.TestCase):
    def setUp(self):
        self.data={"BTC/USDT":bars(120,"100"),
                   "SOL/USDT":bars(120,"50")}

    def test_no_future_candle_leak_into_signal(self):
        original=bars(90)
        altered=bars(90)
        first=history_signal(original,55)
        # Future candle changed beyond the calculation time: must not matter.
        altered[-1]=Candle(altered[-1].timestamp,D("1000000"),
                           D("1000000"),D("1000000"),D("1000000"))
        self.assertEqual(history_signal(altered,55),first)

    def test_train_test_split_and_no_trades(self):
        with patch("backtest_multi_rules.history_signal",return_value="HOLD"):
            report=backtest(self.data,train_fraction=.7)
        self.assertEqual(report["train"]["closed_trades"],0)
        self.assertEqual(report["test"]["closed_trades"],0)
        self.assertIsNone(report["test"]["win_rate"])
        self.assertEqual(report["ending_equity_marked_usdt"],"1000")
        self.assertEqual(report["open_positions_at_end"],0)
        self.assertEqual(len(report["equity_curve"]),60)
        self.assertEqual(report["equity_curve"][-1]["segment"],"test")

    def test_buy_using_prev_closed_signal_not_current_candle(self):
        observed=[]
        def predict(bars,i,neutral_fg=50):
            observed.append(i)
            return "BUY" if i==59 else "HOLD"
        with patch("backtest_multi_rules.history_signal",side_effect=predict):
            report=backtest({"BTC/USDT":self.data["BTC/USDT"]})
        self.assertTrue(all(index<120 for index in observed))
        self.assertEqual(report["all"]["closed_trades"],1)
        self.assertEqual(report["trades"][0]["reason"],"same_bar_stop")
        self.assertEqual(report["trades"][0]["opened_at"],
                         self.data["BTC/USDT"][60].timestamp.isoformat())

    def test_stop_first_if_both_levels_touched(self):
        with patch("backtest_multi_rules.history_signal",
                   side_effect=lambda bars,i,neutral_fg=50:
                               "BUY" if i==59 else "HOLD"):
            report=backtest({"BTC/USDT":self.data["BTC/USDT"]})
        self.assertEqual(report["trades"][0]["reason"],"same_bar_stop")
        self.assertLess(D(report["trades"][0]["net_pnl"]),0)

    def test_shared_capital_limit_holds(self):
        with patch("backtest_multi_rules.history_signal",return_value="BUY"):
            report=backtest(self.data)
        self.assertLessEqual(report["open_positions_at_end"],3)
        self.assertLess(D(report["ending_equity_marked_usdt"]),D("1000"))

    def test_invalid_gapped_data_rejected(self):
        source=bars(120)
        source[61]=Candle(source[61].timestamp+timedelta(hours=1),
                          source[61].open,source[61].high,source[61].low,
                          source[61].close)
        with self.assertRaisesRegex(ValueError,"Gaps"):
            backtest({"BTC/USDT":source})

    def test_insufficient_data_rejected(self):
        with self.assertRaisesRegex(ValueError,">=100"):
            backtest({"BTC/USDT":bars(99)})

    def test_unapproved_sentiment_proxy_rejected(self):
        with self.assertRaises(ValueError):
            backtest(self.data,neutral_fg=15)


if __name__=="__main__":
    unittest.main()
