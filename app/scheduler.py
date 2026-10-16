import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from app.market_data import get_gold_candles
from app.indicators import generate_multi_timeframe_signal
from app.news_filter import check_news_filter
from app.risk import calculate_risk_levels
from app.trade_manager import process_trade_signal, load_active_trade

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "analysis.jsonl"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("gold-scheduler")

LATEST_ANALYSIS: Optional[Dict[str, Any]] = None


def get_latest_analysis() -> Optional[Dict[str, Any]]:
    global LATEST_ANALYSIS
    if LATEST_ANALYSIS is not None:
        return LATEST_ANALYSIS

    # Fallback to reading the last line of the log file if available
    if LOG_FILE.exists():
        try:
            with LOG_FILE.open("r", encoding="utf-8") as file:
                lines = [line.strip() for line in file if line.strip()]
                if lines:
                    return json.loads(lines[-1])
        except Exception:
            pass

    return None


def scheduled_analysis():
    """
    Fetch multi-timeframe gold candles (1H analysis + 5min entry),
    evaluate Support & Resistance, enforce confirmed SL/TP,
    prevent duplicate entries, manage active trades, and log analysis.
    """
    global LATEST_ANALYSIS
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        logger.info("Starting scheduled XAU/USD multi-timeframe analysis")

        # Fetch 5min entry candles and 1h macro candles
        entry_candles = get_gold_candles(interval="5min", outputsize=100)
        htf_candles = None
        try:
            htf_candles = get_gold_candles(interval="1h", outputsize=100)
        except Exception as e:
            logger.warning("Could not fetch 1h HTF candles: %s", e)

        # Technical multi-timeframe signal & Support/Resistance
        technical = generate_multi_timeframe_signal(
            entry_df=entry_candles,
            htf_df=htf_candles,
            entry_tf="5min",
            htf_tf="1h",
        )

        news = check_news_filter()
        news_analysis = news.get("news_analysis")

        raw_signal = technical.get("signal", "WAIT")
        price = technical.get("price")
        atr = technical.get("technical_indicators", {}).get("atr14")
        sr_levels = technical.get("support_resistance", {})
        has_setup = technical.get("has_setup", False)

        signal = raw_signal
        reason = technical.get("reason", "No valid signal")

        # News filter always takes execution priority
        news_blocked = not news.get("allowed", False)
        if news_blocked:
            signal = "WAIT"
            reason = f"Execution suspended by news filter: {news.get('reason')}"

        # Calculate risk levels (SL, TP1, TP2, TP3)
        risk_levels = calculate_risk_levels(
            signal=raw_signal if raw_signal in ("BUY", "SELL") else None,
            price=price,
            atr=atr,
            support_resistance=sr_levels,
        )

        # Extract potential setup SL and TP
        potential_sl = risk_levels.get("sl") if risk_levels else None
        potential_tp = risk_levels.get("tp") if risk_levels else None
        potential_tp1 = risk_levels.get("tp1") if risk_levels else None
        potential_tp2 = risk_levels.get("tp2") if risk_levels else None
        potential_tp3 = risk_levels.get("tp3") if risk_levels else None

        # Execute trade guard: Prevent duplicate entries and require confirmed SL/TP
        trade_exec = process_trade_signal(
            current_price=price,
            signal=signal,
            sl=potential_sl,
            tp=potential_tp,
            reason=reason,
            timeframe="5min",
            tp1=potential_tp1,
            tp2=potential_tp2,
            tp3=potential_tp3,
            candles_df=entry_candles,
        )

        # Handle trade closure (SL hit or TP hit)
        trade_action = trade_exec.get("action", "")
        trade_status = trade_exec.get("trade_status", "")
        is_closed = trade_action in ("CLOSE_SL", "CLOSE_TP") or trade_status in ("CLOSED_SL", "CLOSED_TP")

        # Use effective SL and TP only if an active position exists or a new entry is confirmed
        if is_closed and not trade_exec.get("is_new_entry"):
            effective_sl = None
            effective_tp = None
            setup = None
            has_setup = False
        else:
            effective_sl = trade_exec.get("sl", potential_sl)
            effective_tp = trade_exec.get("tp", potential_tp)

            # Build setup card only if there is an active position or confirmed new entry
            setup = None
            if (technical.get("setup") or risk_levels) and (trade_exec.get("is_new_entry") or trade_status == "ACTIVE_POSITION"):
                base_setup = technical.get("setup") or {}
                if trade_status == "ACTIVE_POSITION":
                    effective_tp1 = trade_exec.get("tp1")
                    effective_tp2 = trade_exec.get("tp2") or effective_tp
                    effective_tp3 = trade_exec.get("tp3")
                else:
                    effective_tp1 = trade_exec.get("tp1") or (risk_levels.get("tp1") if risk_levels else base_setup.get("tp1"))
                    effective_tp2 = trade_exec.get("tp2") or (risk_levels.get("tp2") if risk_levels else base_setup.get("tp2")) or effective_tp
                    effective_tp3 = trade_exec.get("tp3") or (risk_levels.get("tp3") if risk_levels else base_setup.get("tp3"))

                setup = {
                    "status": "PENDING_NEWS_CLEARANCE" if news_blocked else trade_status or "ACTIVE",
                    "direction": raw_signal,
                    "entry_price": trade_exec.get("entry_price", price),
                    "stop_loss": effective_sl,
                    "take_profit": effective_tp,
                    "sl": effective_sl,
                    "tp": effective_tp,
                    "tp1": effective_tp1,
                    "tp2": effective_tp2,
                    "tp3": effective_tp3,
                    "risk_reward_ratio": risk_levels.get("risk_reward_ratio", 2.0) if risk_levels else 2.0,
                    "confidence": base_setup.get("confidence", "MEDIUM"),
                    "reasons": base_setup.get("reasons", [reason]),
                }
            else:
                has_setup = False

        result = {
            "timestamp_utc": started_at,
            "symbol": "XAU/USD",
            "entry_timeframe": "5min",
            "analysis_timeframe": "1h",
            "signal": signal,
            "action": trade_exec.get("action"),
            "is_new_entry": trade_exec.get("is_new_entry"),
            "duplicate_prevented": trade_exec.get("duplicate_prevented"),
            "trade_status": trade_exec.get("trade_status"),
            "message": trade_exec.get("message"),
            "reason": reason,
            "price": price,
            "has_setup": bool(setup),
            "take_profit": effective_tp,
            "stop_loss": effective_sl,
            "tp": effective_tp,
            "sl": effective_sl,
            "active_position": trade_exec.get("active_position"),
            "setup": setup,
            "support_resistance": sr_levels,
            "multi_timeframe": {
                "htf_trend": technical.get("higher_timeframe_analysis", {}).get("trend"),
                "htf_rsi": technical.get("higher_timeframe_analysis", {}).get("rsi14"),
                "entry_signal": raw_signal,
            },
            "news_filter": news,
            "news_analysis": news_analysis,
            "trading_mode": "analysis_only",
            "real_orders_enabled": False,
        }

        LATEST_ANALYSIS = result

        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(json.dumps(result, default=str) + "\n")

        # Store analysis result in MongoDB
        try:
            from app.mongo_db import save_analysis
            save_analysis(result)
        except Exception as mongo_err:
            logger.debug("MongoDB analysis storage skipped: %s", mongo_err)

        logger.info(
            "Analysis complete: action=%s signal=%s is_new_entry=%s price=%s TP=%s SL=%s duplicate_prevented=%s msg=%s",
            trade_exec.get("action"),
            signal,
            trade_exec.get("is_new_entry"),
            price,
            effective_tp,
            effective_sl,
            trade_exec.get("duplicate_prevented"),
            trade_exec.get("message"),
        )

        return result

    except Exception:
        logger.exception("Scheduled analysis failed; no trade was placed")
        return None
