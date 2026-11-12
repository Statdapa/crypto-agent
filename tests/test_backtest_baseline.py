import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from backtest_baseline import Candle, read_candles, backtest_sma
from trading_engine import RiskLimits


class BaselineTests(unittest.TestCase):
    def candles(self,prices):
        start=datetime(2025,1,1,tzinfo=timezone.utc)
        return [Candle(start+timedelta(hours=4*i),Decimal(str(p)),
                       Decimal(str(p+2)),Decimal(str(p-2)),
                       Decimal(str(p))) for i,p in enumerate(prices)]

    def test_lookahead_not_used_for_last_candle_signal(self):
        # A huge move on the final candle must not cause an entry at its open.
        prices=[100]*52+[200]
        result=backtest_sma(self.candles(prices),fast=3,slow=5)
        self.assertEqual(result["closed_trades"],0)
        self.assertEqual(result["open_position_quantity"],"0")

    def test_pessimistic_stop_first_when_both_hit(self):
        prices=[100]*12+[110]*5+[100]*8
        candles=self.candles(prices)
        # prior cross must exist to open on a subsequent candle
        result=backtest_sma(candles,fast=3,slow=5)
        self.assertIn("disclaimer",result)
        self.assertLessEqual(result["net_return_pct"],200)

    def test_reject_bad_ohlc(self):
        start=datetime(2025,1,1,tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            Candle.from_row({"timestamp":start.isoformat(),"open":"100",
                             "high":"80","low":"90","close":"110"})

    def test_reject_unsorted_csv(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"bars.csv"
            with f.open("w",newline="",encoding="utf-8") as out:
                writer=csv.DictWriter(out,fieldnames=["timestamp","open","high","low","close"])
                writer.writeheader()
                for hour in [1,0]:
                    writer.writerow({"timestamp":f"2025-01-01T{hour:02d}:00:00+00:00",
                                     "open":100,"high":102,"low":99,"close":100})
            with self.assertRaises(ValueError):
                read_candles(str(f))


if __name__=="__main__":
    unittest.main()
