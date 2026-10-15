import os
import time
from typing import Dict, Any, Optional
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://api.twelvedata.com/time_series"

# In-memory cache to prevent Twelve Data rate limiting (8 requests/min on free tier)
_CANDLE_CACHE: Dict[str, Dict[str, Any]] = {}


def get_gold_candles(interval: str = "5min", outputsize: int = 100, use_cache: bool = True) -> pd.DataFrame:
    """
    Fetch XAU/USD gold candlestick data from Twelve Data.
    Supported intervals: '5min', '15min', '1h', '4h', '1day'.
    Uses an in-memory cache (30-90s TTL depending on timeframe) to prevent API rate limits.
    """
    # Normalize interval
    normalized_interval = interval.lower().strip()
    interval_mapping = {
        "5m": "5min",
        "5min": "5min",
        "15m": "15min",
        "15min": "15min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1day",
        "1day": "1day",
        "daily": "1day",
    }
    api_interval = interval_mapping.get(normalized_interval, normalized_interval)

    cache_key = f"{api_interval}_{outputsize}"
    now = time.time()

    # Determine TTL: shorter for lower timeframe, longer for higher timeframe
    ttl_seconds = 45 if api_interval in ("5min", "15min") else 120

    if use_cache and cache_key in _CANDLE_CACHE:
        cached_entry = _CANDLE_CACHE[cache_key]
        if now - cached_entry["timestamp"] < ttl_seconds:
            return cached_entry["data"].copy()

    api_key = os.getenv("TWELVE_DATA_API_KEY")
    if not api_key:
        # If cache exists even if expired, fallback to it
        if cache_key in _CANDLE_CACHE:
            return _CANDLE_CACHE[cache_key]["data"].copy()
        raise RuntimeError("Missing TWELVE_DATA_API_KEY in .env")

    try:
        response = requests.get(
            API_URL,
            params={
                "symbol": "XAU/USD",
                "interval": api_interval,
                "outputsize": outputsize,
                "apikey": api_key,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        if "values" not in data:
            # If rate-limited or error, fallback to cache if available
            if cache_key in _CANDLE_CACHE:
                return _CANDLE_CACHE[cache_key]["data"].copy()
            raise RuntimeError(data.get("message", "No candle data returned"))

        df = pd.DataFrame(data["values"])

        for column in ["open", "high", "low", "close"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")

        df = (
            df.dropna(subset=["datetime", "open", "high", "low", "close"])
            .sort_values("datetime")
            .drop_duplicates("datetime")
            .reset_index(drop=True)
        )

        min_required = 20 if api_interval in ("1day", "4h") else 30
        if len(df) < min_required:
            if cache_key in _CANDLE_CACHE:
                return _CANDLE_CACHE[cache_key]["data"].copy()
            raise RuntimeError(f"Not enough valid candles for {api_interval} (got {len(df)})")

        _CANDLE_CACHE[cache_key] = {
            "timestamp": now,
            "data": df.copy(),
        }

        return df

    except Exception as exc:
        # Fallback to expired cache if available to avoid crashing during temporary spikes
        if cache_key in _CANDLE_CACHE:
            return _CANDLE_CACHE[cache_key]["data"].copy()
        raise exc