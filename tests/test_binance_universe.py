"""Binance-wide spot universe, symbol validation and scan tests (offline)."""
import unittest
from unittest.mock import patch

from binance_universe import BinanceSpotUniverse, MarketUnavailable, spot_usdt_catalog


def entry(base, quote="USDT", status="TRADING", spot=True):
    return {
        "symbol": base+quote, "baseAsset": base, "quoteAsset": quote,
        "status": status, "isSpotTradingAllowed": spot,
    }


def ticker(base, price="100", volume="5000000", change="6.0"):
    return {
        "symbol":base+"USDT", "lastPrice":price, "quoteVolume":volume,
        "priceChangePercent":change, "highPrice":"110", "lowPrice":"90",
    }


class FakeUniverse(BinanceSpotUniverse):
    def __init__(self, catalog_data, ticker_data=None, candles_data=None):
        super().__init__(ttl_seconds=900)
        self.catalog_data=catalog_data
        self.ticker_data=ticker_data if ticker_data is not None else []
        self.candles_data=candles_data if candles_data is not None else []
        self.calls=[]

    def _get(self, endpoint, params=None):
        self.calls.append((endpoint,params))
        if endpoint.endswith("exchangeInfo"):
            return self.catalog_data
        if endpoint.endswith("klines"):
            return self.candles_data
        if endpoint.endswith("ticker/24hr"):
            return self.ticker_data
        raise AssertionError("Unexpected Binance path")


class BinanceUniverseTests(unittest.TestCase):
    def data(self):
        return {"symbols":[entry("BTC"),entry("SOL"),entry("NEW"),
                           entry("DELISTED",status="BREAK"),
                           entry("NOTSPOT",spot=False),
                           entry("EUR",quote="EUR"),
                           entry("BAD")]}

    def test_active_spot_usdt_only(self):
        markets=spot_usdt_catalog(self.data())
        self.assertEqual(set(markets),{"BTC","SOL","NEW","BAD"})
        self.assertNotIn("DELISTED",markets)

    def test_catalog_fails_closed(self):
        for response in (None,{},{"symbols":[]},{"symbols":"wrong"}):
            with self.subTest(response=response):
                with self.assertRaises(MarketUnavailable):
                    spot_usdt_catalog(response)

    def test_any_new_pair_discovered_without_static_mapping(self):
        universe=FakeUniverse(self.data(),ticker("NEW"))
        self.assertEqual(universe.require("new"),"NEWUSDT")
        self.assertEqual(universe.ticker_24h("NEW")["pair"],"NEWUSDT")
        self.assertIsNone(universe.ticker_24h("NEW")["change_7d"])
        self.assertEqual(len([p for p,_ in universe.calls if p.endswith("exchangeInfo")]),1)

    def test_wrong_pair_response_rejected(self):
        universe=FakeUniverse(self.data(),ticker("BTC"))
        with self.assertRaisesRegex(MarketUnavailable,"mismatch"):
            universe.ticker_24h("SOL")

    def test_nontrading_pair_denied(self):
        universe=FakeUniverse(self.data())
        with self.assertRaises(MarketUnavailable):
            universe.require("DELISTED")
        with self.assertRaises(MarketUnavailable):
            universe.require("NOTSPOT")
        with self.assertRaises(MarketUnavailable):
            universe.require("")

    def test_all_market_scan_liquidity_volume_and_movers(self):
        prices=[ticker("BTC",change="-3",volume="80000000"),
                ticker("SOL",change="12",volume="2000000"),
                ticker("NEW",change="9",volume="1500000"),
                ticker("BAD",change="150",volume="10"),
                ticker("DELISTED",change="999",volume="900000000")]
        universe=FakeUniverse(self.data(),prices)
        outcome=universe.scan(min_quote_volume=1_000_000,top=3)
        self.assertEqual(outcome["total_spot_usdt_pairs"],4)
        self.assertEqual(outcome["liquid_pairs_count"],3)
        self.assertEqual([x["coin"] for x in outcome["top_movers"]],
                         ["SOL","NEW","BTC"])

    def test_bad_tickers_not_in_scanner(self):
        rows=[ticker("SOL",price="NaN"),ticker("BTC",change="Infinity"),
              ticker("NEW",price="3",volume="1000001")]
        universe=FakeUniverse(self.data(),rows)
        scan=universe.scan(top=5)
        self.assertEqual([x["coin"] for x in scan["top_movers"]],["NEW"])

    def test_candles_are_complete_and_ordered(self):
        import time
        base=int(time.time()*1000)-50*4*3600*1000
        width=4*3600*1000
        rows=[[base+i*width,"100","110","90","105","123",base+(i+1)*width-1]
              for i in range(50)]
        universe=FakeUniverse(self.data(),candles_data=rows)
        outcome=universe.candles("NEW",limit=180)
        self.assertEqual(len(outcome),50)
        self.assertEqual(outcome[0][4],105.0)

    def test_malformed_candles_rejected(self):
        import time
        base=int(time.time()*1000)-50*4*3600*1000
        width=4*3600*1000
        rows=[[base+i*width,"100","110","90","105","123",base+(i+1)*width-1]
              for i in range(50)]
        rows[25][2]="80"
        universe=FakeUniverse(self.data(),candles_data=rows)
        with self.assertRaisesRegex(MarketUnavailable,"OHLC"):
            universe.candles("NEW",limit=180)


if __name__=="__main__":
    unittest.main()
