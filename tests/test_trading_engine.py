"""Deterministic tests: no internet, exchange API, or real-money orders."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from trading_engine import D, MarketSignal, PaperTrader, RiskLimits, load_latest_agent_signal


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "paper.db")
        self.today = datetime.now(timezone.utc).replace(microsecond=0)
        self.trader = PaperTrader(self.path)

    def sig(self, action="BUY", price="100", minute=0):
        return MarketSignal("BTC/USDT", D(price), action, self.today+timedelta(minutes=minute))

    def test_reject_nan_infinite_and_invalid_symbols(self):
        for value in ["NaN", "Infinity", "-1", "0"]:
            with self.assertRaises(ValueError):
                self.sig(price=value)
        with self.assertRaises(ValueError):
            self.trader.tick(MarketSignal("ETH/USDT", D("100"), "BUY", self.today),
                             now=self.today)
        with self.assertRaises(ValueError):
            D(True)

    def test_limits_validate_fee_slippage(self):
        for opts in (dict(fee_bps=1000),dict(slippage_bps=1000),
                     dict(max_risk_fraction="1.0"), dict(max_position_fraction="0"),
                     dict(max_entries_per_day=0)):
            with self.assertRaises(ValueError):
                RiskLimits(**opts)

    def test_buy_fee_slippage_stop_and_restart(self):
        tick = self.trader.tick(self.sig(),now=self.today)
        self.assertEqual(tick["action"],"BUY")
        self.assertEqual(self.trader.status()["fill_count"],1)
        position=self.trader.status()["position"]
        self.assertGreater(D(position["entry_price"]),D("100"))
        self.assertLessEqual(D(position["qty"])*D(position["entry_price"]),D("200"))
        self.assertLess(D(self.trader.status()["cash"]),D("800"))
        restarted=PaperTrader(self.path)
        self.assertIsNotNone(restarted.status()["position"])
        self.assertEqual(restarted.tick(self.sig(),now=self.today)["action"],"DUPLICATE")
        close=restarted.tick(self.sig("HOLD","96",5),now=self.today+timedelta(minutes=5))
        self.assertEqual(close["action"],"SELL")
        self.assertEqual(close["reason"],"stop_triggered")
        self.assertIsNone(restarted.status()["position"])
        self.assertEqual(restarted.status()["fill_count"],2)
        self.assertLess(D(restarted.fills()[0]["realized_pnl"]),0)

    def test_take_profit_without_new_buy(self):
        self.trader.tick(self.sig(),now=self.today)
        r=self.trader.tick(self.sig("HOLD","107",10),now=self.today+timedelta(minutes=10))
        self.assertEqual(r["reason"],"take_profit_triggered")
        self.assertEqual(len(self.trader.fills()),2)

    def test_duplicate_and_replay_conflicting_action(self):
        self.trader.tick(self.sig(),now=self.today)
        r=self.trader.tick(self.sig("SELL"),now=self.today)
        self.assertEqual(r["action"],"DUPLICATE")
        r=self.trader.tick(self.sig("HOLD","99",minute=-1),now=self.today)
        self.assertEqual(r["reason"],"out_of_order_snapshot")

    def test_stale_future_wrong_pair_rejected(self):
        self.assertEqual(self.trader.tick(self.sig(), now=self.today+timedelta(hours=2))["reason"],
                         "stale_signal")
        self.assertEqual(self.trader.tick(self.sig(minute=10),now=self.today)["reason"],
                         "signal_from_future")
        other=MarketSignal("ETH/USDT",D("100"),"BUY",self.today)
        self.assertEqual(self.trader.tick(other,now=self.today)["reason"],"symbol_not_allowed")
        self.assertEqual(self.trader.status()["fill_count"],0)

    def test_halt_closes_and_blocks_until_manual_resume(self):
        self.trader.tick(self.sig(),now=self.today)
        self.trader.halt()
        res=self.trader.tick(self.sig("HOLD","100",1),now=self.today+timedelta(minutes=1))
        self.assertEqual(res["reason"],"risk_or_manual_kill")
        res=self.trader.tick(self.sig("BUY","100",2),now=self.today+timedelta(minutes=2))
        self.assertEqual(res["action"],"BLOCK")
        self.assertTrue(self.trader.status()["halted"])
        self.trader.resume()
        res=self.trader.tick(self.sig("BUY","100",3),now=self.today+timedelta(minutes=3))
        self.assertEqual(res["action"],"BUY")

    def test_daily_entry_limit(self):
        limits=RiskLimits(max_entries_per_day=1)
        t=PaperTrader(str(Path(self.temp.name)/"daily.db"),limits)
        t.tick(self.sig(),now=self.today)
        t.tick(self.sig("SELL","101",1),now=self.today+timedelta(minutes=1))
        result=t.tick(self.sig("BUY","100",2),now=self.today+timedelta(minutes=2))
        self.assertEqual(result["reason"],"daily_entry_limit")

    def test_severe_gap_triggers_kill_switch(self):
        t=PaperTrader(str(Path(self.temp.name)/"gap.db"),
                      RiskLimits(max_position_fraction="0.8", max_risk_fraction="0.1",
                                 max_daily_loss_fraction="0.03"))
        t.tick(self.sig(),now=self.today)
        res=t.tick(self.sig("HOLD","40",1),now=self.today+timedelta(minutes=1))
        self.assertEqual(res["reason"],"risk_or_manual_kill")
        self.assertTrue(t.status()["halted"])

    def test_db_adapter_reads_saved_signals(self):
        dbpath=str(Path(self.temp.name)/"agent.db")
        with sqlite3.connect(dbpath) as c:
            c.execute("""CREATE TABLE market_snapshots (
                id INTEGER PRIMARY KEY, coin TEXT, price REAL, signal TEXT, created_at TEXT)""")
            c.execute("INSERT INTO market_snapshots(coin,price,signal,created_at) VALUES(?,?,?,?)",
                      ("BTC", 100.0, "HOLD", self.today.isoformat()))
        s=load_latest_agent_signal(dbpath,"BTC/USDT")
        self.assertEqual(s.action,"HOLD")
        self.assertEqual(s.price,D(100))


if __name__ == "__main__":
    unittest.main()
