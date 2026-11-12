"""Market-data symbol and cross-source checks run without live credentials."""
import unittest

from market_data_guard import (
    MarketDataValidationError, positive_price, validate_binance_reference,
    validate_coingecko_asset, validate_snapshot_identity,
)


class MarketPriceGuardTests(unittest.TestCase):
    def quote(self, symbol="sol", identifier="solana", usd=130):
        return {"id": identifier, "symbol": symbol,
                "market_data": {"current_price": {"usd": usd}}}

    def test_valid_sol_quote_and_reference(self):
        sol = validate_coingecko_asset(self.quote(), "SOL", "solana")
        self.assertEqual(str(sol), "130")
        self.assertEqual(str(validate_binance_reference(
            {"symbol": "SOLUSDT", "price": "131"}, "SOL", sol
        )), "131")

    def test_btc_data_masquerading_as_sol_rejected(self):
        with self.assertRaisesRegex(MarketDataValidationError, "asset mismatch"):
            validate_coingecko_asset(
                self.quote(symbol="btc", identifier="bitcoin", usd=83000),
                "SOL", "solana")

    def test_sol_label_with_btc_price_rejected_by_reference(self):
        with self.assertRaisesRegex(MarketDataValidationError, "price mismatch"):
            validate_binance_reference(
                {"symbol": "SOLUSDT", "price": "135"}, "SOL", 83000)

    def test_wrong_market_pair_rejected(self):
        with self.assertRaisesRegex(MarketDataValidationError, "pair mismatch"):
            validate_binance_reference(
                {"symbol": "BTCUSDT", "price": 83000}, "SOL", 130)

    def test_missing_identity_rejected(self):
        for quote in (
            {"market_data": {"current_price": {"usd": 130}}},
            self.quote(symbol="btc"),
            self.quote(identifier="bitcoin"),
        ):
            with self.subTest(quote=quote):
                with self.assertRaises(MarketDataValidationError):
                    validate_coingecko_asset(quote, "SOL", "solana")

    def test_bad_price_rejected(self):
        for value in ("NaN", "Infinity", "0", -5, None, True):
            with self.subTest(value=value):
                with self.assertRaises(MarketDataValidationError):
                    positive_price(value, "test")

    def test_snapshot_for_different_coin_rejected(self):
        with self.assertRaisesRegex(MarketDataValidationError, "Snapshot rejected"):
            validate_snapshot_identity("SOL", {"coin": "BTC", "price": 83000})
        validate_snapshot_identity("SOL", {"coin": "SOL", "price": 135})

    def test_reference_margin(self):
        validate_binance_reference({"symbol": "BTCUSDT", "price": "80000"},
                                   "BTC", "80500")
        with self.assertRaises(MarketDataValidationError):
            validate_binance_reference({"symbol": "BTCUSDT", "price": "80000"},
                                       "BTC", "100000")


if __name__ == "__main__":
    unittest.main()
