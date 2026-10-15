import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv(
    "FOREX_FACTORY_API_URL",
    "http://127.0.0.1:3000/api/v1/high-impact",
)

CALENDAR_TIMEZONE = os.getenv("FOREX_FACTORY_TIMEZONE", "").strip()

EVENTS_FILE = (
    Path(__file__).resolve().parent.parent / "events.json"
)

logger = logging.getLogger("news-fetcher")


def refresh_news_calendar():
    """Fetch high-impact USD events and save them in UTC."""

    if not CALENDAR_TIMEZONE:
        raise RuntimeError(
            "Set FOREX_FACTORY_TIMEZONE in .env to the timezone "
            "configured in your Forex Factory calendar"
        )

    try:
        calendar_tz = ZoneInfo(CALENDAR_TIMEZONE)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"Unknown timezone: {CALENDAR_TIMEZONE}"
        ) from exc

    response = requests.get(API_URL, timeout=20)
    response.raise_for_status()
    payload = response.json()

    # This API returns events at the top-level "events" field.
    events_data = payload.get("events")
    date_queried = payload.get("dateQueried")

    if not isinstance(events_data, list) or not date_queried:
        raise RuntimeError(
            "Unexpected API response: missing events or dateQueried"
        )

    # Use the full date, including year, supplied by the API.
    try:
        event_date = datetime.strptime(
            date_queried, "%Y-%m-%d"
        ).date()
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid calendar date: {date_queried}"
        ) from exc

    normalized_events = []

    for item in events_data:
        if not isinstance(item, dict):
            continue

        currency = str(item.get("currency", "")).upper()
        impact = str(item.get("impact", "")).strip().lower()
        impact_score = item.get("impactScore")

        # Gold-focused filter: high-impact USD events only.
        if currency != "USD":
            continue

        if impact != "high" and impact_score != 3:
            continue

        title = item.get("title")
        time_text = item.get("time")

        if not title or not time_text:
            logger.warning("Skipping event missing title or time")
            continue

        # Skip events without an exact clock time.
        try:
            local_datetime = datetime.strptime(
                f"{event_date.isoformat()} {str(time_text).strip()}",
                "%Y-%m-%d %I:%M%p",
            )
        except ValueError:
            logger.warning(
                "Skipping event with unsupported time: %s (%s)",
                title,
                time_text,
            )
            continue

        # Interpret the displayed event time in the configured
        # Forex Factory timezone, then convert it to UTC.
        local_datetime = local_datetime.replace(
            tzinfo=calendar_tz
        )
        utc_datetime = local_datetime.astimezone(timezone.utc)

        normalized_events.append({
            "name": str(title),
            "currency": currency,
            "time": utc_datetime.isoformat(),
            "impact": "high",
        })

    # Preserve the existing file if the response is unusable.
    if not normalized_events:
        raise RuntimeError(
            "No valid high-impact USD events found; "
            "existing events.json was not changed"
        )

    calendar = {
        "verified": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": API_URL,
        "source_date": date_queried,
        "source_timestamp": payload.get("timestamp"),
        "timezone_used": CALENDAR_TIMEZONE,
        "events": normalized_events,
    }

    temporary_file = EVENTS_FILE.with_suffix(".json.tmp")

    temporary_file.write_text(
        json.dumps(calendar, indent=2),
        encoding="utf-8",
    )

    os.replace(temporary_file, EVENTS_FILE)

    logger.info(
        "Saved %s high-impact USD events to %s",
        len(normalized_events),
        EVENTS_FILE,
    )

    return {
        "updated": True,
        "event_count": len(normalized_events),
        "file": str(EVENTS_FILE),
        "timezone": CALENDAR_TIMEZONE,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    try:
        print(json.dumps(refresh_news_calendar(), indent=2))
    except Exception:
        logger.exception("News calendar refresh failed")
        raise SystemExit(1)
