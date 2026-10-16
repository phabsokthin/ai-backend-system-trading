import os
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

logger = logging.getLogger("mongo-db")

MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb://127.0.0.1:27017/"
)
MONGO_DB_NAME = os.getenv(
    "MONGO_DB_NAME",
    "ai-system-trading"
)

try:
    client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=3000
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
    try:
        if client is None:
            return {"connected": False, "error": "Client not initialized"}
        client.admin.command("ping")
        return {
            "connected": True,
            "database": MONGO_DB_NAME
        }
    except Exception as exc:
        return {
            "connected": False,
            "database": MONGO_DB_NAME,
            "error": str(exc),
        }


def save_analysis(analysis_data: Dict[str, Any]) -> Optional[str]:
    """Store analysis result in MongoDB 'analysis' collection without duplicates."""
    if analysis_collection is None:
        return None

    try:
        doc = dict(analysis_data)
        timestamp_utc = doc.get("timestamp_utc")

        if timestamp_utc:
            existing = analysis_collection.find_one({"timestamp_utc": timestamp_utc})
            if existing:
                return str(existing["_id"])

        if "created_at" not in doc:
            doc["created_at"] = datetime.now(timezone.utc)

        result = analysis_collection.insert_one(doc)
        inserted_id = str(result.inserted_id)

        is_new_entry = doc.get("is_new_entry", False) or doc.get("action") in ("OPEN_BUY", "OPEN_SELL")
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
                "stop_loss": doc.get("stop_loss") or doc.get("sl"),
                "take_profit": doc.get("take_profit") or doc.get("tp"),
                "reason": doc.get("reason"),
                "setup": doc.get("setup"),
                "is_new_entry": True,
                "created_at": datetime.now(timezone.utc),
            }
            signals_collection.update_one(
                {"setup_id": setup_id},
                {"$setOnInsert": sig_doc},
                upsert=True,
            )

        return inserted_id
    except Exception as exc:
        logger.warning("Failed saving analysis to MongoDB: %s", exc)
        return None


def save_trade_to_db(trade_data: Dict[str, Any]) -> Optional[str]:
    """Store or update trade in MongoDB 'trades' collection once."""
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


if __name__ == "__main__":
    try:
        print(check_mongodb())
    except Exception as error:
        print("MongoDB connection failed:", error)
    finally:
        if client:
            client.close()
