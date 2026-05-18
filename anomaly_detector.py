"""
anomaly_detector.py
===================
Anomaly detection module for FinSight Agent.
Detects suspicious activity in on-chain wallets and market data.

USAGE:
    from anomaly_detector import AnomalyDetector

    detector = AnomalyDetector(db_path="btc_agent.db")

    alerts = detector.check_wallet_anomaly("0xYourWalletAddress")
    for alert in alerts:
        send_telegram(alert.to_telegram())

    alerts = detector.check_market_anomaly("BTC")
    for alert in alerts:
        send_telegram(alert.to_telegram())

INTEGRATION INTO btc_agent_okx.py:
    1. from anomaly_detector import AnomalyDetector
       detector = AnomalyDetector(llm=llm)

    2. schedule.every(30).minutes.do(lambda: run_anomaly_scan())

    3. elif text_lower.startswith("/scan"):
           parts = text.split()
           addr = parts[1] if len(parts) > 1 else OKX_WALLET_ADDRESS
           send_telegram("Scanning for anomalies...")
           alerts = detector.check_wallet_anomaly(addr)
           if alerts:
               for a in alerts:
                   send_telegram(a.to_telegram())
           else:
               send_telegram("No anomalies detected.")
"""

import os
import sqlite3
import requests
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "")
ETHERSCAN_BASE = "https://api.etherscan.io/v2/api"
WEI_TO_ETH = 1e-18

SEVERITY_LOW      = "LOW"
SEVERITY_MEDIUM   = "MEDIUM"
SEVERITY_HIGH     = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

SEVERITY_PREFIX = {
    SEVERITY_LOW:      "[LOW]     ",
    SEVERITY_MEDIUM:   "[MEDIUM]  ",
    SEVERITY_HIGH:     "[HIGH]    ",
    SEVERITY_CRITICAL: "[CRITICAL]",
}


@dataclass
class AnomalyAlert:
    alert_type: str
    severity: str
    title: str
    description: str
    value: float = 0.0
    threshold: float = 0.0
    wallet: str = ""
    coin: str = ""
    tx_hash: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%d %b %Y %H:%M"))
    ai_reason: str = ""

    def to_telegram(self) -> str:
        prefix      = SEVERITY_PREFIX.get(self.severity, "[ALERT]   ")
        wallet_line = f"Wallet    : {self.wallet[:8]}...{self.wallet[-4:]}\n" if self.wallet else ""
        coin_line   = f"Coin      : {self.coin}\n" if self.coin else ""
        tx_line     = f"Tx Hash   : {self.tx_hash[:16]}...\n" if self.tx_hash else ""
        ai_line     = f"\nAI Analysis\n{self.ai_reason}\n" if self.ai_reason else ""
        etherscan   = f"\nhttps://etherscan.io/address/{self.wallet}" if self.wallet else ""

        return (
            f"--------------------\n"
            f"ANOMALY ALERT\n"
            f"{prefix} {self.title}\n"
            f"--------------------\n\n"
            f"{wallet_line}"
            f"{coin_line}"
            f"{tx_line}"
            f"Value     : {self.value}\n"
            f"Threshold : {self.threshold}\n"
            f"Time      : {self.timestamp}\n"
            f"\nDetail\n"
            f"{self.description}"
            f"{ai_line}"
            f"{etherscan}\n"
            f"--------------------\n"
            f"Note: Not financial advice. Always verify manually."
        )


class AnomalyDetector:

    def __init__(self, db_path: str = "btc_agent.db", llm=None):
        self.db_path = db_path
        self.llm = llm
        self._init_tables()

    # ------------------------------------------------------------------
    # Database setup
    # ------------------------------------------------------------------

    def _init_tables(self):
        """Create required tables if they do not already exist."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS anomaly_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type  TEXT,
                severity    TEXT,
                title       TEXT,
                wallet      TEXT,
                coin        TEXT,
                value       REAL,
                threshold   REAL,
                description TEXT,
                tx_hash     TEXT,
                detected_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                coin        TEXT,
                price       REAL,
                change_24h  REAL,
                rsi         REAL,
                fg_value    REAL,
                signal      TEXT,
                confidence  REAL,
                recorded_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()

    # Kept for backward compatibility with any external code that calls this directly.
    def _init_anomaly_table(self):
        self._init_tables()

    def _log_anomaly(self, alert: AnomalyAlert):
        """Persist an alert to the anomaly_log table."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO anomaly_log
               (alert_type, severity, title, wallet, coin, value, threshold, description, tx_hash)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                alert.alert_type, alert.severity, alert.title,
                alert.wallet, alert.coin, alert.value,
                alert.threshold, alert.description, alert.tx_hash,
            ),
        )
        conn.commit()
        conn.close()

    def _already_alerted(self, tx_hash: str, hours: int = 24) -> bool:
        """
        Return True if this tx_hash was already logged within the last `hours` hours.
        Market alerts (no tx_hash) always return False so they are never suppressed.
        """
        if not tx_hash:
            return False
        conn = sqlite3.connect(self.db_path)
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        row = conn.execute(
            "SELECT id FROM anomaly_log WHERE tx_hash=? AND detected_at > ?",
            (tx_hash, cutoff),
        ).fetchone()
        conn.close()
        return row is not None

    # ------------------------------------------------------------------
    # External API helpers
    # ------------------------------------------------------------------

    def _etherscan(self, params: dict) -> dict:
        """
        Wrapper for the Etherscan v2 API.
        Raises ValueError if the API key is missing or the response indicates an error.
        """
        if not ETHERSCAN_KEY:
            raise ValueError("ETHERSCAN_API_KEY not set in .env")
        params["apikey"] = ETHERSCAN_KEY
        params["chainid"] = 1
        r = requests.get(ETHERSCAN_BASE, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "0" and data.get("message") not in (
            "No transactions found",
            "No records found",
        ):
            raise ValueError(data.get("result", "Etherscan error"))
        return data

    def _get_eth_price(self) -> float:
        """Fetch the current ETH/USD price from CoinGecko. Returns 0.0 on failure."""
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "ethereum", "vs_currencies": "usd"},
                timeout=5,
            )
            r.raise_for_status()
            return float(r.json()["ethereum"]["usd"])
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # Statistical helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _zscore(values: List[float], target: float) -> float:
        """Return the absolute z-score of `target` relative to `values`."""
        if len(values) < 3:
            return 0.0
        arr = np.array(values, dtype=float)
        std = arr.std()
        if std == 0:
            return 0.0
        return abs((target - arr.mean()) / std)

    @staticmethod
    def _iqr_outlier(values: List[float], target: float, multiplier: float = 2.0) -> bool:
        """Return True if `target` lies outside the IQR fence."""
        if len(values) < 4:
            return False
        arr = np.array(values, dtype=float)
        q1, q3 = np.percentile(arr, 25), np.percentile(arr, 75)
        iqr = q3 - q1
        return target > q3 + multiplier * iqr or target < q1 - multiplier * iqr

    # ------------------------------------------------------------------
    # AI analysis (optional)
    # ------------------------------------------------------------------

    def _get_ai_reason(self, alert: AnomalyAlert) -> str:
        """
        Use an LLM to generate a short analysis for the alert.
        Returns an empty string if no LLM is configured or the call fails.
        """
        if not self.llm:
            return ""
        try:
            from langchain_core.messages import HumanMessage

            prompt = (
                f"You are a crypto security analyst. Explain in 2 concise sentences "
                f"why the following activity is suspicious:\n\n"
                f"Type: {alert.alert_type}\n"
                f"Detail: {alert.description}\n"
                f"Value: {alert.value} | Threshold: {alert.threshold}\n\n"
                f"Focus on real risk, not theory. Be direct and professional."
            )
            response = self.llm.invoke([HumanMessage(content=prompt)])
            return response.content.strip()
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Wallet checks
    # ------------------------------------------------------------------

    def check_wallet_anomaly(
        self, wallet: str, tx_limit: int = 50, force: bool = False
    ) -> List[AnomalyAlert]:
        """
        Check on-chain anomalies for `wallet`.
        Returns a list of AnomalyAlert objects (empty if nothing suspicious found).

        Args:
            wallet:   Ethereum address starting with '0x'.
            tx_limit: Number of recent transactions to fetch (default 50).
            force:    If True, bypass the 24-hour deduplication window and return
                      ALL detected anomalies regardless of prior alerts.
                      Use this for manual scans so historical anomalies are visible.
        """
        if not wallet or not wallet.startswith("0x"):
            return []

        try:
            data = self._etherscan(
                {
                    "module": "account",
                    "action": "txlist",
                    "address": wallet,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": tx_limit,
                    "sort": "desc",
                }
            )
            txs = data.get("result", [])
        except ValueError as e:
            print(f"[AnomalyDetector] Etherscan error: {e}")
            return []

        if not txs or not isinstance(txs, list):
            return []

        eth_price      = self._get_eth_price()
        all_eth_values = [int(tx["value"]) * WEI_TO_ETH for tx in txs]

        candidates: List[AnomalyAlert] = []
        candidates += self._check_large_transfer(txs, wallet, all_eth_values, eth_price)
        candidates += self._check_new_wallet_interaction(txs, wallet)
        candidates += self._check_frequency_spike(txs, wallet)
        candidates += self._check_failed_tx_spike(txs, wallet)
        candidates += self._check_dust_attack(txs, wallet)
        candidates += self._check_rapid_drain(txs, wallet, all_eth_values)

        alerts: List[AnomalyAlert] = []
        for alert in candidates:
            is_new = not self._already_alerted(alert.tx_hash)
            if is_new:
                alert.ai_reason = self._get_ai_reason(alert)
                self._log_anomaly(alert)
                alerts.append(alert)
            elif force:
                alert.ai_reason = self._get_ai_reason(alert)
                alerts.append(alert)

        return alerts

    def _check_large_transfer(
        self,
        txs: list,
        wallet: str,
        all_eth_values: List[float],
        eth_price: float,
    ) -> List[AnomalyAlert]:
        alerts    = []
        historical = all_eth_values[5:]  # Skip 5 most recent as baseline.

        for tx in txs[:10]:
            eth_val = int(tx["value"]) * WEI_TO_ETH
            if eth_val < 0.01:
                continue

            zscore     = self._zscore(historical, eth_val)
            is_outlier = self._iqr_outlier(historical, eth_val, multiplier=2.5)
            usd_val    = eth_val * eth_price

            if zscore > 3.0 or (is_outlier and eth_val > 1.0):
                severity  = SEVERITY_CRITICAL if zscore > 5 or usd_val > 50_000 else SEVERITY_HIGH
                direction = "outgoing from" if tx["from"].lower() == wallet.lower() else "incoming to"
                mean_hist = float(np.mean(historical)) if historical else 0.0
                std_hist  = float(np.std(historical))  if historical else 0.0
                alerts.append(
                    AnomalyAlert(
                        alert_type="LARGE_TRANSFER",
                        severity=severity,
                        title=f"Large Transfer Detected — {eth_val:.4f} ETH",
                        description=(
                            f"Transfer of {eth_val:.4f} ETH (approx. ${usd_val:,.0f}) "
                            f"{direction} your wallet.\n"
                            f"This is {zscore:.1f} standard deviations above the historical average.\n"
                            f"Average transfer size: {mean_hist:.4f} ETH"
                        ),
                        value=round(eth_val, 6),
                        threshold=round(mean_hist + 3 * std_hist, 6),
                        wallet=wallet,
                        tx_hash=tx["hash"],
                    )
                )
        return alerts

    def _check_new_wallet_interaction(self, txs: list, wallet: str) -> List[AnomalyAlert]:
        alerts = []

        # Build a set of known addresses from historical transactions.
        known_addresses: set = set()
        for tx in txs[5:]:
            known_addresses.add(tx.get("from", "").lower())
            known_addresses.add(tx.get("to",   "").lower())
        known_addresses.discard(wallet.lower())
        known_addresses.discard("")

        for tx in txs[:5]:
            eth_val     = int(tx["value"]) * WEI_TO_ETH
            counterpart = tx["to"] if tx["from"].lower() == wallet.lower() else tx["from"]
            if not counterpart:
                continue
            if counterpart.lower() not in known_addresses and eth_val > 0.05:
                alerts.append(
                    AnomalyAlert(
                        alert_type="NEW_ADDRESS_INTERACTION",
                        severity=SEVERITY_MEDIUM,
                        title="Interaction with Unknown Wallet",
                        description=(
                            f"Transaction of {eth_val:.4f} ETH to/from an address "
                            f"with no prior interaction.\n"
                            f"Address: {counterpart[:10]}...{counterpart[-4:]}\n"
                            f"Always verify recipient identity before large transfers."
                        ),
                        value=round(eth_val, 6),
                        threshold=0.05,
                        wallet=wallet,
                        tx_hash=tx["hash"],
                    )
                )
        return alerts

    def _check_frequency_spike(self, txs: list, wallet: str) -> List[AnomalyAlert]:
        now        = datetime.utcnow()
        recent_1h  = [tx for tx in txs if datetime.utcfromtimestamp(int(tx["timeStamp"])) > now - timedelta(hours=1)]
        recent_24h = [tx for tx in txs if datetime.utcfromtimestamp(int(tx["timeStamp"])) > now - timedelta(hours=24)]

        avg_per_hour = len(recent_24h) / 24.0
        count_1h     = len(recent_1h)

        if count_1h > 0 and avg_per_hour > 0:
            spike_ratio = count_1h / avg_per_hour
            if spike_ratio > 5 and count_1h >= 3:
                return [
                    AnomalyAlert(
                        alert_type="FREQUENCY_SPIKE",
                        severity=SEVERITY_HIGH if spike_ratio > 10 else SEVERITY_MEDIUM,
                        title=f"Transaction Frequency Spike ({count_1h} in 1 hour)",
                        description=(
                            f"Detected {count_1h} transactions in the last hour.\n"
                            f"Normal average: {avg_per_hour:.1f} tx/hour ({spike_ratio:.1f}x spike).\n"
                            f"This may indicate bot activity or a compromised account."
                        ),
                        value=float(count_1h),
                        threshold=avg_per_hour * 5,
                        wallet=wallet,
                    )
                ]
        return []

    def _check_failed_tx_spike(self, txs: list, wallet: str) -> List[AnomalyAlert]:
        recent_10 = txs[:10]
        if not recent_10:
            return []

        failed    = [tx for tx in recent_10 if tx.get("isError") == "1"]
        fail_rate = len(failed) / len(recent_10)

        if fail_rate >= 0.4 and len(failed) >= 3:
            return [
                AnomalyAlert(
                    alert_type="FAILED_TX_SPIKE",
                    severity=SEVERITY_HIGH if fail_rate >= 0.7 else SEVERITY_MEDIUM,
                    title=f"High Failed Transaction Rate ({len(failed)}/10)",
                    description=(
                        f"{len(failed)} of the last 10 transactions failed "
                        f"({fail_rate * 100:.0f}% failure rate).\n"
                        f"Possible causes: misconfigured bot, insufficient gas, "
                        f"or unauthorized access attempts on the wallet."
                    ),
                    value=round(fail_rate * 100, 2),
                    threshold=40.0,
                    wallet=wallet,
                )
            ]
        return []

    def _check_dust_attack(self, txs: list, wallet: str) -> List[AnomalyAlert]:
        now        = datetime.utcnow()
        recent_24h = [tx for tx in txs if datetime.utcfromtimestamp(int(tx["timeStamp"])) > now - timedelta(hours=24)]
        dust       = [
            tx for tx in recent_24h
            if tx["to"].lower() == wallet.lower()
            and 0 < int(tx["value"]) * WEI_TO_ETH < 0.001
        ]
        senders = {tx["from"].lower() for tx in dust}

        if len(dust) >= 5 and len(senders) >= 3:
            return [
                AnomalyAlert(
                    alert_type="DUST_ATTACK",
                    severity=SEVERITY_HIGH,
                    title=f"Potential Dust Attack ({len(dust)} dust transactions)",
                    description=(
                        f"Received {len(dust)} dust transactions (< 0.001 ETH) from "
                        f"{len(senders)} different wallets within 24 hours.\n"
                        f"Dust attacks are used to de-anonymize wallets or initiate phishing. "
                        f"Do not interact with this dust."
                    ),
                    value=float(len(dust)),
                    threshold=5.0,
                    wallet=wallet,
                )
            ]
        return []

    def _check_rapid_drain(
        self, txs: list, wallet: str, all_eth_values: List[float]
    ) -> List[AnomalyAlert]:
        now       = datetime.utcnow()
        recent_6h = [
            tx for tx in txs
            if datetime.utcfromtimestamp(int(tx["timeStamp"])) > now - timedelta(hours=6)
            and tx["from"].lower() == wallet.lower()
            and tx.get("isError") == "0"
        ]
        if len(recent_6h) < 3:
            return []

        total_out = sum(int(tx["value"]) * WEI_TO_ETH for tx in recent_6h)
        if total_out > 0.5:
            return [
                AnomalyAlert(
                    alert_type="RAPID_DRAIN",
                    severity=SEVERITY_CRITICAL if total_out > 5.0 else SEVERITY_HIGH,
                    title=f"Rapid Drain Detected — {total_out:.4f} ETH outflow",
                    description=(
                        f"Total of {total_out:.4f} ETH left the wallet within 6 hours "
                        f"across {len(recent_6h)} transactions.\n"
                        f"This pattern is commonly associated with compromised accounts "
                        f"or rug pulls from approved contracts."
                    ),
                    value=round(total_out, 6),
                    threshold=0.5,
                    wallet=wallet,
                )
            ]
        return []

    # ------------------------------------------------------------------
    # Market checks
    # ------------------------------------------------------------------

    def check_market_anomaly(self, coin: str = "BTC") -> List[AnomalyAlert]:
        """
        Check market anomalies for `coin` using data stored in the database.
        Requires at least 5 rows in price_history to produce meaningful results.
        """
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """SELECT price, change_24h, rsi, fg_value, signal, confidence, recorded_at
               FROM price_history
               WHERE coin = ?
               ORDER BY recorded_at DESC
               LIMIT 100""",
            (coin,),
        ).fetchall()
        conn.close()

        if len(rows) < 5:
            return []

        prices   = [r[0] for r in rows if r[0] is not None]
        changes  = [r[1] for r in rows if r[1] is not None]
        rsi_vals = [r[2] for r in rows if r[2] is not None]
        fg_vals  = [r[3] for r in rows if r[3] is not None]
        latest   = rows[0]

        alerts: List[AnomalyAlert] = []
        alerts += self._check_price_crash(coin, latest[0], latest[1], prices, changes)
        alerts += self._check_rsi_extreme(coin, latest[2], rsi_vals)
        alerts += self._check_fg_extreme(coin, latest[3], fg_vals)
        alerts += self._check_signal_flip(coin, rows)

        for alert in alerts:
            alert.ai_reason = self._get_ai_reason(alert)
            self._log_anomaly(alert)

        return alerts

    def _check_price_crash(
        self,
        coin: str,
        price: Optional[float],
        change: Optional[float],
        prices: List[float],
        changes: List[float],
    ) -> List[AnomalyAlert]:
        if change is None or not changes:
            return []

        zscore = self._zscore(changes[1:], change)

        if change <= -10:
            return [
                AnomalyAlert(
                    alert_type="PRICE_CRASH",
                    severity=SEVERITY_CRITICAL if change <= -20 else SEVERITY_HIGH,
                    title=f"{coin} Price Crash — {change:.2f}% in 24h",
                    description=(
                        f"{coin} dropped {abs(change):.2f}% in the last 24 hours.\n"
                        f"This is {zscore:.1f} standard deviations outside normal movement.\n"
                        f"Review your position and stop-loss levels."
                    ),
                    value=round(change, 2),
                    threshold=-10.0,
                    coin=coin,
                )
            ]
        elif change >= 15:
            return [
                AnomalyAlert(
                    alert_type="PRICE_PUMP",
                    severity=SEVERITY_MEDIUM,
                    title=f"{coin} Price Pump — +{change:.2f}% in 24h",
                    description=(
                        f"{coin} surged {change:.2f}% in 24 hours — an unusual move.\n"
                        f"Monitor for a potential sell-off. Verify volume and market sentiment."
                    ),
                    value=round(change, 2),
                    threshold=15.0,
                    coin=coin,
                )
            ]
        return []

    def _check_rsi_extreme(
        self, coin: str, rsi: Optional[float], rsi_vals: List[float]
    ) -> List[AnomalyAlert]:
        if rsi is None:
            return []

        if rsi >= 80:
            return [
                AnomalyAlert(
                    alert_type="RSI_OVERBOUGHT",
                    severity=SEVERITY_MEDIUM,
                    title=f"{coin} RSI Extreme Overbought ({rsi})",
                    description=(
                        f"RSI for {coin} at {rsi} — extreme overbought level.\n"
                        f"Historically, RSI above 80 precedes a price correction.\n"
                        f"Consider reducing exposure or setting a trailing stop-loss."
                    ),
                    value=rsi,
                    threshold=80.0,
                    coin=coin,
                )
            ]
        elif rsi <= 20:
            return [
                AnomalyAlert(
                    alert_type="RSI_OVERSOLD",
                    severity=SEVERITY_MEDIUM,
                    title=f"{coin} RSI Extreme Oversold ({rsi})",
                    description=(
                        f"RSI for {coin} at {rsi} — extreme oversold level.\n"
                        f"Potential rebound, but confirm with volume and sentiment first."
                    ),
                    value=rsi,
                    threshold=20.0,
                    coin=coin,
                )
            ]
        return []

    def _check_fg_extreme(
        self, coin: str, fg: Optional[float], fg_vals: List[float]
    ) -> List[AnomalyAlert]:
        if fg is None:
            return []

        if fg <= 10:
            return [
                AnomalyAlert(
                    alert_type="EXTREME_FEAR",
                    severity=SEVERITY_HIGH,
                    title=f"Extreme Fear in Market ({fg}/100)",
                    description=(
                        f"Fear and Greed Index at {fg}/100 — extreme fear territory.\n"
                        f"Historically, extreme fear zones have been optimal accumulation periods, "
                        f"but require strong conviction and risk management."
                    ),
                    value=fg,
                    threshold=10.0,
                    coin=coin,
                )
            ]
        elif fg >= 90:
            return [
                AnomalyAlert(
                    alert_type="EXTREME_GREED",
                    severity=SEVERITY_HIGH,
                    title=f"Extreme Greed in Market ({fg}/100)",
                    description=(
                        f"Fear and Greed Index at {fg}/100 — extreme greed territory.\n"
                        f"Market euphoria at peak levels. Consider taking partial profits "
                        f"and tightening risk management."
                    ),
                    value=fg,
                    threshold=90.0,
                    coin=coin,
                )
            ]
        return []

    def _check_signal_flip(self, coin: str, rows: list) -> List[AnomalyAlert]:
        signals = [r[4] for r in rows[:5] if r[4]]
        if len(signals) < 2:
            return []

        if (
            signals[0] != signals[1]
            and signals[1] in ("BUY", "SELL")
            and signals[0] in ("BUY", "SELL")
        ):
            return [
                AnomalyAlert(
                    alert_type="SIGNAL_FLIP",
                    severity=SEVERITY_MEDIUM,
                    title=f"{coin} Signal Flip: {signals[1]} -> {signals[0]}",
                    description=(
                        f"AI signal for {coin} changed from {signals[1]} to {signals[0]}.\n"
                        f"A sudden signal flip may indicate a trend reversal.\n"
                        f"Wait for confirmation before executing."
                    ),
                    value=1.0,
                    threshold=1.0,
                    coin=coin,
                )
            ]
        return []

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_anomaly_history(self, limit: int = 10) -> str:
        """Return a formatted string of the most recent anomalies from the database."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """SELECT severity, title, coin, wallet, detected_at
               FROM anomaly_log
               ORDER BY detected_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()

        if not rows:
            return "No anomalies recorded."

        lines = ["Anomaly History\n--------------------\n"]
        for r in rows:
            prefix = SEVERITY_PREFIX.get(r[0], "[ALERT]   ")
            target = r[2] or (f"{r[3][:8]}..." if r[3] else "-")
            lines.append(f"{prefix} {r[1]}\n  Target: {target} | Time: {r[4][:16]}\n")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# CLI test entry-point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print("FinSight Anomaly Detector — Test Mode\n")
    detector = AnomalyDetector(db_path="btc_agent.db")

    print("Checking market anomalies for BTC...")
    alerts = detector.check_market_anomaly("BTC")

    if alerts:
        for alert in alerts:
            print(f"\n{alert.to_telegram()}\n{'=' * 40}")
    else:
        print("No anomalies detected.")

    print("\nAnomaly history:")
    print(detector.get_anomaly_history())