# Mirror of app/mongo_db.py for root imports
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.mongo_db import (
    client,
    db,
    trades_collection,
    candles_collection,
    signals_collection,
    analysis_collection,
    news_collection,
    news_events_collection,
    news_analysis_collection,
    check_mongodb,
    save_analysis,
    save_signal_to_db,
    get_latest_analysis_from_db,
    get_analysis_history,
    save_trade_to_db,
    get_signals_history,
    delete_signal_from_db,
    delete_trade_from_db,
    clear_all_signals_from_db,
    clear_all_trades_from_db,
    generate_news_event_id,
    save_news_event_to_db,
    save_news_events_to_db,
    get_news_events_from_db,
    get_previous_events_for_event,
    save_news_analysis_to_db,
    get_latest_news_from_db,
    get_news_analysis_history,
    delete_news_event_from_db,
    clear_all_news_from_db,
)

if __name__ == "__main__":
    try:
        print(check_mongodb())
    except Exception as error:
        print("MongoDB connection failed:", error)
    finally:
        if client:
            client.close()
