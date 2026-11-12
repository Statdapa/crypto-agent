"""Offline deterministic multi-coin demo tests: no exchange or network calls."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from multi_portfolio import MultiPaperPortfolio, PortfolioLimits
from multi_paper_trade import (
    format_fill, format_multi_status, format_multi_positions, rank_candidates, safe_candidate,
    scan_cycle, send_pending,
)
from trading_engine import D


class MultiPortfolioTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "multi.db")
        self.engine = MultiPaperPortfolio(self.path)
        self.t = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=6)

    def entry(self,symbol="BTC/USDT",price="100",when=None):
        when = when or self.t
        mark = self.engine.mark(symbol,price,when,now=when)
        self.assertEqual(mark["action"],"HOLD")
        return self.engine.on_signal(symbol,"BUY",when,now=when)

    def test_two_coins_share_one_cash_wallet(self):
        first = self.entry("BTC/USDT","100")
        second = self.entry("SOL/USDT","50")
        self.assertEqual(first["action"],"BUY")
        self.assertEqual(second["action"],"BUY")
        info = self.engine.status()
        self.assertEqual(info["open_positions"],2)
        self.assertLess(D(info["cash"]),D("701"))
        self.assertGreater(D(info["cash"]),D("695"))
        self.assertLessEqual(D(info["total_marked_exposure"]),D("450.1"))
        self.assertLess(D(info["equity"]),D("1000"))
        restarted = MultiPaperPortfolio(self.path)
        self.assertEqual(restarted.status()["open_positions"],2)

    def test_max_positions_three(self):
        self.entry("BTC/USDT","100")
        self.entry("SOL/USDT","50")
        self.entry("ETH/USDT","200")
        self.assertEqual(self.entry("ADA/USDT","1")["reason"],"max_open_positions")
        self.assertEqual(self.engine.status()["open_positions"],3)

    def test_takeprofit_and_stoploss_at_quote_marks(self):
        self.entry("BTC/USDT","100")
        self.entry("SOL/USDT","50")
        later=self.t+timedelta(minutes=1)
        self.assertEqual(self.engine.mark("BTC/USDT","106",later,now=later)["reason"],
                         "take_profit_triggered")
        self.assertEqual(self.engine.mark("SOL/USDT","45",later,now=later)["reason"],
                         "stop_triggered")
        self.assertEqual(self.engine.status()["open_positions"],0)
        fills=self.engine.fills()
        self.assertEqual(len(fills),4)
        self.assertGreater(D(fills[1]["pnl"]),0)
        self.assertLess(D(fills[0]["pnl"]),0)
        self.assertIn("Realized PnL",format_fill(fills[0],self.engine))
        self.assertIn("Entry price:",format_fill(fills[0],self.engine))
        self.assertIn("Original SL:",format_fill(fills[0],self.engine))
        self.assertIn("Return on invested notional:",format_fill(fills[0],self.engine))
        self.assertEqual(self.engine.stats()["closed_trades"],2)

    def test_duplicate_signal_safe(self):
        self.entry()
        self.assertEqual(self.engine.on_signal("BTC/USDT","BUY",self.t,now=self.t)["action"],
                         "DUPLICATE")
        self.assertEqual(self.engine.status()["open_positions"],1)

    def test_cooldown_after_exit(self):
        self.entry()
        nextmark=self.t+timedelta(minutes=1)
        self.engine.mark("BTC/USDT","108",nextmark,now=nextmark)
        self.assertEqual(
            self.engine.on_signal("BTC/USDT","BUY",nextmark,now=nextmark)["reason"],
            "symbol_cooldown"
        )

    def test_halt_exits_and_blocks_new_positions(self):
        self.entry("BTC/USDT","100")
        self.engine.halt()
        later=self.t+timedelta(minutes=1)
        self.assertEqual(
            self.engine.mark("BTC/USDT","100",later,now=later)["reason"],
            "risk_or_manual_kill"
        )
        self.assertEqual(self.entry("SOL/USDT","50",later)["reason"],"kill_switch")
        self.assertTrue(self.engine.status()["halted"])

    def test_mark_rejects_stale_and_bad_symbols(self):
        old=self.t-timedelta(hours=2)
        self.assertEqual(
            self.engine.mark("BTC/USDT",100,old,now=self.t)["reason"],
            "stale_or_future_quote"
        )
        with self.assertRaises(ValueError):
            self.engine.mark("BTCUSDT",100,self.t,now=self.t)
        self.assertEqual(self.engine.status()["open_positions"],0)

    def test_risk_config_immutable(self):
        self.entry()
        with self.assertRaisesRegex(ValueError,"risk profile"):
            MultiPaperPortfolio(self.path,PortfolioLimits(max_positions=9))

    def test_exactly_once_alert_ack_per_fill(self):
        self.entry()
        with patch.dict(os.environ,{"TELEGRAM_TOKEN":"fake","TELEGRAM_CHAT_ID":"99"}):
            self.assertEqual(send_pending(self.engine,sender=lambda *_:False)["sent"],0)
            self.assertEqual(len(self.engine.pending_alerts()),1)
            payload=[]
            self.assertEqual(send_pending(self.engine,sender=lambda *a: payload.append(a) or True)["sent"],1)
            self.assertIn("OPEN LONG",payload[0][2])
            self.assertEqual(send_pending(self.engine,sender=lambda *_:True)["sent"],0)

    def test_prevents_historic_stale_signal(self):
        self.engine.mark("BTC/USDT","100",self.t,now=self.t)
        too_old=self.t-timedelta(hours=2)
        self.assertEqual(
            self.engine.on_signal("BTC/USDT","BUY",too_old,now=self.t)["reason"],
            "stale_or_future_signal"
        )

    def test_buy_requires_verified_quote(self):
        self.assertEqual(self.engine.on_signal(
            "BTC/USDT","BUY",self.t,now=self.t)["reason"],"no_fresh_verified_mark")

    def test_status_message_contains_all_positions(self):
        self.entry("BTC/USDT","100")
        self.entry("SOL/USDT","50")
        msg=format_multi_status(self.engine)
        self.assertIn("BTC/USDT",msg)
        self.assertIn("SOL/USDT",msg)
        self.assertIn("Equity",msg)
        self.assertIn("PnL",msg)

    def test_position_command_shows_shared_ledger_active_positions(self):
        self.entry("BTC/USDT","100")
        self.entry("SOL/USDT","50")
        msg=format_multi_positions(self.engine)
        self.assertIn("Active: 2",msg)
        self.assertIn("OPEN LONG — BTC/USDT",msg)
        self.assertIn("OPEN LONG — SOL/USDT",msg)
        self.assertIn("Unrealized PnL",msg)
        self.assertIn("Stop loss",msg)
        self.assertIn("Take profit",msg)

    def test_position_command_empty_portfolio_is_explicit(self):
        msg=format_multi_positions(self.engine)
        self.assertIn("Active: 0",msg)
        self.assertIn("No open positions",msg)


class UniverseSelectionTests(unittest.TestCase):
    def test_filters_stablecoins_leverage_and_thin_volumes(self):
        scan={"top_movers":[
            {"coin":"USDC","quote_volume":99999999,"change_24h":25},
            {"coin":"BTCDOWN","quote_volume":99999999,"change_24h":10},
            {"coin":"XYZ","quote_volume":100,"change_24h":20},
            {"coin":"SOL","quote_volume":9999999,"change_24h":12},
            {"coin":"ADA","quote_volume":9999999,"change_24h":2},
        ]}
        self.assertEqual(rank_candidates(scan),["SOL","ADA"])
        self.assertFalse(safe_candidate("USDC"))
        self.assertFalse(safe_candidate("ETHUP"))

    def test_scan_cycle_does_not_buy_without_valid_strategy_signal(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        e=MultiPaperPortfolio(str(Path(temp.name)/"multi.db"))
        class Dummy:
            def scan(self,**kwargs):
                return {"top_movers":[{"coin":"SOL","quote_volume":5000000,
                                       "change_24h":6}]}
        def quote(base):
            return {"coin":base,"pair":base+"USDT","price":100,
                    "fetched_at":datetime.now(timezone.utc).isoformat()}
        output=scan_cycle(e,Dummy(),quote_override=quote,
                          signal_override=lambda coin,q:{"signal":"HOLD"})
        self.assertEqual(output["decisions"][0]["action"],"HOLD")
        self.assertEqual(e.status()["open_positions"],0)

    def test_scan_cycle_buy_signal_creates_only_one_position_per_cycle(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        e=MultiPaperPortfolio(str(Path(temp.name)/"multi.db"))
        class Dummy:
            def scan(self,**kwargs):
                return {"top_movers":[{"coin":"SOL","quote_volume":5000000,
                                       "change_24h":6}]}
        def quote(base):
            return {"coin":base,"pair":base+"USDT","price":100,
                    "fetched_at":datetime.now(timezone.utc).isoformat()}
        output=scan_cycle(e,Dummy(),quote_override=quote,
                          signal_override=lambda coin,q:{"signal":"BUY"})
        self.assertEqual(output["decisions"][0]["action"],"BUY")
        self.assertEqual(e.status()["open_positions"],1)


if __name__ == "__main__":
    unittest.main()
