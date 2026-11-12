"""Fail-closed market-data identity and independent price checks.

A quote must refer to the exact requested instrument before it can be used for
analysis, database snapshots, alerts, or paper trading. No hardcoded prices.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


class MarketDataValidationError(ValueError):
    """Untrusted, inconsistent, or mismatched market data."""


def positive_price(value, label: str) -> Decimal:
    if isinstance(value, bool):
        raise MarketDataValidationError(f"{label}: boolean is not a price")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketDataValidationError(f"{label}: invalid price") from exc
    if not price.is_finite() or price <= 0:
        raise MarketDataValidationError(f"{label}: price must be positive and finite")
    return price


def validate_coingecko_asset(data: dict, requested_symbol: str, expected_id: str) -> Decimal:
    """Verify response id, ticker symbol, and USD price for a /coins/{id} call."""
    if not isinstance(data, dict):
        raise MarketDataValidationError("CoinGecko response is not an object")
    identifier = str(data.get("id") or "").lower()
    symbol = str(data.get("symbol") or "").upper()
    requested = requested_symbol.upper()
    if identifier != expected_id.lower() or symbol != requested:
        raise MarketDataValidationError(
            f"CoinGecko asset mismatch: requested {requested}/{expected_id}, "
            f"received {symbol or 'UNKNOWN'}/{identifier or 'UNKNOWN'}"
        )
    market_data = data.get("market_data")
    if not isinstance(market_data, dict):
        raise MarketDataValidationError(f"{requested}: missing market_data")
    current = market_data.get("current_price")
    if not isinstance(current, dict):
        raise MarketDataValidationError(f"{requested}: missing current_price")
    return positive_price(current.get("usd"), f"{requested} USD")


def validate_binance_reference(
    data: dict, requested_symbol: str, coingecko_price,
    max_deviation: str = "0.08",
) -> Decimal:
    """Reject wrong pair or >8% deviation against independent USDT spot quote.

    USDT and USD are different units; the tolerance allows ordinary spread and
    stablecoin differences, but deliberately fails closed on major mismatches.
    """
    if not isinstance(data, dict):
        raise MarketDataValidationError("Binance response is not an object")
    pair = f"{requested_symbol.upper()}USDT"
    returned = str(data.get("symbol") or "").upper()
    if returned != pair:
        raise MarketDataValidationError(
            f"Reference pair mismatch: expected {pair}, received {returned or 'UNKNOWN'}"
        )
    reference = positive_price(data.get("price"), f"{pair} reference")
    original = positive_price(coingecko_price, f"{requested_symbol.upper()} CoinGecko")
    limit = Decimal(max_deviation)
    if abs(original-reference) / reference > limit:
        raise MarketDataValidationError(
            f"{requested_symbol.upper()} price mismatch across independent feeds: "
            f"CoinGecko={original} USD, Binance={reference} USDT; snapshot blocked"
        )
    return reference


def validate_snapshot_identity(coin_symbol: str, price_data: dict) -> None:
    """Prevent persisting a quote for one asset under another asset's ticker."""
    if not isinstance(price_data, dict):
        raise MarketDataValidationError("Missing price data")
    actual = str(price_data.get("coin") or "").upper()
    requested = coin_symbol.upper()
    if actual != requested:
        raise MarketDataValidationError(
            f"Snapshot rejected: expected {requested}, received {actual or 'UNKNOWN'}"
        )
    positive_price(price_data.get("price"), f"{requested} snapshot")
