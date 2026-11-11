"""Explicit paper-only CLI. No exchange credentials or live order function."""
import argparse
import json
import os
import time
from pathlib import Path

from trading_engine import PaperTrader, RiskLimits, load_latest_agent_signal
from paper_demo import cycle, notify_pending


def parser():
    p = argparse.ArgumentParser(description="Fail-closed SPOT paper trading (never sends orders)")
    p.add_argument("command", choices=("tick", "status", "fills", "stats", "watch", "demo-watch", "notify", "halt", "resume"))
    p.add_argument("--source-db", default=os.getenv("DB_PATH", "crypto_intelligence_agent.db"))
    p.add_argument("--ledger", default=os.getenv("PAPER_DB_PATH", "data/paper_trading.db"))
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--poll-seconds", type=int, default=60)
    p.add_argument("--max-cycles", type=int, default=0, help="0 = unlimited watch cycles")
    p.add_argument("--ack-resume-risk", action="store_true",
                   help="Required for manual resume after reviewing the risk halt")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if Path(args.ledger).resolve() == Path(args.source_db).resolve():
        raise SystemExit("The paper ledger must never overwrite the agent signal DB.")
    limits = RiskLimits(allowed_symbol=args.symbol)
    engine = PaperTrader(args.ledger, limits)
    if args.command == "status":
        result = engine.status()
    elif args.command == "fills":
        result = engine.fills()
    elif args.command == "stats":
        result = engine.stats()
    elif args.command == "notify":
        result = notify_pending(engine)
    elif args.command == "demo-watch":
        if args.poll_seconds < 10 or args.max_cycles < 0:
            raise SystemExit("demo-watch requires poll-seconds >=10 and max-cycles >=0")
        cycles = 0
        print("DEMO ONLY — verified quotes, virtual SL/TP and Telegram fill alerts.", flush=True)
        try:
            while args.max_cycles == 0 or cycles < args.max_cycles:
                result = cycle(engine, args.source_db, args.symbol)
                print(json.dumps(result, default=str), flush=True)
                cycles += 1
                if args.max_cycles == 0 or cycles < args.max_cycles:
                    time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            print("Demo stopped.", flush=True)
        return 0
    elif args.command == "watch":
        if args.poll_seconds < 10 or args.max_cycles < 0:
            raise SystemExit("watch requires poll-seconds >=10 and max-cycles >=0")
        cycles = 0
        print("PAPER_ONLY watch; press Ctrl+C to exit. No real exchange orders.", flush=True)
        try:
            while args.max_cycles == 0 or cycles < args.max_cycles:
                signal = load_latest_agent_signal(args.source_db, args.symbol)
                result = engine.tick(signal) if signal else {"action": "SKIP", "reason": "no_saved_signal"}
                print(json.dumps(result, default=str), flush=True)
                cycles += 1
                if args.max_cycles == 0 or cycles < args.max_cycles:
                    time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            print("Paper watch stopped.", flush=True)
        return 0
    elif args.command == "halt":
        engine.halt()
        result = {"mode": "PAPER_ONLY", "halted": True}
    elif args.command == "resume":
        if not args.ack_resume_risk:
            raise SystemExit("Manual resume requires --ack-resume-risk")
        engine.resume()
        result = {"mode": "PAPER_ONLY", "halted": False}
    else:
        signal = load_latest_agent_signal(args.source_db, args.symbol)
        if signal is None:
            result = {"action": "SKIP", "reason": "no_saved_signal"}
        else:
            result = engine.tick(signal)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
