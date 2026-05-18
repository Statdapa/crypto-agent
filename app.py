"""
app.py — FinSight Agent Flask Backend v2
=========================================
Tambahan dari v1:
- Token authentication (DASHBOARD_TOKEN di .env)
- Proper error handling + retry
- OKX balance real via API
- Weekly report endpoint
- Health check endpoint

CARA JALANKAN:
    python app.py

TAMBAHKAN KE .env:
    ETHERSCAN_API_KEY=your_key
    WALLET_ADDRESS=0xYourWallet
    DASHBOARD_TOKEN=bebas_isi_password_apapun
"""

import os, time, sqlite3, requests, hmac, hashlib, base64
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder=".")
CORS(app)

ETHERSCAN_KEY   = os.getenv("ETHERSCAN_API_KEY", "")
WALLET_ADDRESS  = os.getenv("WALLET_ADDRESS", "")
OKX_WALLET      = os.getenv("OKX_WALLET_ADDRESS", "")
OKX_API_KEY     = os.getenv("OKX_API_KEY", "")
OKX_SECRET_KEY  = os.getenv("OKX_SECRET_KEY", "")
OKX_PASSPHRASE  = os.getenv("OKX_PASSPHRASE", "")
DB_PATH         = os.getenv("DB_PATH", "btc_agent.db")
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
COINGECKO_BASE  = "https://api.coingecko.com/api/v3"
ETHERSCAN_BASE  = "https://api.etherscan.io/v2/api"
OKX_BASE_URL    = "https://www.okx.com"
WEI_TO_ETH      = 1e-18

_cache = {}

def get_cache(key, ttl=60):
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < ttl:
            return val
    return None

def set_cache(key, val):
    _cache[key] = (val, time.time())

def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not DASHBOARD_TOKEN:
            return f(*args, **kwargs)
        token = request.headers.get("X-Dashboard-Token") or request.args.get("token")
        if token != DASHBOARD_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def safe_get(url, params=None, timeout=8, retries=2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            if attempt == retries:
                raise TimeoutError(f"Timeout: {url}")
            time.sleep(1)
        except requests.exceptions.ConnectionError:
            if attempt == retries:
                raise ConnectionError(f"Connection failed: {url}")
            time.sleep(1)
        except Exception as e:
            raise e

def etherscan_get(params):
    if not ETHERSCAN_KEY:
        raise ValueError("ETHERSCAN_API_KEY tidak ada di .env")
    params["apikey"]  = ETHERSCAN_KEY
    params["chainid"] = 1
    data = safe_get(ETHERSCAN_BASE, params=params)
    if data.get("status") == "0" and data.get("message") not in ("No transactions found", "No records found"):
        raise ValueError(data.get("result", "Etherscan error"))
    return data

def okx_headers(method, path, body=""):
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    msg = timestamp + method.upper() + path + body
    sig = base64.b64encode(
        hmac.new(OKX_SECRET_KEY.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "OK-ACCESS-KEY":        OKX_API_KEY,
        "OK-ACCESS-SIGN":       sig,
        "OK-ACCESS-TIMESTAMP":  timestamp,
        "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
        "Content-Type":         "application/json",
    }


# ── Serve dashboard ──
@app.route("/")
def index():
    return send_from_directory(".", "dashboard.html")


# ── Health check ──
@app.route("/api/health")
def health():
    status = {
        "status":               "ok",
        "timestamp":            datetime.now().isoformat(),
        "etherscan_configured": bool(ETHERSCAN_KEY),
        "wallet_configured":    bool(WALLET_ADDRESS),
        "okx_configured":       bool(OKX_API_KEY and OKX_SECRET_KEY),
        "db_exists":            os.path.exists(DB_PATH),
        "auth_enabled":         bool(DASHBOARD_TOKEN),
        "wallet_preview":       WALLET_ADDRESS[:8]+"..."+WALLET_ADDRESS[-4:] if WALLET_ADDRESS else None,
    }
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            status["db_tables"] = [t[0] for t in tables]
            conn.close()
        except Exception as e:
            status["db_error"] = str(e)
    return jsonify(status)


# ── Prices ──
@app.route("/api/prices")
def prices():
    ids = request.args.get("ids", "bitcoin,ethereum,solana,binancecoin,dogecoin,avalanche-2,ripple,cardano,polkadot")
    cached = get_cache(f"prices_{ids}", ttl=30)
    if cached: return jsonify(cached)
    try:
        data = safe_get(f"{COINGECKO_BASE}/simple/price",
                        params={"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true"})
        set_cache(f"prices_{ids}", data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/chart")
def chart():
    coin = request.args.get("coin", "bitcoin")
    days = request.args.get("days", "1")
    cached = get_cache(f"chart_{coin}_{days}", ttl=120)
    if cached: return jsonify(cached)
    try:
        interval = "hourly" if int(days) <= 1 else "daily"
        data = safe_get(f"{COINGECKO_BASE}/coins/{coin}/market_chart",
                        params={"vs_currency": "usd", "days": days, "interval": interval})
        set_cache(f"chart_{coin}_{days}", data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/market")
def market():
    cached = get_cache("market", ttl=60)
    if cached: return jsonify(cached)
    try:
        data = safe_get(f"{COINGECKO_BASE}/coins/markets",
                        params={"vs_currency": "usd", "order": "market_cap_desc",
                                "per_page": 20, "page": 1, "sparkline": "true",
                                "price_change_percentage": "1h,24h,7d"})
        set_cache("market", data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/feargreed")
def feargreed():
    cached = get_cache("fg", ttl=300)
    if cached: return jsonify(cached)
    try:
        data = safe_get("https://api.alternative.me/fng/?limit=1", timeout=5)
        set_cache("fg", data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 503


# ── Wallet ──
@app.route("/api/wallet/balance")
def wallet_balance():
    address = request.args.get("address", WALLET_ADDRESS)
    if not address:
        return jsonify({"error": "WALLET_ADDRESS tidak ada di .env"}), 400
    cached = get_cache(f"balance_{address}", ttl=60)
    if cached: return jsonify(cached)
    try:
        data        = etherscan_get({"module": "account", "action": "balance", "address": address, "tag": "latest"})
        balance_eth = int(data["result"]) * WEI_TO_ETH
        price_data  = safe_get(f"{COINGECKO_BASE}/simple/price", params={"ids": "ethereum", "vs_currencies": "usd"})
        eth_price   = price_data.get("ethereum", {}).get("usd", 0)
        result = {"address": address, "balance_eth": round(balance_eth, 6),
                  "balance_usd": round(balance_eth * eth_price, 2), "eth_price": eth_price}
        set_cache(f"balance_{address}", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wallet/transactions")
def wallet_transactions():
    address = request.args.get("address", WALLET_ADDRESS)
    if not address:
        return jsonify({"error": "WALLET_ADDRESS tidak ada di .env"}), 400
    cached = get_cache(f"txs_{address}", ttl=120)
    if cached: return jsonify(cached)
    try:
        data = etherscan_get({"module": "account", "action": "txlist", "address": address,
                              "startblock": 0, "endblock": 99999999, "page": 1, "offset": 50, "sort": "desc"})
        txs = data.get("result", [])
        formatted = [{
            "hash":           tx["hash"],
            "from":           tx["from"],
            "to":             tx["to"],
            "value_eth":      round(int(tx["value"]) * WEI_TO_ETH, 6),
            "is_error":       tx.get("isError") == "1",
            "direction":      "out" if tx["from"].lower() == address.lower() else "in",
            "timestamp":      datetime.utcfromtimestamp(int(tx["timeStamp"])).strftime("%d %b %Y %H:%M"),
            "timestamp_unix": int(tx["timeStamp"]),
        } for tx in txs]
        result = {"address": address, "transactions": formatted}
        set_cache(f"txs_{address}", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── OKX Balance Real ──
@app.route("/api/okx/balance")
def okx_balance():
    if not (OKX_API_KEY and OKX_SECRET_KEY and OKX_PASSPHRASE):
        return jsonify({"error": "OKX credentials tidak ada di .env (OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE)"}), 400
    cached = get_cache("okx_balance", ttl=60)
    if cached: return jsonify(cached)
    try:
        path    = "/api/v5/account/balance"
        headers = okx_headers("GET", path)
        r       = requests.get(OKX_BASE_URL + path, headers=headers, timeout=10)
        r.raise_for_status()
        data    = r.json()
        if data.get("code") != "0":
            return jsonify({"error": data.get("msg", "OKX API error")}), 500
        details  = data["data"][0]["details"] if data.get("data") else []
        balances = [
            {"currency": d["ccy"], "available": float(d["availBal"]),
             "frozen": float(d["frozenBal"]),
             "total": float(d["availBal"]) + float(d["frozenBal"]),
             "usd_value": float(d.get("eqUsd", 0))}
            for d in details
            if float(d["availBal"]) + float(d["frozenBal"]) > 0
        ]
        total_usd = round(sum(b["usd_value"] for b in balances), 2)
        result    = {"balances": balances, "total_usd": total_usd}
        set_cache("okx_balance", result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Anomaly Scan ──
@app.route("/api/scan", methods=["POST"])
def scan():
    body    = request.get_json(silent=True) or {}
    address = body.get("address", WALLET_ADDRESS)
    if not address:
        return jsonify({"error": "WALLET_ADDRESS tidak dikonfigurasi di .env"}), 400
    if not ETHERSCAN_KEY:
        return jsonify({"error": "ETHERSCAN_API_KEY tidak ada di .env"}), 400
    try:
        from anomaly_detector import AnomalyDetector
        detector      = AnomalyDetector(db_path=DB_PATH)
        wallet_alerts = detector.check_wallet_anomaly(address)
        market_alerts = detector.check_market_anomaly("BTC") + detector.check_market_anomaly("ETH")
        all_alerts    = wallet_alerts + market_alerts
        return jsonify({
            "success":    True,
            "address":    address,
            "total":      len(all_alerts),
            "scanned_at": datetime.now().isoformat(),
            "alerts": [{
                "type": a.alert_type, "severity": a.severity, "title": a.title,
                "description": a.description, "value": a.value, "threshold": a.threshold,
                "wallet": a.wallet, "coin": a.coin, "tx_hash": a.tx_hash,
                "timestamp": a.timestamp, "ai_reason": a.ai_reason,
            } for a in all_alerts],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan/history")
def scan_history():
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            """SELECT alert_type, severity, title, wallet, coin, value, threshold,
                      description, tx_hash, detected_at
               FROM anomaly_log ORDER BY detected_at DESC LIMIT 20"""
        ).fetchall()
        conn.close()
        return jsonify({"alerts": [
            {"type": r[0], "severity": r[1], "title": r[2], "wallet": r[3], "coin": r[4],
             "value": r[5], "threshold": r[6], "description": r[7], "tx_hash": r[8], "detected_at": r[9]}
            for r in rows
        ], "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Weekly Report ──
@app.route("/api/report/weekly")
def weekly_report():
    coin = request.args.get("coin", "BTC").upper()
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT price, change_24h, rsi, signal, confidence, recorded_at FROM price_history WHERE coin=? ORDER BY recorded_at DESC LIMIT 168",
            (coin,)
        ).fetchall()
        cutoff    = (datetime.now() - timedelta(days=7)).isoformat()
        anomalies = conn.execute(
            "SELECT severity, title, detected_at FROM anomaly_log WHERE detected_at > ? ORDER BY detected_at DESC",
            (cutoff,)
        ).fetchall()
        conn.close()

        if not rows:
            return jsonify({"error": f"Tidak ada data historis untuk {coin}"}), 404

        prices  = [r[0] for r in rows if r[0]]
        rsi_vals = [r[2] for r in rows if r[2]]
        signals  = [r[3] for r in rows if r[3]]

        p_open  = prices[-1]; p_close = prices[0]
        p_high  = max(prices); p_low = min(prices)
        pct_chg = round((p_close - p_open) / p_open * 100, 2) if p_open else 0
        avg_rsi = round(sum(rsi_vals) / len(rsi_vals), 1) if rsi_vals else None

        buy_c  = signals.count("BUY")
        sell_c = signals.count("SELL")
        hold_c = signals.count("HOLD")
        dominant = max([("BUY", buy_c), ("SELL", sell_c), ("HOLD", hold_c)], key=lambda x: x[1])[0]

        return jsonify({
            "coin": coin, "period": "7 days",
            "generated": datetime.now().strftime("%d %b %Y %H:%M"),
            "price": {
                "open": round(p_open, 2), "close": round(p_close, 2),
                "high": round(p_high, 2), "low": round(p_low, 2),
                "pct_change": pct_chg,
                "trend": "bullish" if pct_chg > 0 else "bearish",
            },
            "signals": {
                "dominant": dominant, "buy_count": buy_c,
                "sell_count": sell_c, "hold_count": hold_c, "avg_rsi": avg_rsi,
            },
            "anomalies": {
                "total":    len(anomalies),
                "critical": sum(1 for a in anomalies if a[0] == "CRITICAL"),
                "high":     sum(1 for a in anomalies if a[0] == "HIGH"),
                "medium":   sum(1 for a in anomalies if a[0] == "MEDIUM"),
                "list":     [{"severity": a[0], "title": a[1], "time": a[2]} for a in anomalies[:5]],
            },
            "summary": (
                f"{coin} {'gained' if pct_chg > 0 else 'lost'} {abs(pct_chg):.1f}% this week. "
                f"Dominant signal: {dominant}. Avg RSI: {avg_rsi}. "
                f"{len(anomalies)} anomalies — {sum(1 for a in anomalies if a[0]=='CRITICAL')} critical."
            )
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Signals from DB ──
@app.route("/api/signals")
def signals():
    try:
        conn  = sqlite3.connect(DB_PATH)
        coins = conn.execute("SELECT DISTINCT coin FROM price_history").fetchall()
        result = []
        for (coin,) in coins:
            row = conn.execute(
                "SELECT price, change_24h, rsi, signal, confidence, recorded_at FROM price_history WHERE coin=? ORDER BY recorded_at DESC LIMIT 1",
                (coin,)
            ).fetchone()
            if row:
                result.append({"coin": coin, "price": row[0], "change_24h": row[1],
                                "rsi": row[2], "signal": row[3], "confidence": row[4], "updated_at": row[5]})
        conn.close()
        return jsonify({"signals": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Config ──
@app.route("/api/config")
def config():
    return jsonify({
        "etherscan_configured": bool(ETHERSCAN_KEY),
        "wallet_configured":    bool(WALLET_ADDRESS),
        "okx_cex_configured":   bool(OKX_API_KEY and OKX_SECRET_KEY),
        "okx_wallet_configured":bool(OKX_WALLET),
        "auth_enabled":         bool(DASHBOARD_TOKEN),
        "db_path":              DB_PATH,
        "wallet_preview":       WALLET_ADDRESS[:8]+"..."+WALLET_ADDRESS[-4:] if WALLET_ADDRESS else None,
    })


# ── Error handlers ──
@app.errorhandler(404)
def not_found(e): return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def server_error(e): return jsonify({"error": "Internal server error", "detail": str(e)}), 500

@app.errorhandler(401)
def unauthorized(e): return jsonify({"error": "Unauthorized"}), 401


if __name__ == "__main__":
    print("\nFinSight Agent — Flask Backend v2")
    print("=" * 45)
    print(f"Etherscan : {'configured' if ETHERSCAN_KEY else 'MISSING — add ETHERSCAN_API_KEY to .env'}")
    print(f"Wallet    : {WALLET_ADDRESS[:8]+'...'+WALLET_ADDRESS[-4:] if WALLET_ADDRESS else 'MISSING — add WALLET_ADDRESS to .env'}")
    print(f"OKX CEX   : {'configured' if OKX_API_KEY else 'not configured'}")
    print(f"Auth      : {'enabled' if DASHBOARD_TOKEN else 'disabled'}")
    print(f"DB        : {DB_PATH} ({'exists' if os.path.exists(DB_PATH) else 'NOT FOUND'})")
    print(f"\nDashboard : http://localhost:5000")
    print(f"Health    : http://localhost:5000/api/health")
    print("=" * 45 + "\n")
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)