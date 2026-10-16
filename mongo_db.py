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
    """Store analysis result in MongoDB 'analysis' collection."""
    if analysis_collection is None:
        return None
    try:
        doc = dict(analysis_data)
        if "created_at" not in doc:
            doc["created_at"] = datetime.now(timezone.utc)
        result = analysis_collection.insert_one(doc)
        inserted_id = str(result.inserted_id)

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
                logger.debug("Failed recording to signals_collection: %s", sig_err)

        return inserted_id
    except Exception as exc:
        logger.warning("Failed saving analysis to MongoDB: %s", exc)
        return None


if __name__ == "__main__":
    try:
        print(check_mongodb())
    except Exception as error:
        print("MongoDB connection failed:", error)
    finally:
        if client:
            client.close()
