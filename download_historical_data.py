"""Paginated Binance SPOT/USDT 4h OHLCV history, public read-only endpoint.

Download thousands of *completed* candles, aligned and gap-checked. Never
fabricate prices, reuse stale API responses, or overwrite without permission.
Creates the same timestamp,open,high,low,close schema as short downloader.
"""
from __future__ import annotations

import argparse
import csv
import os
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from binance_universe import BinanceSpotUniverse, BASE_URL

BAR_MS = 4 * 60 * 60 * 1000
COLUMNS = ("timestamp", "open", "high", "low", "close")


def checked_bar(row, latest_open):
    """Validate a complete Binance kline. Preserve exchange price precision."""
    if not isinstance(row, list) or len(row) < 7:
        raise ValueError("Malformed Binance 4h kline")
    start, end = int(row[0]), int(row[6])
    if start % BAR_MS or end != start + BAR_MS - 1:
        raise ValueError("Incorrect 4h kline UTC boundaries")
    if start > latest_open:
        raise ValueError("Current/incomplete/future candle returned")
    try:
        o,h,l,c = [Decimal(str(row[i])) for i in (1,2,3,4)]
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid OHLC price") from exc
    if not all(x.is_finite() and x > 0 for x in (o,h,l,c)):
        raise ValueError("Non-positive/nonfinite OHLC price")
    if l > min(o,c) or h < max(o,c,l):
        raise ValueError("OHLC high/low inconsistency")
    return {
        "open_time":start,
        "timestamp":datetime.fromtimestamp(start / 1000, timezone.utc).isoformat(),
        "open":str(o),"high":str(h),"low":str(l),"close":str(c),
    }


def fetch_klines(session, pair, end_time, limit, *, timeout=15, pause=0.1,
                 sleeper=time.sleep):
    """A small, bounded retry policy for throttling/transient failures."""
    for attempt in range(4):
        try:
            response=session.get(
                BASE_URL+"/api/v3/klines",
                params={"symbol":pair,"interval":"4h",
                        "limit":limit,"endTime":end_time},
                timeout=timeout,
            )
            if response.status_code in (418,429,500,502,503,504):
                if attempt == 3:
                    response.raise_for_status()
                retry_after=response.headers.get("Retry-After","")
                try:
                    delay=min(max(float(retry_after),0),10)
                except (ValueError,TypeError):
                    delay=0
                sleeper(max(delay,0.5*(2**attempt)))
                continue
            response.raise_for_status()
            result=response.json()
            if not isinstance(result,list):
                raise ValueError("Binance returned invalid kline payload")
            if pause>0:
                sleeper(pause)
            return result
        except (requests.Timeout,requests.ConnectionError):
            if attempt == 3:
                raise
            sleeper(0.5*(2**attempt))
    raise RuntimeError("Failed to fetch Binance OHLCV after retries")


def collect_history(base, *, bars=6000, universe=None, session=None,
                    now_ms=None, pause=0.1, sleeper=time.sleep):
    """Backward pagination ensures final *bars* include most recent closed bar.

    Exact pair checked via active Binance spot exchangeInfo. No future candle.
    A newly listed coin can legitimately return fewer than requested.
    """
    if not isinstance(bars,int) or isinstance(bars,bool) or not 300 <= bars <= 20000:
        raise ValueError("--bars must be between 300 and 20000")
    universe=universe or BinanceSpotUniverse()
    pair=universe.require(base)
    if pair != str(base).upper()+"USDT":
        raise ValueError("Unexpected Binance USDT pair")
    session=session or requests.Session()
    now_ms=int(time.time()*1000) if now_ms is None else int(now_ms)
    latest_open=(now_ms//BAR_MS-1)*BAR_MS
    cursor=latest_open+BAR_MS-1
    rows={}
    while len(rows)<bars:
        requested=min(1000,bars-len(rows))
        raw=fetch_klines(session,pair,cursor,requested,pause=pause,sleeper=sleeper)
        if not raw:
            break
        parsed=[checked_bar(x,latest_open) for x in raw]
        starts=[r["open_time"] for r in parsed]
        if len(set(starts))!=len(starts) or starts!=sorted(starts):
            raise ValueError("Duplicate/unordered klines in Binance page")
        if starts[-1]>cursor:
            raise ValueError("Exchange returned bar newer than cursor")
        for item in parsed:
            stamp=item["open_time"]
            if stamp in rows:
                raise ValueError("Overlapping/duplicate candles across pages")
            rows[stamp]=item
        # request endTime must go strictly backwards to avoid overlap.
        next_cursor=starts[0]-1
        if next_cursor>=cursor:
            raise ValueError("Pagination failed to progress")
        cursor=next_cursor
        if len(parsed)<requested:
            # Exchange has exhausted history for this pair.
            break
    if len(rows)<300:
        raise ValueError("Fewer than 300 historical completed 4h candles")
    chronological=[rows[key] for key in sorted(rows)]
    if len(chronological)>bars:
        raise ValueError("Collected unexpected extra rows")
    for old,new in zip(chronological,chronological[1:]):
        if new["open_time"]-old["open_time"]!=BAR_MS:
            raise ValueError(
                "Missing 4h candle / discontinuity at "+old["timestamp"]+
                "; refusing to fabricate data"
            )
    return pair,chronological


def save_history(base, *, bars=6000, output_dir="data/raw", overwrite=False,
                 universe=None, session=None, now_ms=None, pause=0.1,
                 sleeper=time.sleep):
    out=Path(output_dir)
    path=out/(str(base).upper()+"_USDT_4h.csv")
    if path.exists() and not overwrite:
        raise FileExistsError(
            str(path)+" already exists; use --overwrite to replace intentionally"
        )
    pair,rows=collect_history(base,bars=bars,universe=universe,
                              session=session,now_ms=now_ms,
                              pause=pause,sleeper=sleeper)
    out.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="",
            prefix="."+str(base).upper()+"_",suffix=".tmp",
            dir=out,delete=False
        ) as f:
            temporary=Path(f.name)
            writer=csv.DictWriter(f,fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows({k:row[k] for k in COLUMNS} for row in rows)
        if path.exists() and not overwrite:
            raise FileExistsError("Output appeared during collection: "+str(path))
        os.replace(temporary,path)
        temporary=None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {
        "pair":pair,
        "path":str(path),
        "requested_bars":bars,
        "downloaded_bars":len(rows),
        "start_utc":rows[0]["timestamp"],
        "end_utc":rows[-1]["timestamp"],
        "coverage_warning":(
            "Shorter than requested; coin may have listed later"
            if len(rows)<bars else None
        ),
    }


def main(argv=None):
    parser=argparse.ArgumentParser(
        description="Historical Binance spot 4h data; PUBLIC READ ONLY"
    )
    parser.add_argument("coins",nargs="+",help="Base coins, e.g. BTC SOL")
    parser.add_argument("--bars",type=int,default=6000)
    parser.add_argument("--output-dir",default="data/raw")
    parser.add_argument("--overwrite",action="store_true")
    args=parser.parse_args(argv)
    try:
        for base in dict.fromkeys(c.upper().strip() for c in args.coins):
            result=save_history(base,bars=args.bars,
                                output_dir=args.output_dir,
                                overwrite=args.overwrite)
            print(result)
    except (ValueError,requests.RequestException,OSError) as exc:
        parser.error(str(exc))
    print("Completed public candle history download. No exchange orders.")


if __name__=="__main__":
    main()
