"""Demo-only Telegram trade alerts and verified quote driven paper fills."""
import html
import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from trading_engine import D, MarketSignal, load_latest_agent_signal

load_dotenv()


def verified_quote(symbol):
    from crypto_intelligence_agent import get_price
    coin = symbol.split("/")[0].upper()
    data = get_price(coin)
    if not data or data.get("coin") != coin:
        raise ValueError("No verified market quote")
    at = datetime.fromisoformat(data["fetched_at"].replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - at).total_seconds()
    if at.tzinfo is None or age < -60 or age > 120:
        raise ValueError("Market quote timestamp is stale")
    return D(data["price"]), at


def money(value):
    return "USD " + format(D(value), ",.4f")


def format_fill(fill, trader):
    side = fill["side"]
    lines = [
        "<b>DEMO ONLY — " + ("OPEN LONG" if side == "BUY" else "CLOSE LONG") + "</b>",
        "Pair: " + html.escape(fill["symbol"]),
        "Quantity: " + str(fill["qty"]),
        "Fill: " + money(fill["price"]),
        "Fee: " + money(fill["fee"]),
        "Reason: " + html.escape(fill["reason"]),
        "UTC: " + html.escape(fill["at"]),
    ]
    if side == "BUY":
        pos = trader.status()["position"]
        if pos and pos["opened_at"] == fill["at"]:
            lines += ["SL: " + money(pos["stop_price"]),
                      "TP: " + money(pos["take_price"])]
        else:
            lines.append("Posisi mungkin sudah ditutup.")
    else:
        entry = trader.entry_for_close(fill["id"])
        if entry:
            lines.append("Entry: " + money(entry["price"]))
        pnl = D(fill["realized_pnl"])
        lines.append("Realized net PnL: " + format(pnl, "+,.4f") + " USDT")
        lines.append("Status: " + ("PROFIT" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"))
    lines.append("<i>Simulasi; tidak ada order exchange.</i>")
    return "\n".join(lines)


def format_status(trader):
    info = trader.status()
    stats = trader.stats()
    lines = [
        "<b>DEMO PAPER ACCOUNT</b>",
        "Cash: " + money(info["cash"]),
        "Equity: " + money(info["equity"]),
        "Kill switch: " + ("ON" if info["halted"] else "OFF"),
        "Last mark: " + (money(info["last_price"]) if info["last_price"] else "N/A"),
        "Mark UTC: " + html.escape(info["last_at"] or "N/A"),
    ]
    p = info["position"]
    if p:
        lines += [
            "<b>OPEN LONG: " + html.escape(p["symbol"]) + "</b>",
            "Entry: " + money(p["entry_price"]),
            "Qty: " + p["qty"],
            "SL: " + money(p["stop_price"]),
            "TP: " + money(p["take_price"]),
        ]
        if info["unrealized_net_pnl_estimate"] is not None:
            lines.append("Unrealized PnL (net exit estimate): " +
                         format(D(info["unrealized_net_pnl_estimate"]), "+,.4f") + " USDT")
    else:
        lines.append("Position: NONE")
    lines += [
        "Closed trades: " + str(stats["closed_trades"]),
        "Wins / losses: " + str(stats["wins"]) + " / " + str(stats["losses"]),
        "Realized net PnL: " + format(D(stats["realized_net_pnl"]), "+,.4f") + " USDT",
        ("Win rate: N/A" if stats["win_rate"] is None else
         "Win rate: {:.2f}%".format(stats["win_rate"] * 100)),
        "<i>Virtual balance. Simulated SL/TP only checked on observed quotes.</i>",
    ]
    return "\n".join(lines)


def format_trades(trader):
    fills = trader.fills(8)
    if not fills:
        return "DEMO: belum ada transaksi."
    lines = ["<b>DEMO FILLS (newest first)</b>"]
    for row in fills:
        pnl = ("" if row["realized_pnl"] is None else
               " | PnL " + format(D(row["realized_pnl"]), "+,.4f") + " USDT")
        lines.append(
            "#" + str(row["id"]) + " " + row["side"] + " " +
            html.escape(row["symbol"]) + " @ " + money(row["price"]) +
            pnl + " (" + html.escape(row["reason"]) + ")"
        )
    return "\n".join(lines)


def telegram_send(token, chat, message):
    try:
        response = requests.post(
            "https://api.telegram.org/bot" + token + "/sendMessage",
            data={"chat_id": chat, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("ok") is True
    except (requests.RequestException, ValueError) as exc:
        print("DEMO notification failed:", type(exc).__name__, flush=True)
        return False


def notify_pending(trader, sender=telegram_send):
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return {"sent": 0, "pending": len(trader.pending_notifications()),
                "status": "no_telegram_config"}
    sent = 0
    for fill in trader.pending_notifications():
        if not sender(token, chat, format_fill(fill, trader)):
            break
        trader.acknowledge_notification(fill["id"])
        sent += 1
    return {"sent": sent, "pending": len(trader.pending_notifications())}


def cycle(trader, source_db, symbol, quote_provider=verified_quote):
    try:
        price, at = quote_provider(symbol)
    except Exception as exc:
        return {"action": "SKIP", "reason": "quote_unavailable",
                "error_type": type(exc).__name__,
                "notifications": notify_pending(trader)}
    mark = trader.observe_price(symbol, price, at)
    if mark["action"] == "REJECT":
        return {"action": "REJECT", "reason": mark["reason"],
                "notifications": notify_pending(trader)}
    signal = load_latest_agent_signal(source_db, symbol)
    decision = {"action": "SKIP", "reason": "no_saved_signal"}
    if signal:
        priced = MarketSignal(symbol, price, signal.action, signal.observed_at)
        decision = trader.tick(priced)
    return {
        "mode": "PAPER_ONLY", "mark_price": str(price),
        "mark_action": mark["action"], "mark_reason": mark["reason"],
        "signal_action": decision["action"], "signal_reason": decision["reason"],
        "notifications": notify_pending(trader),
    }
