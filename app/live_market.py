# app/live_market.py

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

import pandas as pd
import requests
import websocket
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("gold-scheduler")

API_URL = "https://api.twelvedata.com/time_series"
WS_URL = "wss://ws.twelvedata.com/v1/quotes/price"

API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")
SYMBOL = os.getenv("TWELVE_DATA_SYMBOL", "XAU/USD")
WS_ENABLED = os.getenv("TWELVE_DATA_WS_ENABLED", "true").lower() == "true"

# The interval must be supported by your Twelve Data account.
INTERVAL_SECONDS = {
    "1min": 60,
    "5min": 300,
    "15min": 900,
    "30min": 1800,
    "1h": 3600,
    "4h": 14400,
    "1day": 86400,
}


class LiveGoldMarket:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop_event = threading.Event()

        # One cached historical series per requested timeframe.
        self._candles = {}
        self._history_loaded_at = {}

        self.current_price = None
        self.last_tick_at = None
        self.connection_status = "disconnected"

        self._ws_thread = None
        self._ws_app = None

    def start(self):
        """Start the WebSocket in a background thread."""
        if not WS_ENABLED:
            logger.info("Twelve Data WebSocket is disabled")
            return

        if not API_KEY:
            logger.error("Missing TWELVE_DATA_API_KEY in .env")
            return

        if self._ws_thread and self._ws_thread.is_alive():
            return

        self._stop_event.clear()
        self._ws_thread = threading.Thread(
            target=self._websocket_loop,
            name="twelve-data-websocket",
            daemon=True,
        )
        self._ws_thread.start()
        logger.info("Twelve Data WebSocket worker started")

    def stop(self):
        """Stop the WebSocket worker."""
        self._stop_event.set()

        if self._ws_app:
            try:
                self._ws_app.close()
            except Exception:
                logger.exception("Error closing Twelve Data WebSocket")

        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=3)

        logger.info("Twelve Data WebSocket worker stopped")

    def _websocket_loop(self):
        """Reconnect automatically when the connection drops."""
        while not self._stop_event.is_set():
            try:
                url = f"{WS_URL}?apikey={quote(API_KEY, safe='')}"

                self._ws_app = websocket.WebSocketApp(
                    url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )

                self._ws_app.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                )

            except Exception:
                logger.exception("Twelve Data WebSocket connection failed")

            if not self._stop_event.is_set():
                self.connection_status = "reconnecting"
                self._stop_event.wait(5)

    def _on_open(self, ws):
        self.connection_status = "connected"
        logger.info("Connected to Twelve Data WebSocket")

        ws.send(json.dumps({
            "action": "subscribe",
            "params": {
                "symbols": SYMBOL,
            },
        }))

        # Twelve Data also documents application-level heartbeat messages.
        threading.Thread(
            target=self._heartbeat_loop,
            args=(ws,),
            daemon=True,
        ).start()

    def _heartbeat_loop(self, ws):
        while not self._stop_event.wait(10):
            try:
                if ws.sock and ws.sock.connected:
                    ws.send(json.dumps({"action": "heartbeat"}))
                else:
                    return
            except Exception:
                return

    def _on_message(self, ws, message):
        try:
            payload = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Received invalid WebSocket JSON")
            return

        event = str(payload.get("event", "")).lower()

        # Log subscription acknowledgements and provider errors.
        if "status" in event:
            logger.info("Twelve Data subscription status: %s", payload)
            return

        if payload.get("status") == "error" or payload.get("code"):
            logger.warning("Twelve Data message: %s", payload)
            return

        # Only use price events to construct live candles.
        if event != "price" and "price" not in payload:
            return

        incoming_symbol = payload.get("symbol")
        if incoming_symbol and incoming_symbol != SYMBOL:
            return

        try:
            price = float(payload["price"])
            timestamp = float(
                payload.get("timestamp") or time.time()
            )

            # Accommodate Unix timestamps expressed in milliseconds.
            if timestamp > 100_000_000_000:
                timestamp /= 1000

            if not pd.notna(price) or price <= 0:
                return

            self._apply_tick(price, timestamp)

        except (KeyError, TypeError, ValueError):
            logger.warning("Could not parse Twelve Data price message: %s", payload)

    def _on_error(self, ws, error):
        self.connection_status = "error"
        logger.error("Twelve Data WebSocket error: %s", error)

    def _on_close(self, ws, status_code, message):
        self.connection_status = "disconnected"
        logger.warning(
            "Twelve Data WebSocket closed: code=%s message=%s",
            status_code,
            message,
        )

    def _load_history(self, interval, limit):
        """Load historical OHLC candles using the Twelve Data REST API."""
        if not API_KEY:
            raise RuntimeError("Missing TWELVE_DATA_API_KEY in .env")

        response = requests.get(
            API_URL,
            params={
                "symbol": SYMBOL,
                "interval": interval,
                "outputsize": limit,
                "timezone": "UTC",
                "apikey": API_KEY,
            },
            timeout=20,
        )
        response.raise_for_status()

        payload = response.json()

        if "values" not in payload:
            raise RuntimeError(
                payload.get("message", "Twelve Data returned no candle values")
            )

        rows = []

        for item in payload["values"]:
            try:
                dt = datetime.strptime(
                    item["datetime"], "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)

                rows.append({
                    "time": int(dt.timestamp()),
                    "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "ema9": None,
                    "ema21": None,
                    "ema50": None,
                    "rsi": None,
                    "atr": None,
                })
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping invalid historical candle: %s", item)

        rows.sort(key=lambda candle: candle["time"])

        # Remove duplicate candle timestamps.
        unique = {candle["time"]: candle for candle in rows}
        rows = list(unique.values())
        rows.sort(key=lambda candle: candle["time"])

        with self._lock:
            self._candles[interval] = rows
            self._history_loaded_at[interval] = time.time()

        logger.info(
            "Loaded %s historical candles for %s (%s)",
            len(rows),
            SYMBOL,
            interval,
        )

    def _apply_tick(self, price, timestamp):
        """Update the currently forming candle for every cached timeframe."""
        with self._lock:
            self.current_price = price
            self.last_tick_at = timestamp

            for interval, rows in self._candles.items():
                seconds = INTERVAL_SECONDS[interval]
                bucket_time = int(timestamp // seconds) * seconds

                dt = datetime.fromtimestamp(bucket_time, tz=timezone.utc)

                candle = {
                    "time": bucket_time,
                    "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "ema9": None,
                    "ema21": None,
                    "ema50": None,
                    "rsi": None,
                    "atr": None,
                }

                if not rows:
                    rows.append(candle)
                    continue

                last = rows[-1]

                if bucket_time < last["time"]:
                    # Ignore delayed ticks from older candles.
                    continue

                if bucket_time == last["time"]:
                    last["high"] = max(last["high"], price)
                    last["low"] = min(last["low"], price)
                    last["close"] = price
                    last["datetime"] = candle["datetime"]
                else:
                    rows.append(candle)

                # Bound memory usage.
                if len(rows) > 1000:
                    del rows[:-1000]

    @staticmethod
    def _with_indicators(rows):
        """Return candles with EMA, RSI, and ATR calculated from OHLC."""
        if not rows:
            return []

        df = pd.DataFrame(rows).copy()

        close = df["close"]
        high = df["high"]
        low = df["low"]

        df["ema9"] = close.ewm(span=9, adjust=False).mean()
        df["ema21"] = close.ewm(span=21, adjust=False).mean()
        df["ema50"] = close.ewm(span=50, adjust=False).mean()

        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(
            alpha=1 / 14, min_periods=14, adjust=False
        ).mean()
        avg_loss = loss.ewm(
            alpha=1 / 14, min_periods=14, adjust=False
        ).mean()

        rs = avg_gain / avg_loss.where(avg_loss != 0)
        df["rsi"] = 100 - (100 / (1 + rs))

        # Avoid an undefined RSI when losses are zero.
        df.loc[(avg_loss == 0) & (avg_gain > 0), "rsi"] = 100
        df.loc[(avg_loss == 0) & (avg_gain == 0), "rsi"] = 50

        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        df["atr"] = true_range.ewm(
            alpha=1 / 14, min_periods=14, adjust=False
        ).mean()

        df = df.where(pd.notna(df), None)

        result = []
        for candle in df.to_dict(orient="records"):
            cleaned = {}
            for key, value in candle.items():
                if key in ("time",):
                    cleaned[key] = int(value)
                elif isinstance(value, (int, float)) and pd.notna(value):
                    cleaned[key] = float(value)
                elif value is None or pd.isna(value):
                    cleaned[key] = None
                else:
                    cleaned[key] = value
            result.append(cleaned)

        return result

    def get_candles(self, interval="5min", limit=100):
        """Keep the response shape compatible with /api/candles."""
        norm = str(interval).strip().lower()
        mapping = {
            "1m": "1min", "1min": "1min",
            "5m": "5min", "5min": "5min",
            "15m": "15min", "15min": "15min",
            "30m": "30min", "30min": "30min",
            "1h": "1h",
            "4h": "4h",
            "1d": "1day", "1day": "1day", "daily": "1day",
        }
        interval = mapping.get(norm, norm)

        if interval not in INTERVAL_SECONDS:
            raise ValueError(f"Unsupported interval: {interval}")

        limit = max(1, min(int(limit), 500))

        with self._lock:
            loaded_at = self._history_loaded_at.get(interval, 0)
            has_history = bool(self._candles.get(interval))

        # Load historical candles on first use, then refresh the base history
        # periodically rather than making a REST request on every chart poll.
        if not has_history or time.time() - loaded_at > 1800:
            self._load_history(interval, max(limit, 100))

        with self._lock:
            rows = [dict(row) for row in self._candles.get(interval, [])]
            current_price = self.current_price

        candles = self._with_indicators(rows)[-limit:]

        if current_price is None and candles:
            current_price = candles[-1]["close"]

        return {
            "success": True,
            "symbol": SYMBOL,
            "interval": interval,
            "count": len(candles),
            "current_price": current_price,
            "connection_status": self.connection_status,
            "last_tick_at": self.last_tick_at,
            "candles": candles,
        }


# Shared instance used by FastAPI.
market = LiveGoldMarket()