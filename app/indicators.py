import os
from typing import Optional, Dict, Any, List
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for column in ["open", "high", "low", "close"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

    # EMA 9 and EMA 21
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    # RSI 14
    change = df["close"].diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        min_periods=14,
        adjust=False,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        min_periods=14,
        adjust=False,
    ).mean()

    rs = avg_gain / avg_loss
    df["rsi14"] = 100 - (100 / (1 + rs))

    # ATR 14
    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    df["atr14"] = true_range.ewm(
        alpha=1 / 14,
        min_periods=14,
        adjust=False,
    ).mean()

    return df


def calculate_support_resistance(
    df: pd.DataFrame,
    htf_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Calculate Support and Resistance levels for Gold (XAU/USD):
    - Classic Pivot Points (P, R1, R2, R3, S1, S2, S3)
    - Fibonacci Pivot Points (Fib R1-R3, Fib S1-S3)
    - Dynamic Swing Highs and Swing Lows (recent price action)
    - Nearest Resistance & Nearest Support to current price
    - Price Zone classification
    """
    if len(df) < 10:
        return {}

    current_price = float(df.iloc[-1]["close"])

    # Determine reference candle for Pivot Points (prefer HTF e.g. Daily / 4H / 1H if available)
    ref_df = htf_df if (htf_df is not None and len(htf_df) >= 2) else df
    # Use previous completed candle for standard pivot calculation
    prev_candle = ref_df.iloc[-2] if len(ref_df) >= 2 else ref_df.iloc[-1]

    high = float(prev_candle["high"])
    low = float(prev_candle["low"])
    close = float(prev_candle["close"])
    pivot = (high + low + close) / 3.0
    hl_range = high - low

    # Classic Pivot Levels
    r1 = (2.0 * pivot) - low
    s1 = (2.0 * pivot) - high
    r2 = pivot + hl_range
    s2 = pivot - hl_range
    r3 = high + 2.0 * (pivot - low)
    s3 = low - 2.0 * (high - pivot)

    # Fibonacci Pivot Levels
    fib_r1 = pivot + (0.382 * hl_range)
    fib_s1 = pivot - (0.382 * hl_range)
    fib_r2 = pivot + (0.618 * hl_range)
    fib_s2 = pivot - (0.618 * hl_range)
    fib_r3 = pivot + (1.000 * hl_range)
    fib_s3 = pivot - (1.000 * hl_range)

    # Recent Swing Highs and Swing Lows (last 20-40 bars on current df)
    lookback = min(len(df), 40)
    recent_slice = df.iloc[-lookback:]
    swing_high = float(recent_slice["high"].max())
    swing_low = float(recent_slice["low"].min())

    # Collect candidate resistance levels (strictly above current price)
    all_resistances = [r for r in [r1, r2, r3, fib_r1, fib_r2, fib_r3, swing_high] if r > current_price]
    # Collect candidate support levels (strictly below current price)
    all_supports = [s for s in [s1, s2, s3, fib_s1, fib_s2, fib_s3, swing_low] if s < current_price]

    nearest_res = min(all_resistances) if all_resistances else round(current_price * 1.005, 3)
    nearest_sup = max(all_supports) if all_supports else round(current_price * 0.995, 3)

    dist_to_res = round(nearest_res - current_price, 3)
    dist_to_sup = round(current_price - nearest_sup, 3)

    # Classify current price zone
    if dist_to_sup <= 1.0:
        zone = "TESTING_SUPPORT"
    elif dist_to_res <= 1.0:
        zone = "TESTING_RESISTANCE"
    elif current_price > swing_high:
        zone = "BREAKOUT_HIGH"
    elif current_price < swing_low:
        zone = "BREAKOUT_LOW"
    else:
        zone = "CONSOLIDATION_RANGE"

    return {
        "current_price": round(current_price, 3),
        "pivot_point": round(pivot, 3),
        "classic": {
            "r3": round(r3, 3),
            "r2": round(r2, 3),
            "r1": round(r1, 3),
            "pivot": round(pivot, 3),
            "s1": round(s1, 3),
            "s2": round(s2, 3),
            "s3": round(s3, 3),
        },
        "fibonacci": {
            "r3": round(fib_r3, 3),
            "r2": round(fib_r2, 3),
            "r1": round(fib_r1, 3),
            "pivot": round(pivot, 3),
            "s1": round(fib_s1, 3),
            "s2": round(fib_s2, 3),
            "s3": round(fib_s3, 3),
        },
        "swing_levels": {
            "swing_high": round(swing_high, 3),
            "swing_low": round(swing_low, 3),
        },
        "nearest_resistance": round(nearest_res, 3),
        "nearest_support": round(nearest_sup, 3),
        "distance_to_resistance": dist_to_res,
        "distance_to_support": dist_to_sup,
        "zone": zone,
    }


def analyze_timeframe_trend(df: pd.DataFrame, timeframe_label: str = "1h") -> Dict[str, Any]:
    """Analyze trend direction and momentum for a specific timeframe."""
    if len(df) < 20:
        return {
            "timeframe": timeframe_label,
            "trend": "UNKNOWN",
            "rsi": None,
            "atr": None,
            "ema9": None,
            "ema21": None,
        }

    df_ind = add_indicators(df)
    latest = df_ind.iloc[-1]

    price = float(latest["close"])
    ema9 = float(latest["ema9"]) if not pd.isna(latest.get("ema9")) else price
    ema21 = float(latest["ema21"]) if not pd.isna(latest.get("ema21")) else price
    ema50 = float(latest["ema50"]) if not pd.isna(latest.get("ema50")) else price
    rsi = float(latest["rsi14"]) if not pd.isna(latest.get("rsi14")) else 50.0
    atr = float(latest["atr14"]) if not pd.isna(latest.get("atr14")) else 2.0

    # Trend logic
    if ema9 > ema21 and price >= ema50:
        trend = "BULLISH"
    elif ema9 < ema21 and price <= ema50:
        trend = "BEARISH"
    elif ema9 > ema21:
        trend = "BULLISH_WEAK"
    elif ema9 < ema21:
        trend = "BEARISH_WEAK"
    else:
        trend = "NEUTRAL"

    return {
        "timeframe": timeframe_label,
        "trend": trend,
        "price": round(price, 3),
        "ema9": round(ema9, 3),
        "ema21": round(ema21, 3),
        "ema50": round(ema50, 3),
        "rsi14": round(rsi, 2),
        "atr14": round(atr, 3),
    }


def generate_multi_timeframe_signal(
    entry_df: pd.DataFrame,
    htf_df: Optional[pd.DataFrame] = None,
    entry_tf: str = "5min",
    htf_tf: str = "1h",
) -> Dict[str, Any]:
    """
    Perform multi-timeframe analysis:
    - HTF (1H / 4H / 1D): Macro trend, Support and Resistance
    - LTF (5min / 15min): Micro entry trigger (EMA9/21 cross, RSI, ATR)
    - Auto Setup: Generates SL, TP1, TP2, TP3, Risk:Reward, and confidence
    """
    if len(entry_df) < 30:
        return {
            "signal": "WAIT",
            "reason": "Not enough entry timeframe candles",
            "has_setup": False,
            "setup": None,
        }

    entry_df_ind = add_indicators(entry_df)
    latest = entry_df_ind.iloc[-1]

    price = float(latest["close"])
    ema9 = float(latest["ema9"])
    ema21 = float(latest["ema21"])
    rsi = float(latest["rsi14"])
    atr = float(latest["atr14"]) if latest["atr14"] > 0 else 2.5

    # Higher Timeframe analysis
    htf_analysis = analyze_timeframe_trend(htf_df, htf_tf) if htf_df is not None and len(htf_df) >= 20 else {
        "timeframe": htf_tf,
        "trend": "NEUTRAL",
        "price": price,
        "rsi14": rsi,
        "atr14": atr,
    }

    # Support and Resistance levels
    sr_levels = calculate_support_resistance(entry_df, htf_df)

    htf_trend = htf_analysis.get("trend", "NEUTRAL")
    signal = "WAIT"
    reasons: List[str] = []
    confidence = "LOW"

    # Multi-timeframe confluence logic
    # BUY conditions:
    # 1. Entry EMA9 > EMA21
    # 2. Entry RSI between 45 and 70 (healthy momentum, not overbought)
    # 3. HTF trend is BULLISH or BULLISH_WEAK or NEUTRAL
    if ema9 > ema21 and 45.0 <= rsi <= 72.0:
        if "BULLISH" in htf_trend:
            signal = "BUY"
            confidence = "HIGH"
            reasons.append(f"Strong bullish confluence: HTF ({htf_tf}) trend is {htf_trend}")
            reasons.append(f"Entry ({entry_tf}) EMA9 ({round(ema9, 2)}) > EMA21 ({round(ema21, 2)})")
            reasons.append(f"RSI ({round(rsi, 2)}) confirms upward momentum")
        elif htf_trend == "NEUTRAL":
            signal = "BUY"
            confidence = "MEDIUM"
            reasons.append(f"Entry ({entry_tf}) bullish EMA momentum; HTF ({htf_tf}) is neutral/ranging")
            reasons.append(f"RSI ({round(rsi, 2)}) indicates positive buying pressure")

    # SELL conditions:
    # 1. Entry EMA9 < EMA21
    # 2. Entry RSI between 28 and 55 (bearish momentum, not oversold)
    # 3. HTF trend is BEARISH or BEARISH_WEAK or NEUTRAL
    elif ema9 < ema21 and 28.0 <= rsi <= 55.0:
        if "BEARISH" in htf_trend:
            signal = "SELL"
            confidence = "HIGH"
            reasons.append(f"Strong bearish confluence: HTF ({htf_tf}) trend is {htf_trend}")
            reasons.append(f"Entry ({entry_tf}) EMA9 ({round(ema9, 2)}) < EMA21 ({round(ema21, 2)})")
            reasons.append(f"RSI ({round(rsi, 2)}) confirms downward momentum")
        elif htf_trend == "NEUTRAL":
            signal = "SELL"
            confidence = "MEDIUM"
            reasons.append(f"Entry ({entry_tf}) bearish EMA momentum; HTF ({htf_tf}) is neutral/ranging")
            reasons.append(f"RSI ({round(rsi, 2)}) indicates selling pressure")

    # If no pure confluence, check bounce off S&R
    if signal == "WAIT":
        if sr_levels.get("zone") == "TESTING_SUPPORT" and ema9 >= ema21 and rsi >= 40:
            signal = "BUY"
            confidence = "MEDIUM"
            reasons.append(f"Bounce setup: Price testing support ({sr_levels.get('nearest_support')}) with bullish recovery")
        elif sr_levels.get("zone") == "TESTING_RESISTANCE" and ema9 <= ema21 and rsi <= 60:
            signal = "SELL"
            confidence = "MEDIUM"
            reasons.append(f"Rejection setup: Price testing resistance ({sr_levels.get('nearest_resistance')}) with bearish rejection")
        else:
            reasons.append(f"No clear setup: LTF EMA spread is {'bullish' if ema9 > ema21 else 'bearish'} but HTF ({htf_tf}) is {htf_trend} and RSI is {round(rsi, 1)}")

    # Calculate Stop Loss and Take Profit
    atr_mult = float(os.getenv("ATR_STOP_MULTIPLIER", "1.5"))
    rr_ratio = float(os.getenv("RISK_REWARD_RATIO", "2.0"))

    has_setup = signal in ("BUY", "SELL")
    setup = None

    if has_setup:
        dist = atr * atr_mult
        nearest_sup = sr_levels.get("nearest_support", price - dist)
        nearest_res = sr_levels.get("nearest_resistance", price + dist)

        if signal == "BUY":
            # SL is placed just below nearest support or ATR distance
            stop_loss = min(price - dist, nearest_sup - (atr * 0.2)) if nearest_sup < price else (price - dist)
            actual_sl_dist = price - stop_loss
            tp1 = price + (actual_sl_dist * 1.5)
            tp2 = price + (actual_sl_dist * rr_ratio)
            tp3 = max(nearest_res, price + (actual_sl_dist * 3.0))
            take_profit = tp2
        else:
            # SL is placed just above nearest resistance or ATR distance
            stop_loss = max(price + dist, nearest_res + (atr * 0.2)) if nearest_res > price else (price + dist)
            actual_sl_dist = stop_loss - price
            tp1 = price - (actual_sl_dist * 1.5)
            tp2 = price - (actual_sl_dist * rr_ratio)
            tp3 = min(nearest_sup, price - (actual_sl_dist * 3.0))
            take_profit = tp2

        setup = {
            "status": "ACTIVE_SETUP",
            "direction": signal,
            "entry_price": round(price, 3),
            "stop_loss": round(stop_loss, 3),
            "take_profit": round(take_profit, 3),
            "sl": round(stop_loss, 3),
            "tp": round(take_profit, 3),
            "tp1": round(tp1, 3),
            "tp2": round(tp2, 3),
            "tp3": round(tp3, 3),
            "risk_reward_ratio": rr_ratio,
            "risk_distance": round(abs(price - stop_loss), 3),
            "reward_distance": round(abs(take_profit - price), 3),
            "confidence": confidence,
            "reasons": reasons,
        }

    return {
        "signal": signal,
        "reason": "; ".join(reasons) if reasons else "No matching entry conditions",
        "price": round(price, 3),
        "entry_timeframe": entry_tf,
        "analysis_timeframe": htf_tf,
        "has_setup": has_setup,
        "setup": setup,
        "take_profit": setup["tp"] if setup else None,
        "stop_loss": setup["sl"] if setup else None,
        "tp": setup["tp"] if setup else None,
        "sl": setup["sl"] if setup else None,
        "technical_indicators": {
            "ema9": round(ema9, 3),
            "ema21": round(ema21, 3),
            "rsi14": round(rsi, 2),
            "atr14": round(atr, 3),
        },
        "support_resistance": sr_levels,
        "higher_timeframe_analysis": htf_analysis,
    }


def generate_signal(df: pd.DataFrame) -> dict:
    """
    Standard single-timeframe signal generator with Support & Resistance and SL/TP auto-setup.
    Maintained for backward compatibility.
    """
    if len(df) < 50:
        return {
            "signal": "WAIT",
            "reason": "Not enough historical candles",
            "has_setup": False,
            "setup": None,
        }

    return generate_multi_timeframe_signal(
        entry_df=df,
        htf_df=None,
        entry_tf="5min",
        htf_tf="1h",
    )


def format_candles_for_frontend(df: pd.DataFrame, limit: int = 60) -> List[Dict[str, Any]]:
    """
    Format OHLC candlestick records with timestamps and indicator overlays
    for direct use by frontend charting libraries (TradingView Lightweight Charts, ApexCharts, etc.).
    """
    if df is None or df.empty:
        return []

    # Calculate indicators if not already computed
    df_calc = add_indicators(df)
    subset = df_calc.tail(min(limit, len(df_calc))).copy()

    candles = []
    for _, row in subset.iterrows():
        dt_val = row["datetime"]
        # Convert datetime to Unix timestamp (seconds) and readable ISO/string
        if hasattr(dt_val, "timestamp"):
            ts = int(dt_val.timestamp())
            dt_str = dt_val.strftime("%Y-%m-%d %H:%M:%S")
        else:
            try:
                parsed_dt = pd.to_datetime(dt_val)
                ts = int(parsed_dt.timestamp())
                dt_str = parsed_dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = 0
                dt_str = str(dt_val)

        candle = {
            "time": ts,
            "datetime": dt_str,
            "open": round(float(row["open"]), 3),
            "high": round(float(row["high"]), 3),
            "low": round(float(row["low"]), 3),
            "close": round(float(row["close"]), 3),
            "ema9": round(float(row["ema9"]), 3) if ("ema9" in row and not pd.isna(row["ema9"])) else None,
            "ema21": round(float(row["ema21"]), 3) if ("ema21" in row and not pd.isna(row["ema21"])) else None,
            "ema50": round(float(row["ema50"]), 3) if ("ema50" in row and not pd.isna(row["ema50"])) else None,
            "rsi": round(float(row["rsi14"]), 2) if ("rsi14" in row and not pd.isna(row["rsi14"])) else None,
            "atr": round(float(row["atr14"]), 3) if ("atr14" in row and not pd.isna(row["atr14"])) else None,
        }
        candles.append(candle)

    return candles