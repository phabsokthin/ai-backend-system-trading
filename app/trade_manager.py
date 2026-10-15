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
    """Record completed trade to trade_history.jsonl."""
    try:
        with TRADE_HISTORY_FILE.open("a", encoding="utf-8") as file:
            file.write(json.dumps(trade, default=str) + "\n")
    except Exception as exc:
        logger.error("Failed archiving trade: %s", exc)


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
) -> Dict[str, Any]:
    """
    Main trade execution guard:
    1. Prevents duplicate entries on consecutive cycles.
    2. Blocks entries if SL or TP are missing / invalid.
    3. Manages active trade lifecycle (HOLD, CLOSE on TP, CLOSE on SL).
    4. Formats clean JSON status messages.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    active_trade = load_active_trade()

    # Case 1: An active trade is currently open
    if active_trade is not None and active_trade.get("status") == "OPEN":
        direction = active_trade["direction"]
        entry_price = float(active_trade["entry_price"])
        trade_sl = float(active_trade["stop_loss"])
        trade_tp = float(active_trade["take_profit"])

        # Calculate current unrealized PnL in points
        if direction == "BUY":
            pnl_points = round(current_price - entry_price, 3)
            hit_tp = current_price >= trade_tp
            hit_sl = current_price <= trade_sl
        else:  # SELL
            pnl_points = round(entry_price - current_price, 3)
            hit_tp = current_price <= trade_tp
            hit_sl = current_price >= trade_sl

        # Check if Take Profit was reached
        if hit_tp:
            active_trade["status"] = "CLOSED_TP"
            active_trade["exit_price"] = current_price
            active_trade["exit_time"] = now_utc
            active_trade["final_pnl_points"] = pnl_points
            active_trade["exit_reason"] = f"Take Profit reached at {current_price:.3f}"

            archive_closed_trade(active_trade)
            save_active_trade(None)

            return {
                "action": "CLOSE_TP",
                "is_new_entry": False,
                "duplicate_prevented": False,
                "signal": signal,
                "trade_status": "CLOSED_TP",
                "entry_price": entry_price,
                "exit_price": current_price,
                "stop_loss": trade_sl,
                "take_profit": trade_tp,
                "sl": trade_sl,
                "tp": trade_tp,
                "pnl_points": pnl_points,
                "active_position": None,
                "message": f"✅ TAKE PROFIT REACHED: {direction} closed at {current_price:.3f} (+{pnl_points:.2f} pts profit).",
            }

        # Check if Stop Loss was hit
        if hit_sl:
            active_trade["status"] = "CLOSED_SL"
            active_trade["exit_price"] = current_price
            active_trade["exit_time"] = now_utc
            active_trade["final_pnl_points"] = pnl_points
            active_trade["exit_reason"] = f"Stop Loss hit at {current_price:.3f}"

            archive_closed_trade(active_trade)
            save_active_trade(None)

            return {
                "action": "CLOSE_SL",
                "is_new_entry": False,
                "duplicate_prevented": False,
                "signal": signal,
                "trade_status": "CLOSED_SL",
                "entry_price": entry_price,
                "exit_price": current_price,
                "stop_loss": trade_sl,
                "take_profit": trade_tp,
                "sl": trade_sl,
                "tp": trade_tp,
                "pnl_points": pnl_points,
                "active_position": None,
                "message": f"🛑 STOP LOSS HIT: {direction} stopped out at {current_price:.3f} ({pnl_points:.2f} pts loss).",
            }

        # Trade is still active!
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


def reset_active_trade() -> Dict[str, Any]:
    """Manual emergency reset of active position."""
    save_active_trade(None)
    return {"status": "ok", "message": "Active position reset to flat"}
