"""Offline audit tests for the multi-portfolio performance reporter."""
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

from multi_portfolio import MultiPaperPortfolio
from performance_report import (
    build_report, closed_trade_rows, drawdown_metrics, outcome_summary,
    report_from_ledger,
)
from trading_engine import D


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path=str(Path(temp.name)/"portfolio.db")
        self.trader=MultiPaperPortfolio(self.path)
        self.t=datetime.now(timezone.utc).replace(microsecond=0)-timedelta(minutes=8)

    def buy(self, symbol="SOL/USDT", price="100", offset=0):
        now=self.t+timedelta(minutes=offset)
        mark=self.trader.mark(symbol,price,now,now=now)
        self.assertIn(mark["action"],("HOLD","SKIP"))
        return self.trader.on_signal(symbol,"BUY",now,now=now)

    def test_empty_report_no_invented_win_rate(self):
        data=report_from_ledger(self.path)
        s=data["summary"]
        self.assertEqual(s["trades"],0)
        self.assertIsNone(s["win_rate"])
        self.assertIsNone(s["expectancy_usdt_per_closed_trade"])
        self.assertEqual(data["drawdown_observed_only"]["equity_points"],0)

    def test_open_trade_is_not_realized(self):
        self.assertEqual(self.buy()["action"],"BUY")
        r=report_from_ledger(self.path)
        self.assertEqual(r["summary"]["trades"],0)
        self.assertEqual(r["summary"]["unclosed_entries_in_ledger"],1)
        self.assertEqual(r["summary"]["currently_open_positions"],1)
        self.assertIsNone(r["summary"]["win_rate"])
        self.assertGreaterEqual(r["drawdown_observed_only"]["equity_points"],1)

    def test_two_coins_loss_profit_and_coin_groups(self):
        self.buy("SOL/USDT","100")
        self.buy("ETH/USDT","50")
        nexttime=self.t+timedelta(minutes=1)
        self.trader.mark("SOL/USDT","106",nexttime,now=nexttime)
        self.trader.mark("ETH/USDT","45",nexttime,now=nexttime)
        r=report_from_ledger(self.path)
        s=r["summary"]
        self.assertEqual(s["trades"],2)
        self.assertEqual(s["wins"],1)
        self.assertEqual(s["losses"],1)
        self.assertEqual(s["win_rate"],.5)
        self.assertIsNotNone(s["profit_factor"])
        self.assertEqual(set(r["by_coin"]),{"SOL/USDT","ETH/USDT"})
        self.assertEqual(len(r["closed_trades"]),2)
        self.assertEqual(s["unclosed_entries_in_ledger"],0)

    def test_missing_entry_reference_is_rejected(self):
        row={"id":2,"side":"SELL","entry_id":99,"symbol":"SOL/USDT",
             "qty":"1","price":"105","fee":"0.1","pnl":"4","at":self.t.isoformat(),
             "reason":"take_profit_triggered"}
        with self.assertRaisesRegex(ValueError,"unmatched"):
            closed_trade_rows([row])

    def test_equity_drawdown_uses_observed_marks_only(self):
        points=[{"equity":"1000","at":"t0"},
                {"equity":"1100","at":"t1"},
                {"equity":"880","at":"t2"},
                {"equity":"990","at":"t3"}]
        report=drawdown_metrics(points)
        self.assertAlmostEqual(report["max_drawdown_fraction"],0.2)
        self.assertEqual(report["drawdown_usdt"],"220")
        self.assertEqual(report["max_drawdown_at"],"t2")
        self.assertIsNone(drawdown_metrics([])["max_drawdown_fraction"])

    def test_median_and_expectancy(self):
        trades=[{"net_pnl":str(p),
                 "return_on_entry_notional_fraction":float(p/100)}
                for p in (-2,4,8)]
        result=outcome_summary(trades)
        self.assertEqual(result["expectancy_usdt_per_closed_trade"],str(D(10)/D(3)))
        self.assertEqual(result["median_trade_return_fraction"],.04)


if __name__=="__main__":
    unittest.main()
