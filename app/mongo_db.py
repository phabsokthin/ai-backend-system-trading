import os
import logging
from datetime import datetime, timezone
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
    Store analysis result in MongoDB 'analysis' collection.
    Also stores in 'signals' collection if a BUY or SELL setup exists.
    Non-blocking / safe: will never crash caller if MongoDB is temporarily unavailable.
    """
    if analysis_collection is None:
        return None

    try:
        # Clone to prevent MongoDB driver from mutating the original in-memory dict with ObjectId
        doc = dict(analysis_data)
        if "created_at" not in doc:
            doc["created_at"] = datetime.now(timezone.utc)

        result = analysis_collection.insert_one(doc)
        inserted_id = str(result.inserted_id)

        # If analysis contains a confirmed signal / setup, save to signals collection as well
        signal = doc.get("signal")
        if signal in ("BUY", "SELL") or doc.get("has_setup") or doc.get("is_new_entry"):
            try:
                sig_doc = {
                    "analysis_id": inserted_id,
                    "timestamp_utc": doc.get("timestamp_utc") or datetime.now(timezone.utc).isoformat(),
                    "symbol": doc.get("symbol", "XAU/USD"),
                    "signal": signal,
                    "action": doc.get("action"),
                    "price": doc.get("price"),
                    "stop_loss": doc.get("stop_loss") or doc.get("sl"),
                    "take_profit": doc.get("take_profit") or doc.get("tp"),
                    "reason": doc.get("reason"),
                    "setup": doc.get("setup"),
                    "is_new_entry": doc.get("is_new_entry", False),
                    "created_at": datetime.now(timezone.utc),
                }
                signals_collection.insert_one(sig_doc)
            except Exception as sig_err:
                logger.debug("Failed to record to signals_collection: %s", sig_err)

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
    """Retrieve historical analysis records from MongoDB."""
    if analysis_collection is None:
        return []
    try:
        cursor = analysis_collection.find().sort("_id", -1).limit(limit)
        results = []
        for doc in cursor:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
            if isinstance(doc.get("created_at"), datetime):
                doc["created_at"] = doc["created_at"].isoformat()
            results.append(doc)
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


if __name__ == "__main__":
    try:
        print(check_mongodb())
    except Exception as error:
        print("MongoDB connection failed:", error)
    finally:
        if client:
            client.close()
