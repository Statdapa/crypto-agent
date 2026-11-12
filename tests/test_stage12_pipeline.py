"""Offline pagination and reserve-holdout tests (no Binance required)."""
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path

import numpy as np

from backtest_baseline import Candle, read_candles
from download_historical_data import BAR_MS, collect_history,save_history
from feature_ablation import ablation_diagnostics


class FakeUniverse:
    def require(self,base):
        return base.upper()+"USDT"


class FakeResponse:
    status_code=200
    headers={}
    def __init__(self,rows):
        self.rows=rows
    def raise_for_status(self):
        pass
    def json(self):
        return self.rows


class FakeSession:
    def __init__(self,rows):
        self.rows=rows
        self.calls=[]
    def get(self,url,params,timeout):
        self.calls.append(params.copy())
        end=params["endTime"]
        rows=[r for r in self.rows if r[0]<=end]
        return FakeResponse(rows[-params["limit"]:])


def kline(start,close="100"):
    return [start,str(close),str(Decimal(close)*Decimal("1.02")),
            str(Decimal(close)*Decimal(".98")),str(close),
            "1",start+BAR_MS-1]


def bars(n=700,offset=0):
    base=datetime(2024,1,1,tzinfo=timezone.utc)
    result=[]
    for i in range(n):
        p=100+offset+i*.018+2.5*np.sin(i/7)+.6*np.cos(i/2)
        o=p+.15
        c=p-.1
        result.append(Candle(
            base+timedelta(hours=4*i),
            Decimal(str(o)),Decimal(str(max(o,c)+1)),
            Decimal(str(min(o,c)-1)),Decimal(str(c)),
        ))
    return result


class HistoryTests(unittest.TestCase):
    def make_session(self,n=2100,gap=False):
        start=BAR_MS*100000
        rows=[kline(start+i*BAR_MS) for i in range(n)]
        if gap:
            rows.pop(950)
        now_ms=start+n*BAR_MS+1000
        return FakeSession(rows),now_ms

    def test_backward_pagination_and_csv(self):
        session,now=self.make_session()
        with tempfile.TemporaryDirectory() as folder:
            result=save_history("BTC",bars=1800,output_dir=folder,
                                universe=FakeUniverse(),session=session,
                                now_ms=now,pause=0)
            self.assertEqual(result["downloaded_bars"],1800)
            self.assertEqual(len(session.calls),2)
            self.assertLess(session.calls[1]["endTime"],session.calls[0]["endTime"])
            csv=read_candles(str(Path(folder)/"BTC_USDT_4h.csv"))
            self.assertEqual(len(csv),1800)
            self.assertEqual(csv[-1].timestamp,
                datetime.fromtimestamp(session.rows[-1][0]/1000,timezone.utc))

    def test_gap_fails_closed(self):
        session,now=self.make_session(gap=True)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,"Missing 4h"):
                save_history("BTC",bars=1500,output_dir=folder,
                             universe=FakeUniverse(),session=session,
                             now_ms=now,pause=0)
            self.assertFalse((Path(folder)/"BTC_USDT_4h.csv").exists())

    def test_incomplete_bar_is_rejected(self):
        session,now=self.make_session()
        # Inject a bar after now; pagination won't request it, but bad data
        # coming from exchange must be rejected.
        session.rows[-10][0]+=BAR_MS
        with self.assertRaisesRegex(ValueError,"boundaries"):
            collect_history("BTC",bars=500,universe=FakeUniverse(),
                            session=session,now_ms=now,pause=0)

    def test_refuse_unintended_overwrite(self):
        session,now=self.make_session()
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,"SOL_USDT_4h.csv").write_text("do not overwrite")
            with self.assertRaises(FileExistsError):
                save_history("SOL",bars=1000,output_dir=folder,
                             universe=FakeUniverse(),session=session,
                             now_ms=now,pause=0)

    def test_reject_invalid_limits(self):
        session,now=self.make_session()
        with self.assertRaises(ValueError):
            collect_history("BTC",bars=21000,universe=FakeUniverse(),
                            session=session,now_ms=now,pause=0)


class AblationTests(unittest.TestCase):
    def test_holdout_not_scored_and_purge(self):
        report=ablation_diagnostics({
            "BTC/USDT":bars(700),
            "SOL/USDT":bars(700,offset=40),
        },horizon=6)
        self.assertFalse(report["held_out_test_scored"])
        self.assertFalse(report["promotion_to_paper_execution"])
        self.assertLess(report["development_label_last_index"],
                        report["holdout_first_feature_index"])
        self.assertEqual(len(report["candidate_comparison"]),6)
        for validation in report["development_folds"].values():
            self.assertEqual(len(validation),3)
            for fold in validation:
                self.assertLess(fold["train_label_last_index"],
                                fold["calibration_start_index"])
                self.assertLess(fold["calibration_label_last_index"],
                                fold["validation_start_index"])

    def test_short_data_rejected(self):
        with self.assertRaisesRegex(ValueError,"more data"):
            ablation_diagnostics({"BTC/USDT":bars(300)},horizon=6)


if __name__=="__main__":
    unittest.main()
