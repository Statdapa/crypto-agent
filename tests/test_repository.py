import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from data_repository import latest_signals, weekly_summary, recent_alerts

class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=str(Path(self.tmp.name)/"test.db")

    def test_missing_data_never_fabricated(self):
        self.assertEqual(latest_signals(self.path),[])
        self.assertEqual(recent_alerts(self.path),[])
        self.assertIsNone(weekly_summary(self.path,"BTC"))
        self.assertFalse(os.path.exists(self.path))

    def test_modern_schema(self):
        now=datetime.now(timezone.utc)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE market_snapshots (id INTEGER PRIMARY KEY, coin TEXT, price REAL, change_24h REAL, rsi REAL, signal TEXT, signal_strength INTEGER, created_at TEXT)")
            db.executemany("INSERT INTO market_snapshots (coin,price,change_24h,rsi,signal,signal_strength,created_at) VALUES (?,?,?,?,?,?,?)",[
              ("BTC",100,1,28,"BUY",70,(now-timedelta(days=2)).isoformat()),
              ("BTC",110,2,60,"HOLD",50,(now-timedelta(days=1)).isoformat()),
              ("ETH",200,1,40,"SELL",75,(now-timedelta(days=1)).isoformat())])
        signals=latest_signals(self.path)
        self.assertEqual([(x["coin"],x["signal"]) for x in signals],[("BTC","HOLD"),("ETH","SELL")])
        report=weekly_summary(self.path,"BTC",now=now)
        self.assertEqual(report["price"]["pct_change"],10.0)
        self.assertEqual(report["signals"]["buy_count"],1)

    def test_legacy_schema(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE price_history (id INTEGER PRIMARY KEY, coin TEXT, price REAL, change_24h REAL, rsi REAL, signal TEXT, confidence REAL, recorded_at TEXT)")
            db.execute("INSERT INTO price_history (coin,price,change_24h,rsi,signal,confidence,recorded_at) VALUES (?,?,?,?,?,?,?)",
                       ("BTC",100,1,20,"BUY",70,datetime.now(timezone.utc).isoformat()))
        self.assertEqual(latest_signals(self.path)[0]["source"],"price_history")

class ApiTests(unittest.TestCase):
    def setUp(self):
        import app
        app.DASHBOARD_TOKEN="test-token"
        self.app=app
        self.client=app.app.test_client()

    def test_public_health_redacted(self):
        r=self.client.get("/api/health")
        self.assertEqual(r.status_code,200)
        self.assertNotIn("wallet_preview",r.json)

    def test_private_routes_require_header(self):
        for endpoint in ["/api/signals","/api/scan/history","/api/config","/api/okx/balance"]:
            self.assertEqual(self.client.get(endpoint).status_code,401)
            self.assertEqual(self.client.get(endpoint+"?token=test-token").status_code,401)

    def test_fail_closed(self):
        self.app.DASHBOARD_TOKEN=""
        self.assertEqual(self.client.get("/api/signals").status_code,503)

    def test_no_wildcard_cors(self):
        r=self.client.get("/api/health",headers={"Origin":"https://attacker.test"})
        self.assertNotIn("Access-Control-Allow-Origin",r.headers)

if __name__ == "__main__":
    unittest.main()
