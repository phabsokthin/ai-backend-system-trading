import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("trade-manager")

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
ACTIVE_TRADE_FILE = LOG_DIR / "active_trade.json"
TRADE_HISTORY_FILE = LOG_DIR / "trade_history.jsonl"

LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_active_trade() -> Optional[Dict[str, Any]]:
    """Load the currently active trade from disk if one exists."""
    if not ACTIVE_TRADE_FILE.exists():
        return None
    try:
        content = ACTIVE_TRADE_FILE.read_text(encoding="utf-8").strip()
        if content:
            trade = json.loads(content)
            if trade.get("status") == "OPEN":
                return trade
    except Exception as exc:
        logger.warning("Could not read active trade file: %s", exc)
    return None


def save_active_trade(trade: Optional[Dict[str, Any]]) -> None:
    """Save or clear the currently active trade."""
    try:
        if trade is None or trade.get("status") != "OPEN":
            if ACTIVE_TRADE_FILE.exists():
                ACTIVE_TRADE_FILE.unlink(missing_ok=True)
        else:
            ACTIVE_TRADE_FILE.write_text(json.dumps(trade, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to save active trade: %s", exc)


def archive_closed_trade(trade: Dict[str, Any]) -> None:
    """Record completed trade to trade_history.jsonl and MongoDB."""
    try:
        with TRADE_HISTORY_FILE.open("a", encoding="utf-8") as file:
            file.write(json.dumps(trade, default=str) + "\n")
    except Exception as exc:
        logger.error("Failed archiving trade: %s", exc)

    try:
        from app.mongo_db import save_trade_to_db
        save_trade_to_db(trade)
    except Exception as db_exc:
        logger.debug("Failed archiving trade to MongoDB: %s", db_exc)


def validate_sl_tp(direction: str, entry_price: float, sl: Optional[float], tp: Optional[float]) -> tuple[bool, str]:
    """Strictly verify that Stop Loss and Take Profit are present and logically sound."""
    if sl is None or tp is None:
        return False, "Entry blocked: Stop Loss (SL) and Take Profit (TP) must not be hidden or empty."

    try:
        sl_val = float(sl)
        tp_val = float(tp)
        entry_val = float(entry_price)
    except (ValueError, TypeError):
        return False, "Entry blocked: Invalid numeric format for SL or TP."

    if sl_val <= 0 or tp_val <= 0 or entry_val <= 0:
        return False, "Entry blocked: Entry, SL, and TP must be positive price values."

    if direction == "BUY":
        if sl_val >= entry_val:
            return False, f"Entry blocked: For BUY, Stop Loss ({sl_val}) must be below Entry price ({entry_val})."
        if tp_val <= entry_val:
            return False, f"Entry blocked: For BUY, Take Profit ({tp_val}) must be above Entry price ({entry_val})."

    elif direction == "SELL":
        if sl_val <= entry_val:
            return False, f"Entry blocked: For SELL, Stop Loss ({sl_val}) must be above Entry price ({entry_val})."
        if tp_val >= entry_val:
            return False, f"Entry blocked: For SELL, Take Profit ({tp_val}) must be below Entry price ({entry_val})."

    else:
        return False, f"Unknown trade direction: {direction}"

    return True, "SL and TP validated successfully."


def process_trade_signal(
    current_price: float,
    signal: str,
    sl: Optional[float],
    tp: Optional[float],
    reason: str,
    timeframe: str = "5min",
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    tp3: Optional[float] = None,
    candles_df: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Main trade execution guard:
    1. Prevents duplicate entries on consecutive cycles.
    2. Blocks entries and active holding if SL or TP are missing / hidden / invalid.
    3. Checks current price AND candlestick history since entry_time for SL or TP hits.
    4. Automatically closes trade on SL/TP hit, archives it, and returns system to FLAT/WAIT.
    5. Formats clean JSON status messages.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    active_trade = load_active_trade()

    # Case 1: An active trade is currently open
    if active_trade is not None and active_trade.get("status") == "OPEN":
        direction = active_trade.get("direction", "BUY").upper()
        entry_price = float(active_trade["entry_price"])
        raw_sl = active_trade.get("stop_loss") if active_trade.get("stop_loss") is not None else active_trade.get("sl")
        raw_tp = active_trade.get("take_profit") if active_trade.get("take_profit") is not None else active_trade.get("tp")

        # RULE: Setup cannot hold if Stop Loss or Take Profit is hidden, missing, or invalid (<= 0)
        if raw_sl is None or raw_tp is None:
            logger.warning("Active trade %s has hidden/missing SL or TP. Closing immediately.", active_trade.get("id"))
            active_trade["status"] = "CLOSED_INVALID"
            active_trade["exit_price"] = current_price
            active_trade["exit_time"] = now_utc
            active_trade["exit_reason"] = "Stop Loss or Take Profit was hidden or empty."
            archive_closed_trade(active_trade)
            save_active_trade(None)
            return {
                "action": "CLOSE_INVALID",
                "is_new_entry": False,
                "duplicate_prevented": False,
                "signal": "WAIT",
                "trade_status": "FLAT",
                "entry_price": entry_price,
                "exit_price": current_price,
                "stop_loss": None,
                "take_profit": None,
                "sl": None,
                "tp": None,
                "pnl_points": 0.0,
                "active_position": None,
                "message": "⚠️ Active setup cannot hold without confirmed SL and TP. Position closed, waiting for new setup.",
            }

        trade_sl = float(raw_sl)
        trade_tp = float(raw_tp)

        # Evaluate if SL or TP was reached (by current live price OR by candlestick history since entry)
        hit_tp = False
        hit_sl = False
        exit_price = current_price
        exit_time = now_utc
        exit_reason = ""

        # 1. Instant check against current live price
        if direction == "BUY":
            if current_price <= trade_sl:
                hit_sl = True
                exit_price = trade_sl
                exit_reason = f"Stop Loss hit at {current_price:.3f} (SL: {trade_sl:.3f})"
            elif current_price >= trade_tp:
                hit_tp = True
                exit_price = trade_tp
                exit_reason = f"Take Profit reached at {current_price:.3f} (TP: {trade_tp:.3f})"
        else:  # SELL
            if current_price >= trade_sl:
                hit_sl = True
                exit_price = trade_sl
                exit_reason = f"Stop Loss hit at {current_price:.3f} (SL: {trade_sl:.3f})"
            elif current_price <= trade_tp:
                hit_tp = True
                exit_price = trade_tp
                exit_reason = f"Take Profit reached at {current_price:.3f} (TP: {trade_tp:.3f})"

        # 2. Historical candlestick check since entry_time
        # In real trading, if any candle wick crossed SL or TP between cycles or during downtime, the trade was executed!
        if not hit_sl and not hit_tp:
            entry_time_str = active_trade.get("entry_time")
            eval_df = candles_df

            # If candles_df was not passed, dynamically fetch recent market candles to audit the position
            if eval_df is None and entry_time_str:
                try:
                    import pandas as pd
                    from app.market_data import get_gold_candles
                    entry_dt = pd.to_datetime(entry_time_str, utc=True)
                    hours_elapsed = (datetime.now(timezone.utc) - entry_dt.to_pydatetime()).total_seconds() / 3600.0
                    if hours_elapsed > 4:
                        eval_df = get_gold_candles(interval="15min", outputsize=min(max(int(hours_elapsed * 4) + 30, 60), 300))
                    else:
                        eval_df = get_gold_candles(interval=timeframe or "5min", outputsize=100)
                except Exception as exc:
                    logger.debug("Candle audit fetch skipped: %s", exc)

            if eval_df is not None:
                try:
                    import pandas as pd
                    entry_dt = pd.to_datetime(entry_time_str, utc=True)
                    df_copy = eval_df.copy()
                    if "datetime" in df_copy.columns:
                        df_copy["_parsed_dt"] = pd.to_datetime(df_copy["datetime"], utc=True)
                        df_sorted = df_copy.sort_values("_parsed_dt")
                        for _, row in df_sorted.iterrows():
                            if row["_parsed_dt"] >= entry_dt:
                                c_low = float(row["low"])
                                c_high = float(row["high"])
                                c_time = str(row["datetime"])

                                if direction == "BUY":
                                    if c_low <= trade_sl:
                                        hit_sl = True
                                        exit_price = trade_sl
                                        exit_time = c_time
                                        exit_reason = f"Stop Loss hit at {trade_sl:.3f} on candle {c_time} (Low was {c_low:.3f})"
                                        break
                                    elif c_high >= trade_tp:
                                        hit_tp = True
                                        exit_price = trade_tp
                                        exit_time = c_time
                                        exit_reason = f"Take Profit reached at {trade_tp:.3f} on candle {c_time} (High was {c_high:.3f})"
                                        break
                                else:  # SELL
                                    if c_high >= trade_sl:
                                        hit_sl = True
                                        exit_price = trade_sl
                                        exit_time = c_time
                                        exit_reason = f"Stop Loss hit at {trade_sl:.3f} on candle {c_time} (High was {c_high:.3f})"
                                        break
                                    elif c_low <= trade_tp:
                                        hit_tp = True
                                        exit_price = trade_tp
                                        exit_time = c_time
                                        exit_reason = f"Take Profit reached at {trade_tp:.3f} on candle {c_time} (Low was {c_low:.3f})"
                                        break
                except Exception as eval_err:
                    logger.warning("Error checking candlestick history for SL/TP: %s", eval_err)

        # Check if Take Profit was reached
        if hit_tp:
            pnl_points = round(exit_price - entry_price, 3) if direction == "BUY" else round(entry_price - exit_price, 3)
            active_trade["status"] = "CLOSED_TP"
            active_trade["exit_price"] = exit_price
            active_trade["exit_time"] = exit_time
            active_trade["final_pnl_points"] = pnl_points
            active_trade["exit_reason"] = exit_reason or f"Take Profit reached at {exit_price:.3f}"

            archive_closed_trade(active_trade)
            save_active_trade(None)

            logger.info("Closed %s on TP: %s", direction, active_trade["exit_reason"])

            return {
                "action": "CLOSE_TP",
                "is_new_entry": False,
                "duplicate_prevented": False,
                "signal": "WAIT",
                "trade_status": "CLOSED_TP",
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stop_loss": trade_sl,
                "take_profit": trade_tp,
                "sl": trade_sl,
                "tp": trade_tp,
                "pnl_points": pnl_points,
                "active_position": None,
                "message": f"✅ TAKE PROFIT REACHED: {direction} closed at {exit_price:.3f} (+{pnl_points:.2f} pts profit). Waiting for new setup.",
            }

        # Check if Stop Loss was hit
        if hit_sl:
            pnl_points = round(exit_price - entry_price, 3) if direction == "BUY" else round(entry_price - exit_price, 3)
            active_trade["status"] = "CLOSED_SL"
            active_trade["exit_price"] = exit_price
            active_trade["exit_time"] = exit_time
            active_trade["final_pnl_points"] = pnl_points
            active_trade["exit_reason"] = exit_reason or f"Stop Loss hit at {exit_price:.3f}"

            archive_closed_trade(active_trade)
            save_active_trade(None)

            logger.info("Closed %s on SL: %s", direction, active_trade["exit_reason"])

            return {
                "action": "CLOSE_SL",
                "is_new_entry": False,
                "duplicate_prevented": False,
                "signal": "WAIT",
                "trade_status": "CLOSED_SL",
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stop_loss": trade_sl,
                "take_profit": trade_tp,
                "sl": trade_sl,
                "tp": trade_tp,
                "pnl_points": pnl_points,
                "active_position": None,
                "message": f"🛑 STOP LOSS HIT: {direction} stopped out at {exit_price:.3f} ({pnl_points:.2f} pts loss). Waiting for new setup.",
            }

        # Trade is still active!
        pnl_points = round(current_price - entry_price, 3) if direction == "BUY" else round(entry_price - current_price, 3)

        # Check if the incoming signal is the same (re-confirmation)
        if signal == direction:
            # Re-confirmation: prevent duplicate entry
            message = (
                f"HOLD {direction}: Active position open from {entry_price:.3f}. "
                f"Current: {current_price:.3f} (PnL: {pnl_points:+.2f} pts). "
                f"Signal re-confirmed, duplicate entry blocked."
            )
        elif signal in ("BUY", "SELL") and signal != direction:
            # Reversal signal detected while in trade
            message = (
                f"HOLD {direction}: Active position open from {entry_price:.3f} (PnL: {pnl_points:+.2f} pts). "
                f"Opposite signal ({signal}) appeared, monitoring SL ({trade_sl:.3f}) and TP ({trade_tp:.3f})."
            )
        else:
            message = (
                f"HOLD {direction}: Position running from {entry_price:.3f} (PnL: {pnl_points:+.2f} pts). "
                f"SL: {trade_sl:.3f}, TP: {trade_tp:.3f}."
            )

        # Ensure consistent TP1, TP2, TP3 for the active position
        actual_risk = max(abs(entry_price - trade_sl), 0.5)
        tp1 = active_trade.get("tp1")
        tp2 = active_trade.get("tp2") or trade_tp
        tp3 = active_trade.get("tp3")

        is_consistent = False
        if tp1 is not None and tp2 is not None and tp3 is not None:
            if direction == "BUY" and entry_price < tp1 < tp2 < tp3:
                is_consistent = True
            elif direction == "SELL" and entry_price > tp1 > tp2 > tp3:
                is_consistent = True

        if not is_consistent:
            if direction == "BUY":
                tp1 = round(entry_price + (actual_risk * 1.5), 3)
                tp2 = round(trade_tp, 3) if trade_tp > tp1 else round(entry_price + (actual_risk * 2.0), 3)
                tp3 = round(max(entry_price + (actual_risk * 3.0), tp2 + (actual_risk * 0.5)), 3)
            else:
                tp1 = round(entry_price - (actual_risk * 1.5), 3)
                tp2 = round(trade_tp, 3) if trade_tp < tp1 else round(entry_price - (actual_risk * 2.0), 3)
                tp3 = round(min(entry_price - (actual_risk * 3.0), tp2 - (actual_risk * 0.5)), 3)

            active_trade["tp1"] = tp1
            active_trade["tp2"] = tp2
            active_trade["tp3"] = tp3
            save_active_trade(active_trade)

        active_trade["current_price"] = current_price
        active_trade["unrealized_pnl_points"] = pnl_points
        active_trade["last_checked_at"] = now_utc

        return {
            "action": "HOLD",
            "is_new_entry": False,
            "duplicate_prevented": True,
            "signal": signal,
            "trade_status": "ACTIVE_POSITION",
            "entry_price": entry_price,
            "current_price": current_price,
            "stop_loss": trade_sl,
            "take_profit": trade_tp,
            "sl": trade_sl,
            "tp": trade_tp,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "unrealized_pnl_points": pnl_points,
            "active_position": active_trade,
            "message": message,
        }

    # Case 2: No active trade exists. Check if we should OPEN a new trade.
    if signal in ("BUY", "SELL"):
        # Rule: Must have confirmed, unhidden SL and TP
        valid, err_msg = validate_sl_tp(signal, current_price, sl, tp)
        if not valid:
            logger.warning("Rejected entry for %s at %s: %s", signal, current_price, err_msg)
            return {
                "action": "WAIT",
                "is_new_entry": False,
                "duplicate_prevented": False,
                "signal": "WAIT",
                "trade_status": "FLAT",
                "entry_price": current_price,
                "stop_loss": sl,
                "take_profit": tp,
                "sl": sl,
                "tp": tp,
                "active_position": None,
                "message": err_msg,
            }

        # Valid confirmed setup with SL and TP: OPEN NEW TRADE!
        calc_tp1 = round(float(tp1), 3) if tp1 is not None else None
        calc_tp2 = round(float(tp2), 3) if tp2 is not None else round(float(tp), 3)
        calc_tp3 = round(float(tp3), 3) if tp3 is not None else None

        new_trade = {
            "id": f"trade_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            "direction": signal,
            "entry_price": round(current_price, 3),
            "stop_loss": round(float(sl), 3),
            "take_profit": round(float(tp), 3),
            "sl": round(float(sl), 3),
            "tp": round(float(tp), 3),
            "tp1": calc_tp1,
            "tp2": calc_tp2,
            "tp3": calc_tp3,
            "timeframe": timeframe,
            "entry_time": now_utc,
            "status": "OPEN",
            "entry_reason": reason,
        }

        save_active_trade(new_trade)
        logger.info(
            "Opened new %s position at %s | SL=%s | TP=%s | TP1=%s | TP2=%s | TP3=%s",
            signal,
            current_price,
            sl,
            tp,
            calc_tp1,
            calc_tp2,
            calc_tp3,
        )

        return {
            "action": f"OPEN_{signal}",
            "is_new_entry": True,
            "duplicate_prevented": False,
            "signal": signal,
            "trade_status": "ACTIVE_POSITION",
            "entry_price": round(current_price, 3),
            "current_price": round(current_price, 3),
            "stop_loss": round(float(sl), 3),
            "take_profit": round(float(tp), 3),
            "sl": round(float(sl), 3),
            "tp": round(float(tp), 3),
            "tp1": calc_tp1,
            "tp2": calc_tp2,
            "tp3": calc_tp3,
            "active_position": new_trade,
            "message": f"🚀 NEW ENTRY: {signal} confirmed at {current_price:.3f} with confirmed SL={float(sl):.3f} and TP={float(tp):.3f}.",
        }

    # Case 3: Flat and signal is WAIT
    return {
        "action": "WAIT",
        "is_new_entry": False,
        "duplicate_prevented": False,
        "signal": "WAIT",
        "trade_status": "FLAT",
        "entry_price": current_price,
        "stop_loss": sl,
        "take_profit": tp,
        "sl": sl,
        "tp": tp,
        "active_position": None,
        "message": f"Market in WAIT mode: {reason}",
    }


def sync_active_position(
    current_price: Optional[float] = None,
    candles_df: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Audit and synchronize active trade state against latest market data.
    If the active trade hit SL or TP, it is automatically closed and archived.
    Returns the updated trade or None if flat/closed.
    """
    active_trade = load_active_trade()
    if active_trade is None or active_trade.get("status") != "OPEN":
        return {"active": False, "trade_status": "FLAT", "position": None}

    # If current_price is not provided, fetch current candle close
    if current_price is None or current_price <= 0:
        try:
            from app.market_data import get_gold_candles
            recent = get_gold_candles(interval="5min", outputsize=5)
            if recent is not None and len(recent) > 0:
                current_price = float(recent.iloc[-1]["close"])
        except Exception:
            current_price = float(active_trade.get("entry_price", 0))

    exec_result = process_trade_signal(
        current_price=current_price,
        signal="WAIT",
        sl=active_trade.get("stop_loss"),
        tp=active_trade.get("take_profit"),
        reason="Position synchronization audit",
        timeframe=active_trade.get("timeframe", "5min"),
        candles_df=candles_df,
    )

    is_still_open = exec_result.get("trade_status") == "ACTIVE_POSITION"
    return {
        "active": is_still_open,
        "trade_status": exec_result.get("trade_status", "FLAT"),
        "position": exec_result.get("active_position"),
        "exec_result": exec_result,
    }


def reset_active_trade() -> Dict[str, Any]:
    """Manual emergency reset of active position."""
    trade = load_active_trade()
    if trade:
        trade["status"] = "MANUALLY_CLOSED"
        trade["exit_time"] = datetime.now(timezone.utc).isoformat()
        trade["exit_reason"] = "Manual reset requested by user"
        archive_closed_trade(trade)
    save_active_trade(None)
    return {"status": "ok", "message": "Active position reset to flat"}

