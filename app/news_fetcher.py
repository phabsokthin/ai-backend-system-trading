import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv(
    "FOREX_FACTORY_API_URL",
    "https://news-forex.netlify.app/api/high-impact",
)

CALENDAR_TIMEZONE = os.getenv("FOREX_FACTORY_TIMEZONE", "Asia/Phnom_Penh").strip()

ROOT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent

EVENTS_FILE = ROOT_DIR / "events.json"
APP_EVENTS_FILE = APP_DIR / "events.json"

logger = logging.getLogger("news-fetcher")


def parse_event_time(event_date, time_text: str, calendar_tz: ZoneInfo):
    """
    Parse local event time into UTC datetime.
    Supports '%I:%M%p' (e.g. '9:30pm') and gracefully handles 'All Day' / 'Tentative'.
    """
    cleaned = str(time_text).strip()
    if not cleaned or cleaned.lower() in ("all day", "tentative", "day 1", "day 2", "day 3"):
        utc_dt = datetime.combine(event_date, datetime.min.time(), tzinfo=timezone.utc)
        return utc_dt, True

    try:
        local_dt = datetime.strptime(f"{event_date.isoformat()} {cleaned}", "%Y-%m-%d %I:%M%p")
        local_dt = local_dt.replace(tzinfo=calendar_tz)
        return local_dt.astimezone(timezone.utc), False
    except ValueError:
        try:
            local_dt = datetime.strptime(f"{event_date.isoformat()} {cleaned}", "%Y-%m-%d %H:%M")
            local_dt = local_dt.replace(tzinfo=calendar_tz)
            return local_dt.astimezone(timezone.utc), False
        except ValueError:
            utc_dt = datetime.combine(event_date, datetime.min.time(), tzinfo=timezone.utc)
            return utc_dt, True


def fetch_today_payload(api_url: str, day_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch economic events for today from the scraper API."""
    url = f"{api_url}?day={day_str}" if day_str else api_url
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.debug("Failed fetching news from %s: %s", url, exc)
    return None


def refresh_news_calendar() -> Dict[str, Any]:
    """
    Fetch high-impact economic news events for TODAY ONLY from Forex Factory,
    normalize timestamps to UTC, and persist to events.json.
    """
    if not CALENDAR_TIMEZONE:
        raise RuntimeError(
            "Set FOREX_FACTORY_TIMEZONE in .env to the timezone configured in your Forex Factory calendar"
        )

    try:
        calendar_tz = ZoneInfo(CALENDAR_TIMEZONE)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Unknown timezone: {CALENDAR_TIMEZONE}") from exc

    now_utc = datetime.now(timezone.utc)
    today_date = now_utc.date()
    day_str = today_date.strftime("%b%d.%Y").lower()

    # Try fetching with exact day parameter, fallback to plain API_URL
    payload = fetch_today_payload(API_URL, day_str)
    if payload is None:
        payload = fetch_today_payload(API_URL, None)

    if payload is None:
        raise RuntimeError(f"Could not connect to Forex Factory API ({API_URL})")

    events_data = payload.get("events")
    if not isinstance(events_data, list):
        events_data = []

    date_queried_str = payload.get("dateQueried")
    try:
        event_date = datetime.strptime(date_queried_str, "%Y-%m-%d").date() if date_queried_str else today_date
    except ValueError:
        event_date = today_date

    normalized_events: List[Dict[str, Any]] = []
    seen_keys = set()

    for item in events_data:
        if not isinstance(item, dict):
            continue

        currency = str(item.get("currency", "")).upper()
        impact = str(item.get("impact", "")).strip().lower()
        impact_score = item.get("impactScore")

        if impact != "high" and impact_score != 3:
            continue

        title = item.get("title") or item.get("name")
        time_text = item.get("time")

        if not title:
            continue

        utc_dt, is_all_day = parse_event_time(event_date, time_text, calendar_tz)

        # STRICT FILTER: Show ONLY events scheduled for today
        if utc_dt.date() != today_date and event_date != today_date:
            continue

        dedup_key = (str(title).strip().lower(), currency, str(time_text).strip().lower())
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        normalized_events.append({
            "name": str(title).strip(),
            "title": str(title).strip(),
            "currency": currency,
            "time": utc_dt.isoformat(),
            "date": today_date.isoformat(),
            "impact": "high",
            "impact_score": 3,
            "all_day": is_all_day,
            "raw_time": str(time_text).strip() if time_text else "",
            "forecast": str(item.get("forecast") or "").strip() or None,
            "previous": str(item.get("previous") or "").strip() or None,
            "actual": str(item.get("actual") or "").strip() or None,
        })

    # Sort today's events chronologically
    normalized_events.sort(key=lambda x: (x["time"], x["currency"] != "USD"))

    # If scrape returned 0 events but existing cache has valid events for today, preserve them
    if len(normalized_events) == 0 and EVENTS_FILE.exists():
        try:
            cached_data = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
            cached_events = cached_data.get("events", [])
            if cached_data.get("date_queried") == today_date.isoformat() and len(cached_events) > 0:
                logger.info("Preserving %d cached events for today", len(cached_events))
                normalized_events = cached_events
        except Exception:
            pass

    calendar = {
        "verified": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": API_URL,
        "date_queried": today_date.isoformat(),
        "timezone_used": CALENDAR_TIMEZONE,
        "scope": "today_only",
        "total_events": len(normalized_events),
        "total_usd_events": sum(1 for e in normalized_events if e["currency"] == "USD"),
        "events": normalized_events,
    }

    # Save to both ROOT_DIR / events.json and APP_DIR / events.json
    for target_path in (EVENTS_FILE, APP_EVENTS_FILE):
        try:
            temp_path = target_path.with_suffix(".json.tmp")
            temp_path.write_text(json.dumps(calendar, indent=2), encoding="utf-8")
            os.replace(temp_path, target_path)
            logger.info("Saved %s events for today to %s", len(normalized_events), target_path)
        except Exception as save_err:
            logger.warning("Could not write to %s: %s", target_path, save_err)

    # Persist today's events to MongoDB
    mongo_saved = 0
    try:
        from app.mongo_db import save_news_events_to_db
        db_res = save_news_events_to_db(normalized_events)
        mongo_saved = db_res.get("saved", 0)
        logger.info("Stored %s news events into MongoDB", mongo_saved)
    except Exception as mongo_err:
        logger.warning("Failed saving news events to MongoDB: %s", mongo_err)

    return {
        "updated": True,
        "updated_at": calendar["updated_at"],
        "date": today_date.isoformat(),
        "scope": "today_only",
        "event_count": len(normalized_events),
        "usd_event_count": calendar["total_usd_events"],
        "mongo_saved": mongo_saved,
        "file": str(EVENTS_FILE),
        "timezone": CALENDAR_TIMEZONE,
    }


def sync_historical_news_events(days_back: int = 7) -> Dict[str, Any]:
    """
    Fetch and persist historical economic news events for past days into MongoDB.
    Enables deep comparison of current news against previous historical releases.
    """
    if not CALENDAR_TIMEZONE:
        return {"synced": False, "reason": "No calendar timezone configured"}

    try:
        calendar_tz = ZoneInfo(CALENDAR_TIMEZONE)
    except Exception as tz_err:
        return {"synced": False, "reason": str(tz_err)}

    now_utc = datetime.now(timezone.utc)
    all_synced_events: List[Dict[str, Any]] = []
    days_checked = 0
    days_with_data = 0

    for i in range(1, days_back + 1):
        past_date = (now_utc - timedelta(days=i)).date()
        day_str = past_date.strftime("%b%d.%Y").lower()
        days_checked += 1

        payload = fetch_today_payload(API_URL, day_str)
        if not payload or not isinstance(payload.get("events"), list):
            continue

        raw_events = payload.get("events", [])
        if not raw_events:
            continue

        days_with_data += 1
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name")
            if not title:
                continue
            currency = str(item.get("currency", "")).upper()
            impact = str(item.get("impact", "")).strip().lower()
            impact_score = item.get("impactScore") or item.get("impact_score")
            if impact != "high" and impact_score != 3:
                continue

            time_text = item.get("time")
            utc_dt, is_all_day = parse_event_time(past_date, time_text, calendar_tz)

            all_synced_events.append({
                "name": str(title).strip(),
                "title": str(title).strip(),
                "currency": currency,
                "time": utc_dt.isoformat(),
                "date": past_date.isoformat(),
                "impact": "high",
                "impact_score": 3,
                "all_day": is_all_day,
                "raw_time": str(time_text).strip() if time_text else "",
                "forecast": str(item.get("forecast") or "").strip() or None,
                "previous": str(item.get("previous") or "").strip() or None,
                "actual": str(item.get("actual") or "").strip() or None,
            })

    saved_count = 0
    if all_synced_events:
        try:
            from app.mongo_db import save_news_events_to_db
            res = save_news_events_to_db(all_synced_events)
            saved_count = res.get("saved", 0)
            logger.info("Synced and stored %s historical news events in MongoDB", saved_count)
        except Exception as err:
            logger.warning("Failed saving historical news events to MongoDB: %s", err)

    return {
        "synced": True,
        "days_checked": days_checked,
        "days_with_data": days_with_data,
        "total_historical_events": len(all_synced_events),
        "saved_to_mongodb": saved_count,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        res = refresh_news_calendar()
        print(json.dumps(res, indent=2))
    except Exception:
        logger.exception("News calendar refresh failed")
        raise SystemExit(1)
