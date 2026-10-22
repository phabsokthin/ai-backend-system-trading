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
except Exception as e:
    logger.warning("Could not initialize MongoDB client: %s", e)
    client = None
    db = None
    trades_collection = None
    candles_collection = None
    signals_collection = None
    analysis_collection = None


def check_mongodb() -> Dict[str, Any]:
    """Verify MongoDB connectivity."""
    try:
        if client is None:
            return {"connected": False, "error": "Client not initialized"}
        client.admin.command("ping")
        return {
            "connected": True,
            "database": MONGO_DB_NAME,
        }
    except Exception as exc:
        return {
            "connected": False,
            "database": MONGO_DB_NAME,
            "error": str(exc),
        }


def save_analysis(analysis_data: Dict[str, Any]) -> Optional[str]:
    """
    Store analysis result in MongoDB 'analysis' collection without duplicates.
    When a brand-new entry is confirmed (is_new_entry == True), stores the setup
    in 'signals' collection exactly once.
    """
    if analysis_collection is None:
        return None

    try:
        doc = dict(analysis_data)
        timestamp_utc = doc.get("timestamp_utc")

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

        # Deduplicate analysis records: check if an analysis with this exact timestamp exists,
        # or if one was already recorded for the same symbol within a 3-minute window
        if timestamp_utc:
            existing = None
            try:
                clean_ts = str(timestamp_utc).replace("Z", "+00:00")
                parsed_dt = datetime.fromisoformat(clean_ts)
                start_w = parsed_dt - timedelta(minutes=3)
                end_w = parsed_dt + timedelta(minutes=3)
                existing = analysis_collection.find_one({
                    "$or": [
                        {"timestamp_utc": timestamp_utc},
                        {
                            "symbol": doc.get("symbol", "XAU/USD"),
                            "created_at": {"$gte": start_w, "$lte": end_w}
                        }
                    ]
                })
            except Exception:
                existing = analysis_collection.find_one({"timestamp_utc": timestamp_utc})

            if existing:
                # Update existing analysis in-place with latest metrics rather than creating a duplicate
                analysis_collection.update_one({"_id": existing["_id"]}, {"$set": doc})
                return str(existing["_id"])

        if "created_at" not in doc:
            doc["created_at"] = datetime.now(timezone.utc)

        result = analysis_collection.insert_one(doc)
        inserted_id = str(result.inserted_id)

        # STRICT: Only save to 'signals' collection on genuine NEW ENTRY (save only once per setup)
        # Must be an actual OPEN_BUY / OPEN_SELL with is_new_entry == True, never HOLD / WAIT / CLOSE
        is_new_entry = bool(doc.get("is_new_entry")) and doc.get("action") in ("OPEN_BUY", "OPEN_SELL") and doc.get("signal") in ("BUY", "SELL")
        if is_new_entry and signals_collection is not None:
            active_pos = doc.get("active_position") or doc.get("setup") or {}
            setup_id = active_pos.get("id") or f"setup_{timestamp_utc}_{doc.get('signal')}"

            sig_doc = {
                "setup_id": setup_id,
                "analysis_id": inserted_id,
                "timestamp_utc": timestamp_utc or datetime.now(timezone.utc).isoformat(),
                "symbol": doc.get("symbol", "XAU/USD"),
                "signal": doc.get("signal"),
                "action": doc.get("action"),
                "price": doc.get("price"),
                "current_price": doc.get("current_price") or doc.get("price"),
                "stop_loss": doc.get("stop_loss") or doc.get("sl"),
                "take_profit": doc.get("take_profit") or doc.get("tp"),
                "reason": doc.get("reason"),
                "setup": doc.get("setup"),
                "is_new_entry": True,
                "created_at": datetime.now(timezone.utc),
            }
            # Upsert by setup_id: strictly save to db only one time per setup!
            signals_collection.update_one(
                {"setup_id": setup_id},
                {"$setOnInsert": sig_doc},
                upsert=True,
            )

        logger.info("Saved analysis to MongoDB (id=%s, signal=%s, is_new_entry=%s)", inserted_id, doc.get("signal"), is_new_entry)
        return inserted_id
    except Exception as exc:
        logger.warning("Failed to store analysis in MongoDB: %s", exc)
        return None


def get_latest_analysis_from_db() -> Optional[Dict[str, Any]]:
    """Retrieve the most recent analysis record from MongoDB."""
    if analysis_collection is None:
        return None
    try:
        doc = analysis_collection.find_one(sort=[("_id", -1)])
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
            if isinstance(doc.get("created_at"), datetime):
                doc["created_at"] = doc["created_at"].isoformat()
        return doc
    except Exception as exc:
        logger.warning("Failed retrieving latest analysis from MongoDB: %s", exc)
        return None


def get_analysis_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve historical analysis records from MongoDB ordered DESC (newest first), deduplicated by 5m candle."""
    if analysis_collection is None:
        return []
    try:
        cursor = analysis_collection.find().sort([("timestamp_utc", -1), ("_id", -1)]).limit(limit * 2)
        results = []
        seen_candle_slots = set()

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

            # Deduplicate by 5-minute candle block
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
    if signals_collection is not None:
        try:
            # STRICT: Only retrieve genuine new entry trade signals (never HOLD or closures)
            cursor = signals_collection.find({
                "$or": [
                    {"action": {"$in": ["OPEN_BUY", "OPEN_SELL"]}},
                    {"is_new_entry": True}
                ],
                "action": {"$nin": ["HOLD", "WAIT", "CLOSE_SL", "CLOSE_TP", "CLOSE_INVALID", "CLOSE_OFFLINE"]}
            }).sort([("timestamp_utc", -1), ("_id", -1)]).limit(limit)
            results = []
            for doc in cursor:
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                if isinstance(doc.get("created_at"), datetime):
                    doc["created_at"] = doc["created_at"].isoformat()
                results.append(doc)
            # CRITICAL: Return MongoDB result directly (even if empty). Do NOT fall back to file logs when user deleted records!
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
            with analysis_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        is_entry = (record.get("is_new_entry") is True) or (record.get("action") in ("OPEN_BUY", "OPEN_SELL"))
                        if is_entry and record.get("action") not in ("HOLD", "WAIT", "CLOSE_SL", "CLOSE_TP", "CLOSE_INVALID", "CLOSE_OFFLINE"):
                            sig_item = {
                                "id": f"signal_{record.get('timestamp_utc', '').replace(':', '_').replace('.', '_')}_{record.get('signal', 'SETUP')}",
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
                                "is_new_entry": True,
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



if __name__ == "__main__":
    try:
        print(check_mongodb())
    except Exception as error:
        print("MongoDB connection failed:", error)
    finally:
        if client:
            client.close()
