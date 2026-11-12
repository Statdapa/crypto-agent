"""Offline tests for backtest CSV downloader; no Binance network needed."""
import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from download_backtest_data import export_coin, BAR_MS
from backtest_baseline import read_candles


class FakeUniverse:
    def __init__(self, candles):
        self.rows = candles

    def require(self, coin):
        return coin + "USDT"

    def candles(self, coin, interval, limit):
        if interval != "4h":
            raise AssertionError("Expected 4h")
        return self.rows[:limit]


def sample_rows(count=120, gap=False):
    start=datetime(2025,1,1,tzinfo=timezone.utc)
    epoch=int(start.timestamp()*1000)
    result=[]
    for i in range(count):
        tick=epoch + (i+(1 if gap and i>=40 else 0))*BAR_MS
        result.append([tick, 100, 110, 90, 105])
    return result


class DownloadCSVTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir=Path(t.name)

    def test_file_is_backtest_readable(self):
        result=export_coin("BTC",FakeUniverse(sample_rows()),self.dir)
        self.assertEqual(result["rows"],120)
        path=self.dir/"BTC_USDT_4h.csv"
        self.assertTrue(path.exists())
        candles=read_candles(str(path))
        self.assertEqual(len(candles),120)
        self.assertEqual(candles[0].timestamp.tzinfo,timezone.utc)

    def test_missing_candles_fails_without_creating_file(self):
        with self.assertRaisesRegex(ValueError,"fewer than 100"):
            export_coin("SOL",FakeUniverse(sample_rows(99)),self.dir)
        self.assertFalse((self.dir/"SOL_USDT_4h.csv").exists())

    def test_gaps_rejected_without_creating_file(self):
        with self.assertRaisesRegex(ValueError,"gap"):
            export_coin("BTC",FakeUniverse(sample_rows(gap=True)),self.dir)
        self.assertFalse((self.dir/"BTC_USDT_4h.csv").exists())

    def test_explicit_overwrite_required(self):
        export_coin("BTC",FakeUniverse(sample_rows()),self.dir)
        with self.assertRaises(FileExistsError):
            export_coin("BTC",FakeUniverse(sample_rows()),self.dir)
        result=export_coin("BTC",FakeUniverse(sample_rows()),self.dir,overwrite=True)
        self.assertEqual(result["rows"],120)

    def test_rejects_invalid_symbol_and_limits(self):
        with self.assertRaises(ValueError):
            export_coin("../SOL",FakeUniverse(sample_rows()),self.dir)
        with self.assertRaises(ValueError):
            export_coin("BTC",FakeUniverse(sample_rows()),self.dir,limit=1001)


if __name__=="__main__":
    unittest.main()
