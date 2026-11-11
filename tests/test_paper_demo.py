"""Demo monitoring and Telegram accounting: offline regression checks."""
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from paper_demo import cycle, format_fill, format_status, format_trades, notify_pending
from trading_engine import D, MarketSignal, PaperTrader


class DemoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.t = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
        self.ledger = str(Path(temp.name) / "paper.db")
        self.source = str(Path(temp.name) / "agent.db")
        self.engine = PaperTrader(self.ledger)
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute(
                "CREATE TABLE market_snapshots "
                "(id INTEGER PRIMARY KEY, coin TEXT, price REAL, signal TEXT, created_at TEXT)"
            )
            db.execute(
                "INSERT INTO market_snapshots(coin,price,signal,created_at) VALUES(?,?,?,?)",
                ("BTC", 100, "BUY", self.t.isoformat()),
            )

    def q(self, price="100", minutes=1):
        return D(price), self.t+timedelta(minutes=minutes)

    def test_open_then_tp_without_new_signal(self):
        with patch.dict(os.environ, {"TELEGRAM_TOKEN":"","TELEGRAM_CHAT_ID":""}):
            first=cycle(self.engine,self.source,"BTC/USDT",lambda _:self.q())
            self.assertEqual(first["signal_action"],"BUY")
            self.assertEqual(self.engine.status()["fill_count"],1)
            second=cycle(self.engine,self.source,"BTC/USDT",lambda _:self.q("107",2))
        self.assertEqual(second["mark_action"],"SELL")
        self.assertEqual(second["mark_reason"],"take_profit_triggered")
        self.assertEqual(second["signal_action"],"DUPLICATE")
        self.assertEqual(self.engine.stats()["closed_trades"],1)
        self.assertIn("Realized net PnL",format_fill(self.engine.fills()[0],self.engine))

    def test_stop_loss_on_quote_even_without_new_strategy(self):
        self.engine.tick(MarketSignal("BTC/USDT",D(100),"BUY",self.t),now=self.t)
        result=self.engine.observe_price("BTC/USDT",D(95),self.t+timedelta(minutes=1))
        self.assertEqual(result["action"],"SELL")
        self.assertEqual(result["reason"],"stop_triggered")
        self.assertLess(D(self.engine.fills()[0]["realized_pnl"]),0)

    def test_bad_quote_cannot_trade(self):
        def invalid(_):
            raise ValueError("reference mismatch")
        with patch.dict(os.environ,{"TELEGRAM_TOKEN":"","TELEGRAM_CHAT_ID":""}):
            r=cycle(self.engine,self.source,"BTC/USDT",invalid)
        self.assertEqual(r["reason"],"quote_unavailable")
        self.assertEqual(self.engine.status()["fill_count"],0)

    def test_telegram_ack_after_success_only(self):
        self.engine.tick(MarketSignal("BTC/USDT",D(100),"BUY",self.t),now=self.t)
        with patch.dict(os.environ,{"TELEGRAM_TOKEN":"secret","TELEGRAM_CHAT_ID":"99"}):
            fail=notify_pending(self.engine,lambda *args:False)
            self.assertEqual(fail["sent"],0)
            self.assertEqual(len(self.engine.pending_notifications()),1)
            sent=[]
            result=notify_pending(self.engine,lambda *args:sent.append(args) or True)
            self.assertEqual(result["sent"],1)
            self.assertEqual(result["pending"],0)
            self.assertIn("OPEN LONG",sent[0][2])
            self.assertEqual(notify_pending(self.engine,lambda *args:True)["sent"],0)

    def test_position_and_pnl_telegram_format(self):
        self.engine.tick(MarketSignal("BTC/USDT",D(100),"BUY",self.t),now=self.t)
        self.engine.observe_price("BTC/USDT",D(102),self.t+timedelta(minutes=1))
        status=format_status(self.engine)
        self.assertIn("OPEN LONG",status)
        self.assertIn("Unrealized PnL",status)
        self.assertIn("SL:",status)
        self.assertIn("TP:",status)
        self.assertIn("BUY",format_trades(self.engine))

    def test_new_price_does_not_consume_new_strategy_signal(self):
        self.engine.tick(MarketSignal("BTC/USDT",D(100),"HOLD",self.t),now=self.t)
        self.engine.observe_price("BTC/USDT",D(101),self.t+timedelta(minutes=1))
        newer=MarketSignal("BTC/USDT",D(101),"BUY",self.t+timedelta(minutes=2))
        self.assertEqual(self.engine.tick(newer,now=self.t+timedelta(minutes=2))["action"],"BUY")

    def test_invalid_quote_symbol_rejected(self):
        self.assertEqual(self.engine.observe_price("SOL/USDT",100,self.t)["reason"],
                         "symbol_not_allowed")
        self.assertEqual(self.engine.status()["fill_count"],0)

if __name__=="__main__":
    unittest.main()
