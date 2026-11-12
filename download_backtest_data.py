"""Download real, completed Binance spot 4h candles as backtest-ready CSV.

Read-only public Binance API, no key/secret, and no real/paper orders.
Default 500 candles per coin (minus the still-forming candle if present).
No fabricated data or implicit overwrite.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from binance_universe import BinanceSpotUniverse


BAR_MS = 4 * 60 * 60 * 1000
BASE_RE = re.compile(r"^[A-Z0-9]{2,24}$")


def export_coin(base, universe, out_dir="data/raw", limit=500, overwrite=False):
    base = str(base).upper().strip()
    if not BASE_RE.fullmatch(base):
        raise ValueError("Expected base coin symbol, e.g. BTC or SOL")
    if not isinstance(limit, int) or not 100 <= limit <= 1000:
        raise ValueError("Limit must be between 100 and 1000")
    # Validate the exact spot pair through live exchangeInfo: no guessed symbol.
    pair = universe.require(base)
    if pair != base + "USDT":
        raise ValueError("Non-USDT market identity")
    folder = Path(out_dir)
    path = folder / (base + "_USDT_4h.csv")
    if path.exists() and not overwrite:
        raise FileExistsError(
            str(path) + " already exists. Use --overwrite only to intentionally refresh it."
        )

    rows = universe.candles(base, interval="4h", limit=limit)
    if len(rows) < 100:
        raise ValueError(base + " has fewer than 100 completed 4h candles")
    converted = []
    previous = None
    for bar in rows:
        if not isinstance(bar, (list, tuple)) or len(bar) != 5:
            raise ValueError("Malformed candle row")
        epoch = int(bar[0])
        if epoch % BAR_MS:
            raise ValueError("4h candle is not UTC-aligned")
        if previous is not None and epoch - previous != BAR_MS:
            raise ValueError("4h candle gap, duplicate, or unordered timestamp")
        previous = epoch
        when = datetime.fromtimestamp(epoch / 1000, tz=timezone.utc)
        converted.append({
            "timestamp": when.isoformat(),
            "open": str(bar[1]), "high": str(bar[2]),
            "low": str(bar[3]), "close": str(bar[4]),
        })

    folder.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".tmp",
            prefix="." + base + "_", dir=folder, delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer = csv.DictWriter(
                handle, fieldnames=("timestamp", "open", "high", "low", "close")
            )
            writer.writeheader()
            writer.writerows(converted)
        # Existing CSV is replaced only with explicit --overwrite.
        if path.exists() and not overwrite:
            raise FileExistsError("Data file appeared during download: " + str(path))
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return {
        "symbol": pair, "path": str(path), "rows": len(converted),
        "first_utc": converted[0]["timestamp"],
        "last_utc": converted[-1]["timestamp"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch verified Binance spot 4h candles for research CSV (no trading)"
    )
    parser.add_argument("coins", nargs="+", help="Base symbols, e.g. BTC SOL")
    parser.add_argument("--limit", type=int, default=500,
                        help="Requested 4h candles per coin, 100-1000 (default 500)")
    parser.add_argument("--out-dir", default="data/raw")
    parser.add_argument("--overwrite", action="store_true",
                        help="Explicitly refresh existing candle CSV files")
    args = parser.parse_args(argv)
    if not 100 <= args.limit <= 1000:
        parser.error("--limit must be between 100 and 1000")
    universe = BinanceSpotUniverse()
    for base in dict.fromkeys(x.upper() for x in args.coins):
        try:
            meta = export_coin(base, universe, args.out_dir, args.limit,
                               overwrite=args.overwrite)
        except (ValueError, FileExistsError, OSError, RuntimeError) as exc:
            parser.error(str(exc))
        except Exception as exc:
            parser.error(
                "Binance market data unavailable for " + base + ": " +
                str(exc) + ". Check connection, API access or rate limit."
            )
        print(
            meta["symbol"] + ": " + str(meta["rows"]) +
            " completed candles -> " + meta["path"]
        )
        print("UTC coverage: " + meta["first_utc"] + " to " + meta["last_utc"])
    print("Backtest CSV export complete. No exchange orders submitted.")


if __name__ == "__main__":
    main()
