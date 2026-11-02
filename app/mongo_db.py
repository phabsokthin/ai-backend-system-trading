import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

load_dotenv()

logger = logging.getLogger("mongo-db")

MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb://127.0.0.1:27017/",
)
MONGO_DB_NAME = os.getenv(
    "MONGO_DB_NAME",
    "ai-system-trading",
)

try:
    client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=3000,
    )
    db = client[MONGO_DB_NAME]
    trades_collection = db["trades"]
    candles_collection = db["candles"]
    signals_collection = db["signals"]
    analysis_collection = db["analysis"]
    news_collection = db["news"]
    news_events_collection = db["news_events"]
    news_analysis_collection = db["news_analysis"]

    try:
        news_events_collection.create_index([("event_id", 1)], unique=True)
        news_events_collection.create_index([("currency", 1), ("title", 1), ("date", -1)])
        news_events_collection.create_index([("date", -1)])
        news_events_collection.create_index([("time", -1)])
        news_analysis_collection.create_index([("date", -1), ("timestamp_utc", -1)])
    except Exception:
        pass
except Exception as e:
    logger.warning("Could not initialize MongoDB client: %s", e)
    client = None
    db = None
    trades_collection = None
    candles_collection = None
    signals_collection = None
    analysis_collection = None
    news_collection = None
    news_events_collection = None
    news_analysis_collection = None


_mongo_available: Optional[bool] = None
_last_check_time: float = 0.0


def is_mongodb_available(force_check: bool = False) -> bool:
    """Check if MongoDB server is reachable with caching to avoid repeated timeout delays."""
    global _mongo_available, _last_check_time
    import time
    now = time.time()
    if not force_check and _mongo_available is False and (now - _last_check_time) < 15.0:
        return False
    if client is None:
        _mongo_available = False
        _last_check_time = now
        return False
    try:
        client.admin.command("ping")
        _mongo_available = True
        _last_check_time = now
        return True
    except Exception:
        _mongo_available = False
        _last_check_time = now
        return False


def check_mongodb() -> Dict[str, Any]:
    """Verify MongoDB connectivity."""
    is_up = is_mongodb_available(force_check=True)
    if is_up:
        return {
            "connected": True,
            "database": MONGO_DB_NAME,
        }
    return {
        "connected": False,
        "database": MONGO_DB_NAME,
        "error": "MongoDB server unreachable or client not connected",
    }


def save_signal_to_db(doc: Dict[str, Any], analysis_id: Optional[str] = None) -> Optional[str]:
    """
    Save confirmed BUY or SELL signal setup to MongoDB 'signals' collection.
    Guarantees that whenever the system generates a valid BUY or SELL signal,
    the signal setup is recorded in MongoDB without being lost to deduplication.
    """
    if signals_collection is None or not is_mongodb_available():
        return None

    signal = doc.get("signal")
    if signal not in ("BUY", "SELL"):
        return None

    # Exclude flat WAIT or trade closures
    action = str(doc.get("action", ""))
    if action.startswith("CLOSE") or action == "WAIT":
        return None

    try:
        timestamp_utc = doc.get("timestamp_utc") or datetime.now(timezone.utc).isoformat()
        active_pos = doc.get("active_position") or doc.get("setup") or {}
        setup_id = active_pos.get("id") or active_pos.get("setup_id") or doc.get("setup_id")
        if not setup_id:
            candle_time = doc.get("candle_time") or timestamp_utc[:16]
            setup_id = f"setup_{candle_time}_{signal}"

        # Ensure price is present
        price = doc.get("price") or doc.get("current_price") or active_pos.get("entry_price")

        # Determine effective SL and TP
        stop_loss = doc.get("stop_loss") or doc.get("sl") or active_pos.get("stop_loss") or active_pos.get("sl")
        take_profit = doc.get("take_profit") or doc.get("tp") or active_pos.get("take_profit") or active_pos.get("tp")

        # Determine reason
        reason = (
            doc.get("reason")
            or doc.get("message")
            or active_pos.get("entry_reason")
            or ((doc.get("setup") or {}).get("reasons") or ["Technical confluence signal"])[0]
        )

        sig_doc = {
            "setup_id": setup_id,
            "analysis_id": str(analysis_id or doc.get("_id") or ""),
            "timestamp_utc": timestamp_utc,
            "symbol": doc.get("symbol", "XAU/USD"),
            "signal": signal,
            "action": doc.get("action") or f"OPEN_{signal}",
            "price": price,
            "current_price": doc.get("current_price") or price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "sl": stop_loss,
            "tp": take_profit,
            "confidence": (doc.get("setup") or {}).get("confidence", "HIGH"),
            "reason": reason,
            "setup": doc.get("setup") or active_pos,
            "is_new_entry": bool(doc.get("is_new_entry")),
            "trade_status": doc.get("trade_status", "ACTIVE_POSITION"),
            "updated_at": datetime.now(timezone.utc),
        }

        signals_collection.update_one(
            {"setup_id": setup_id},
            {
                "$set": sig_doc,
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
        logger.info("Saved signal setup %s (%s) to MongoDB signals collection", setup_id, signal)
        return setup_id
    except Exception as exc:
        logger.warning("Failed to store signal in MongoDB: %s", exc)
        return None


def save_analysis(analysis_data: Dict[str, Any], update_only: bool = False) -> Optional[str]:
    """
    Store analysis result in MongoDB 'analysis' collection without duplicates.
    Deduplicates by candle_time and consecutive identical market states.
    Guarantees that whenever a BUY or SELL signal is evaluated, it is submitted
    to the 'signals' collection.
    """
    if analysis_collection is None or not is_mongodb_available():
        return None

    try:
        doc = dict(analysis_data)
        timestamp_utc = doc.get("timestamp_utc")
        candle_time = doc.get("candle_time")
        symbol = doc.get("symbol", "XAU/USD")

        # Guarantee current_price and price are synchronized
        if "current_price" not in doc and "price" in doc:
            doc["current_price"] = doc["price"]
        elif "price" not in doc and "current_price" in doc:
            doc["price"] = doc["current_price"]

        # Guarantee reason is never empty or missing
        if not doc.get("reason"):
            doc["reason"] = (
                doc.get("message")
                or (doc.get("setup", {}) or {}).get("reasons", ["Market analysis evaluated"])[0]
                if isinstance((doc.get("setup", {}) or {}).get("reasons"), list) and (doc.get("setup", {}) or {}).get("reasons")
                else doc.get("message", "Market analysis evaluated")
            )

        # CRITICAL: Always submit BUY/SELL signals to 'signals' collection regardless of analysis deduplication!
        if doc.get("signal") in ("BUY", "SELL"):
            save_signal_to_db(doc)

        # 1. If update_only requested, update the most recent document for this symbol in-place
        if update_only:
            latest = analysis_collection.find_one({"symbol": symbol}, sort=[("timestamp_utc", -1), ("_id", -1)])
            if latest:
                analysis_collection.update_one({"_id": latest["_id"]}, {"$set": doc})
                return str(latest["_id"])

        # 2. Deduplicate by candle_time if provided
        if candle_time:
            existing_candle = analysis_collection.find_one({"symbol": symbol, "candle_time": candle_time})
            if existing_candle:
                analysis_collection.update_one({"_id": existing_candle["_id"]}, {"$set": doc})
                return str(existing_candle["_id"])

        # 3. Deduplicate exact consecutive state: if the latest analysis has same price, signal, action, and trend
        if not doc.get("is_new_entry") and not str(doc.get("action", "")).startswith("CLOSE"):
            latest = analysis_collection.find_one({"symbol": symbol}, sort=[("timestamp_utc", -1), ("_id", -1)])
            if latest and not latest.get("is_new_entry"):
                latest_p = round(float(latest.get("current_price") or latest.get("price") or 0), 2)
                doc_p = round(float(doc.get("current_price") or doc.get("price") or 0), 2)
                latest_sig = latest.get("signal")
                doc_sig = doc.get("signal")
                latest_act = latest.get("action")
                doc_act = doc.get("action")

                if latest_p == doc_p and latest_sig == doc_sig and latest_act == doc_act:
                    # Update latest record instead of creating duplicate snapshot
                    analysis_collection.update_one({"_id": latest["_id"]}, {"$set": doc})
                    return str(latest["_id"])

        # 4. Check exact timestamp
        if timestamp_utc:
            existing = analysis_collection.find_one({"timestamp_utc": timestamp_utc, "symbol": symbol})
            if existing:
                analysis_collection.update_one({"_id": existing["_id"]}, {"$set": doc})
                return str(existing["_id"])

        if "created_at" not in doc:
            doc["created_at"] = datetime.now(timezone.utc)

        result = analysis_collection.insert_one(doc)
        inserted_id = str(result.inserted_id)

        # Update signal document with analysis_id
        if doc.get("signal") in ("BUY", "SELL"):
            save_signal_to_db(doc, analysis_id=inserted_id)

        logger.info("Saved analysis to MongoDB (id=%s, signal=%s)", inserted_id, doc.get("signal"))
        return inserted_id
    except Exception as exc:
        logger.warning("Failed to store analysis in MongoDB: %s", exc)
        return None


def get_latest_analysis_from_db() -> Optional[Dict[str, Any]]:
    """Retrieve the most recent analysis record from MongoDB."""
    if analysis_collection is None:
        return None
    try:
        doc = analysis_collection.find_one(sort=[("timestamp_utc", -1), ("_id", -1)])
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
            if isinstance(doc.get("created_at"), datetime):
                doc["created_at"] = doc["created_at"].isoformat()
        return doc
    except Exception as exc:
        logger.warning("Failed retrieving latest analysis from MongoDB: %s", exc)
        return None


def get_analysis_history(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retrieve historical analysis records from MongoDB ordered DESC (newest first).
    Deduplicates both by candle timestamp slot and removes consecutive identical duplicate rows.
    """
    if analysis_collection is None:
        return []
    try:
        cursor = analysis_collection.find().sort([("timestamp_utc", -1), ("_id", -1)]).limit(limit * 4)
        results = []
        seen_candle_slots = set()

        last_p: Optional[float] = None
        last_trend: Optional[str] = None
        last_sig: Optional[str] = None

        for doc in cursor:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
            if isinstance(doc.get("created_at"), datetime):
                doc["created_at"] = doc["created_at"].isoformat()

            # Ensure current_price and reason are guaranteed
            if "current_price" not in doc and "price" in doc:
                doc["current_price"] = doc["price"]
            elif "price" not in doc and "current_price" in doc:
                doc["price"] = doc["current_price"]
            if not doc.get("reason"):
                doc["reason"] = doc.get("message") or "Analysis completed"

            p_val = round(float(doc.get("current_price") or doc.get("price") or 0), 2)
            trend_val = str((doc.get("multi_timeframe") or {}).get("htf_trend") or "")
            sig_val = str(doc.get("signal") or "")
            act_val = str(doc.get("action") or "")
            is_entry = bool(doc.get("is_new_entry"))
            is_close = act_val.startswith("CLOSE")

            # Consecutive identical state suppression (collapse multiple identical rows)
            if not is_entry and not is_close:
                if last_p is not None and last_p == p_val and last_trend == trend_val and last_sig == sig_val:
                    continue

            # Deduplicate by candle time or 5-minute candle block
            candle_time = doc.get("candle_time")
            if candle_time:
                candle_slot = f"{candle_time}_{doc.get('symbol', 'XAU/USD')}"
            else:
                ts_str = doc.get("timestamp_utc") or doc.get("created_at") or ""
                candle_slot = None
                if ts_str:
                    try:
                        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                        minute_slot = (dt.minute // 5) * 5
                        candle_slot = f"{dt.year}_{dt.month}_{dt.day}_{dt.hour}_{minute_slot}_{doc.get('symbol', 'XAU/USD')}"
                    except Exception:
                        candle_slot = ts_str

            if candle_slot and candle_slot in seen_candle_slots:
                continue
            if candle_slot:
                seen_candle_slots.add(candle_slot)

            last_p = p_val
            last_trend = trend_val
            last_sig = sig_val

            results.append(doc)
            if len(results) >= limit:
                break

        return results
    except Exception as exc:
        logger.warning("Failed retrieving analysis history from MongoDB: %s", exc)
        return []


def save_trade_to_db(trade_data: Dict[str, Any]) -> Optional[str]:
    """Store or update trade in MongoDB 'trades' collection."""
    if trades_collection is None:
        return None
    try:
        doc = dict(trade_data)
        trade_id = doc.get("id")
        if trade_id:
            res = trades_collection.update_one(
                {"id": trade_id},
                {"$set": doc, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
            return str(res.upserted_id or trade_id)
        else:
            doc["created_at"] = datetime.now(timezone.utc)
            res = trades_collection.insert_one(doc)
            return str(res.inserted_id)
    except Exception as exc:
        logger.warning("Failed saving trade to MongoDB: %s", exc)
        return None


def get_signals_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve confirmed signal setups from MongoDB 'signals' collection or fallback to analysis.jsonl."""
    if signals_collection is not None and is_mongodb_available():
        try:
            # Retrieve all confirmed trade signals (BUY / SELL)
            cursor = signals_collection.find({
                "signal": {"$in": ["BUY", "SELL"]},
                "action": {"$nin": ["WAIT", "CLOSE_SL", "CLOSE_TP", "CLOSE_INVALID", "CLOSE_OFFLINE"]}
            }).sort([("timestamp_utc", -1), ("_id", -1)]).limit(limit)
            results = []
            for doc in cursor:
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                if isinstance(doc.get("created_at"), datetime):
                    doc["created_at"] = doc["created_at"].isoformat()
                if isinstance(doc.get("updated_at"), datetime):
                    doc["updated_at"] = doc["updated_at"].isoformat()
                results.append(doc)
            return results
        except Exception as exc:
            logger.warning("Failed retrieving signals history from MongoDB: %s", exc)

    # Fallback to scanning logs/analysis.jsonl ONLY if MongoDB is disconnected / unavailable
    try:
        from pathlib import Path
        import json
        analysis_file = Path(__file__).resolve().parent.parent / "logs" / "analysis.jsonl"
        if analysis_file.exists():
            signals = []
            seen_ids = set()
            with analysis_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        is_signal = record.get("signal") in ("BUY", "SELL") and record.get("action") not in ("WAIT", "CLOSE_SL", "CLOSE_TP", "CLOSE_INVALID", "CLOSE_OFFLINE")
                        if is_signal:
                            setup_uid = (record.get("setup") or {}).get("id") or record.get("setup_id") or f"signal_{record.get('timestamp_utc', '')}_{record.get('signal')}"
                            if setup_uid in seen_ids:
                                continue
                            seen_ids.add(setup_uid)
                            sig_item = {
                                "id": setup_uid,
                                "setup_id": setup_uid,
                                "symbol": record.get("symbol", "XAU/USD"),
                                "signal": record.get("signal"),
                                "action": record.get("action"),
                                "price": record.get("price"),
                                "stop_loss": record.get("stop_loss") or record.get("sl"),
                                "take_profit": record.get("take_profit") or record.get("tp"),
                                "confidence": (record.get("setup") or {}).get("confidence", "HIGH"),
                                "reason": record.get("reason"),
                                "setup": record.get("setup"),
                                "timestamp_utc": record.get("timestamp_utc"),
                                "is_new_entry": bool(record.get("is_new_entry")),
                            }
                            signals.append(sig_item)
                    except Exception:
                        pass
            if signals:
                return list(reversed(signals))[:limit]
    except Exception as file_err:
        logger.warning("Fallback reading analysis.jsonl for signals failed: %s", file_err)

    return []


def delete_signal_from_db(signal_id: str) -> bool:
    """Delete a signal document from MongoDB by ID, setup_id, or analysis_id."""
    if signals_collection is None:
        return False
    try:
        from bson import ObjectId
        query = {
            "$or": [
                {"setup_id": signal_id},
                {"analysis_id": signal_id},
                {"id": signal_id},
            ]
        }
        try:
            query["$or"].append({"_id": ObjectId(signal_id)})
        except Exception:
            pass
        res = signals_collection.delete_many(query)
        logger.info("Deleted %d signal document(s) matching %s", res.deleted_count, signal_id)
        return res.deleted_count > 0
    except Exception as exc:
        logger.warning("Error deleting signal %s from MongoDB: %s", signal_id, exc)
        return False


def delete_trade_from_db(trade_id: str) -> bool:
    """Delete a trade document from MongoDB trades collection."""
    if trades_collection is None:
        return False
    try:
        from bson import ObjectId
        query = {"$or": [{"id": trade_id}]}
        try:
            query["$or"].append({"_id": ObjectId(trade_id)})
        except Exception:
            pass
        res = trades_collection.delete_many(query)
        logger.info("Deleted %d trade document(s) matching %s", res.deleted_count, trade_id)
        return res.deleted_count > 0
    except Exception as exc:
        logger.warning("Error deleting trade %s from MongoDB: %s", trade_id, exc)
        return False


def clear_all_signals_from_db() -> int:
    """Clear all records from signals collection."""
    if signals_collection is None:
        return 0
    try:
        res = signals_collection.delete_many({})
        return res.deleted_count
    except Exception as exc:
        logger.warning("Error clearing signals collection: %s", exc)
        return 0


def clear_all_trades_from_db() -> int:
    """Clear all records from trades collection."""
    if trades_collection is None:
        return 0
    try:
        res = trades_collection.delete_many({})
        return res.deleted_count
    except Exception as exc:
        logger.warning("Error clearing trades collection: %s", exc)
        return 0


def generate_news_event_id(currency: str, title: str, date_str: str, raw_time: str = "") -> str:
    """Generate deterministic event ID for deduplication."""
    from app.news_analyzer import slugify
    c = str(currency or "USD").upper().strip()
    t = slugify(title)
    d = str(date_str or "").strip()
    rt = slugify(raw_time)
    parts = [c, t, d]
    if rt:
        parts.append(rt)
    return "_".join(parts)


def save_news_event_to_db(event_data: Dict[str, Any]) -> Optional[str]:
    """Store or update a news event in MongoDB 'news_events' collection."""
    if news_events_collection is None or not is_mongodb_available():
        return None
    try:
        from app.news_analyzer import parse_economic_value
        doc = dict(event_data)
        doc.pop("_id", None)

        title = doc.get("title") or doc.get("name") or "Economic Event"
        currency = str(doc.get("currency", "USD")).upper().strip()
        date_str = str(doc.get("date") or (doc.get("time") or "")[:10]).strip()
        raw_time = str(doc.get("raw_time") or "").strip()

        event_id = doc.get("event_id") or generate_news_event_id(currency, title, date_str, raw_time)
        doc["event_id"] = event_id
        doc["title"] = title
        doc["name"] = title
        doc["currency"] = currency
        doc["date"] = date_str
        doc["raw_time"] = raw_time

        # Numeric parsing
        f_val, f_unit = parse_economic_value(doc.get("forecast"))
        p_val, p_unit = parse_economic_value(doc.get("previous"))
        a_val, a_unit = parse_economic_value(doc.get("actual"))

        doc["forecast_numeric"] = f_val
        doc["previous_numeric"] = p_val
        doc["actual_numeric"] = a_val
        doc["unit"] = f_unit or p_unit or a_unit or ""

        # Compute surprise and direction if actual exists
        if a_val is not None and f_val is not None:
            surprise = round(a_val - f_val, 4)
            doc["surprise"] = surprise
            if abs(surprise) < 0.0001:
                doc["surprise_direction"] = "IN_LINE"
            elif surprise > 0:
                doc["surprise_direction"] = "BEAT"
            else:
                doc["surprise_direction"] = "MISS"
        else:
            doc["surprise"] = None
            doc["surprise_direction"] = None

        if a_val is not None and p_val is not None:
            diff_prev = round(a_val - p_val, 4)
            doc["actual_vs_previous_delta"] = diff_prev
            if abs(diff_prev) < 0.0001:
                doc["actual_vs_previous_direction"] = "UNCHANGED"
            elif diff_prev > 0:
                doc["actual_vs_previous_direction"] = "HIGHER"
            else:
                doc["actual_vs_previous_direction"] = "LOWER"

        doc["updated_at"] = datetime.now(timezone.utc)

        res = news_events_collection.update_one(
            {"event_id": event_id},
            {
                "$set": doc,
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
        return str(res.upserted_id or event_id)
    except Exception as exc:
        logger.warning("Failed saving news event to MongoDB: %s", exc)
        return None


def save_news_events_to_db(events: List[Dict[str, Any]]) -> Dict[str, int]:
    """Store or update a batch of news events in MongoDB."""
    if news_events_collection is None or not events:
        return {"saved": 0, "total": len(events or [])}
    saved_count = 0
    for evt in events:
        if isinstance(evt, dict):
            res = save_news_event_to_db(evt)
            if res:
                saved_count += 1
    return {"saved": saved_count, "total": len(events)}


def get_news_events_from_db(
    date: Optional[str] = None,
    currency: Optional[str] = None,
    impact: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Retrieve news events from MongoDB 'news_events' collection."""
    if news_events_collection is None or not is_mongodb_available():
        return []
    try:
        query: Dict[str, Any] = {}
        if date:
            query["date"] = date
        if currency:
            query["currency"] = str(currency).upper()
        if impact:
            query["impact"] = str(impact).lower()

        cursor = news_events_collection.find(query).sort([("time", -1), ("_id", -1)]).limit(limit)
        events = []
        for doc in cursor:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
            if isinstance(doc.get("created_at"), datetime):
                doc["created_at"] = doc["created_at"].isoformat()
            if isinstance(doc.get("updated_at"), datetime):
                doc["updated_at"] = doc["updated_at"].isoformat()
            events.append(doc)
        return events
    except Exception as exc:
        logger.warning("Failed retrieving news events from MongoDB: %s", exc)
        return []


def get_previous_events_for_event(
    title: str,
    currency: str = "USD",
    before_date: Optional[str] = None,
    before_time: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Retrieve past occurrences of a specific economic event from MongoDB 'news_events',
    strictly prior to before_date or before_time.
    """
    if news_events_collection is None or not title or not is_mongodb_available():
        return []
    try:
        import re
        curr = str(currency or "USD").upper().strip()
        escaped_title = re.escape(title.strip())

        query: Dict[str, Any] = {
            "currency": curr,
            "title": {"$regex": f"^{escaped_title}$", "$options": "i"},
        }
        if before_time:
            query["time"] = {"$lt": before_time}
        elif before_date:
            query["date"] = {"$lt": before_date}

        cursor = news_events_collection.find(query).sort([("time", -1), ("date", -1)]).limit(limit)
        results = []
        for doc in cursor:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
            if isinstance(doc.get("created_at"), datetime):
                doc["created_at"] = doc["created_at"].isoformat()
            if isinstance(doc.get("updated_at"), datetime):
                doc["updated_at"] = doc["updated_at"].isoformat()
            results.append(doc)
        return results
    except Exception as exc:
        logger.warning("Failed querying previous releases for %s: %s", title, exc)
        return []


def save_news_analysis_to_db(news_data: Dict[str, Any]) -> Optional[str]:
    """
    Store the latest news calendar and analysis snapshot in MongoDB 'news' collection,
    persist individual events in 'news_events' collection, and maintain historical analysis in 'news_analysis'.
    """
    if news_collection is None or not is_mongodb_available():
        return None
    try:
        doc = dict(news_data)
        doc.pop("_id", None)
        doc["type"] = "latest_news_analysis"
        doc["saved_at_utc"] = datetime.now(timezone.utc).isoformat()

        # 1. Update latest news analysis snapshot in 'news' collection
        res = news_collection.update_one(
            {"type": "latest_news_analysis"},
            {"$set": doc},
            upsert=True,
        )

        # 2. Persist all individual today's events into 'news_events' collection
        todays_events = doc.get("todays_events") or []
        if isinstance(todays_events, list) and todays_events:
            save_news_events_to_db(todays_events)

        # 3. Store snapshot in 'news_analysis' collection for historical comparison
        if news_analysis_collection is not None:
            date_str = doc.get("date") or datetime.now(timezone.utc).date().isoformat()
            snapshot_doc = {
                "date": date_str,
                "timestamp_utc": doc.get("timestamp_utc") or doc["saved_at_utc"],
                "volatility_risk": doc.get("volatility_risk"),
                "status": doc.get("status"),
                "recommendation": doc.get("recommendation"),
                "todays_events_count": doc.get("todays_events_count", 0),
                "todays_usd_events_count": doc.get("todays_usd_events_count", 0),
                "market_news_note": doc.get("market_news_note"),
                "previous_news_comparison": doc.get("previous_news_comparison"),
                "created_at": datetime.now(timezone.utc),
            }
            # Keep analysis snapshot keyed by date
            news_analysis_collection.update_one(
                {"date": date_str},
                {"$set": snapshot_doc},
                upsert=True,
            )

        return str(res.upserted_id or "updated")
    except Exception as exc:
        logger.warning("Error saving news analysis to MongoDB: %s", exc)
        return None


def get_latest_news_from_db() -> Optional[Dict[str, Any]]:
    """Retrieve the latest news analysis snapshot from MongoDB."""
    if news_collection is None or not is_mongodb_available():
        return None
    try:
        doc = news_collection.find_one({"type": "latest_news_analysis"})
        if doc:
            doc.pop("_id", None)
            return doc
    except Exception as exc:
        logger.warning("Error reading news analysis from MongoDB: %s", exc)
    return None


def get_news_analysis_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve historical news analysis snapshots from MongoDB 'news_analysis' collection."""
    if news_analysis_collection is None or not is_mongodb_available():
        return []
    try:
        cursor = news_analysis_collection.find().sort([("timestamp_utc", -1), ("_id", -1)]).limit(limit)
        results = []
        for doc in cursor:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
            if isinstance(doc.get("created_at"), datetime):
                doc["created_at"] = doc["created_at"].isoformat()
            results.append(doc)
        return results
    except Exception as exc:
        logger.warning("Failed retrieving news analysis history: %s", exc)
        return []


def delete_news_event_from_db(event_id: str) -> bool:
    """Delete a news event document from MongoDB by event_id or _id."""
    if news_events_collection is None or not is_mongodb_available():
        return False
    try:
        from bson import ObjectId
        query = {"$or": [{"event_id": event_id}]}
        try:
            query["$or"].append({"_id": ObjectId(event_id)})
        except Exception:
            pass
        res = news_events_collection.delete_many(query)
        logger.info("Deleted %d news event(s) matching %s", res.deleted_count, event_id)
        return res.deleted_count > 0
    except Exception as exc:
        logger.warning("Error deleting news event %s: %s", event_id, exc)
        return False


def clear_all_news_from_db() -> int:
    """Clear all records from news, news_events, and news_analysis collections."""
    deleted = 0
    if news_events_collection is not None:
        try:
            res1 = news_events_collection.delete_many({})
            deleted += res1.deleted_count
        except Exception as exc:
            logger.warning("Error clearing news_events collection: %s", exc)
    if news_collection is not None:
        try:
            res2 = news_collection.delete_many({})
            deleted += res2.deleted_count
        except Exception as exc:
            logger.warning("Error clearing news collection: %s", exc)
    if news_analysis_collection is not None:
        try:
            res3 = news_analysis_collection.delete_many({})
            deleted += res3.deleted_count
        except Exception as exc:
            logger.warning("Error clearing news_analysis collection: %s", exc)
    return deleted




if __name__ == "__main__":
    try:
        print(check_mongodb())
    except Exception as error:
        print("MongoDB connection failed:", error)
    finally:
        if client:
            client.close()
