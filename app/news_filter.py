import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("news-filter")

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

BLOCK_BEFORE_MINUTES = 15
BLOCK_AFTER_MINUTES = 15
MAX_CALENDAR_AGE_HOURS = 24


def get_events_file() -> Path:
    candidates = [APP_DIR / "events.json", ROOT_DIR / "events.json"]
    valid = [c for c in candidates if c.exists() and c.stat().st_size > 10]
    if valid:
        return max(valid, key=lambda f: f.stat().st_mtime)
    return ROOT_DIR / "events.json"


def analyze_news_events(events: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    """
    Perform deep analysis of economic news events:
    - Identifies today's events
    - Identifies next upcoming event and minutes countdown
    - Evaluates market volatility risks for Gold (XAU/USD)
    - Formulates an actionable news note
    """
    today_utc = now.date()
    todays_events: List[Dict[str, Any]] = []
    upcoming_events: List[Dict[str, Any]] = []
    recent_past_events: List[Dict[str, Any]] = []
    active_blocking_event: Optional[Dict[str, Any]] = None
    next_event: Optional[Dict[str, Any]] = None
    min_future_delta_minutes: Optional[float] = None

    for item in events:
        if not isinstance(item, dict):
            continue

        impact_str = str(item.get("impact", "")).strip().lower()
        impact_score = item.get("impactScore") or item.get("impact_score")
        if impact_str != "high" and impact_score != 3:
            continue

        raw_time = item.get("time")
        if not raw_time:
            continue

        try:
            event_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            currency = str(item.get("currency", "USD")).upper()
            event_record = {
                "name": item.get("name") or item.get("title", "High Impact Event"),
                "title": item.get("title") or item.get("name", "High Impact Event"),
                "time": event_time.isoformat(),
                "currency": currency,
                "impact": "high",
                "forecast": item.get("forecast") or "—",
                "previous": item.get("previous") or "—",
                "actual": item.get("actual"),
            }

            delta_minutes = (event_time - now).total_seconds() / 60.0

            # Check if today
            if event_time.date() == today_utc:
                todays_events.append(event_record)
                if delta_minutes <= 0:
                    recent_past_events.append({
                        **event_record,
                        "minutes_ago": round(abs(delta_minutes), 1),
                    })

            # Check if in block window (e.g. 15m before to 15m after)
            if -BLOCK_AFTER_MINUTES <= delta_minutes <= BLOCK_BEFORE_MINUTES:
                active_blocking_event = {
                    **event_record,
                    "delta_minutes": round(delta_minutes, 1),
                }

            # If event is in the future
            if delta_minutes > 0:
                future_record = {
                    **event_record,
                    "minutes_until": round(delta_minutes, 1),
                    "hours_until": round(delta_minutes / 60.0, 1),
                }
                upcoming_events.append(future_record)

                if min_future_delta_minutes is None or delta_minutes < min_future_delta_minutes:
                    min_future_delta_minutes = delta_minutes
                    next_event = future_record

        except Exception:
            continue

    upcoming_events.sort(key=lambda x: x["minutes_until"])

    # Formulate market analysis note
    if active_blocking_event:
        market_news_note = (
            f"HIGH-IMPACT NEWS WINDOW ACTIVE: '{active_blocking_event['name']}' "
            f"is currently executing or within the ±15 min high-risk window. "
            f"Gold (XAU/USD) is subject to extreme spreads and wild spikes. Trading suspended."
        )
        status = "BLOCKED"
    elif next_event and next_event["minutes_until"] <= 60:
        market_news_note = (
            f"APPROACHING HIGH-IMPACT NEWS: '{next_event['name']}' scheduled in "
            f"{int(next_event['minutes_until'])} minutes. Caution: XAU/USD volatility is rising. "
            f"Prepare for rapid price action around Support/Resistance zones."
        )
        status = "CAUTION"
    elif todays_events:
        names = ", ".join([e["name"] for e in todays_events[:2]])
        next_note = f" Next: '{next_event['name']}' in {next_event['hours_until']}h." if next_event else ""
        market_news_note = (
            f"HIGH-IMPACT NEWS SCHEDULED TODAY: {names}.{next_note} "
            f"Technical setups are permitted, but monitor positions ahead of release."
        )
        status = "CLEAR"
    elif upcoming_events:
        market_news_note = (
            f"UPCOMING NEWS AHEAD: Next high-impact event '{upcoming_events[0]['name']}' "
            f"in {upcoming_events[0]['hours_until']} hours. Market conditions clear for technical execution."
        )
        status = "CLEAR"
    else:
        market_news_note = (
            "CLEAR NEWS CALENDAR: No major high-impact US economic events scheduled in the immediate horizon. "
            "Market direction on XAU/USD is primarily driven by technical levels and Support/Resistance."
        )
        status = "CLEAR"

    return {
        "status": status,
        "has_news_today": len(todays_events) > 0,
        "todays_events_count": len(todays_events),
        "todays_events": todays_events,
        "active_blocking_event": active_blocking_event,
        "next_event": next_event,
        "upcoming_events": upcoming_events[:8],
        "recent_past_events": recent_past_events,
        "market_news_note": market_news_note,
    }


def check_news_filter() -> dict:
    events_file = get_events_file()

    # If calendar missing, try to auto-refresh once
    if not events_file.exists():
        try:
            from app.news_fetcher import refresh_news_calendar
            refresh_news_calendar()
        except Exception as e:
            logger.warning("Could not auto-fetch calendar on missing file: %s", e)

    if not events_file.exists():
        return {
            "allowed": False,
            "reason": "News calendar missing",
            "news_analysis": {
                "has_news_today": False,
                "market_news_note": "News calendar missing; trades blocked for risk safety.",
                "status": "BLOCKED",
            },
        }

    try:
        data = json.loads(events_file.read_text(encoding="utf-8"))

        if data.get("verified") is not True:
            return {
                "allowed": False,
                "reason": "News calendar has not been verified",
                "news_analysis": None,
            }

        updated_at = datetime.fromisoformat(
            str(data["updated_at"]).replace("Z", "+00:00")
        )

        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        # Check staleness (> 24 hours) - attempt refresh if stale but do NOT destroy news_analysis if refresh fails
        stale_note = ""
        if now - updated_at > timedelta(hours=MAX_CALENDAR_AGE_HOURS):
            try:
                from app.news_fetcher import refresh_news_calendar
                refresh_res = refresh_news_calendar()
                data = json.loads(events_file.read_text(encoding="utf-8"))
                updated_at = datetime.fromisoformat(str(data["updated_at"]).replace("Z", "+00:00"))
            except Exception as refresh_err:
                logger.debug("News auto-refresh deferred: %s", refresh_err)
                stale_note = " (Calendar cache active)"

        events = data.get("events", [])
        if not isinstance(events, list):
            return {
                "allowed": False,
                "reason": "Invalid events list in calendar",
                "news_analysis": None,
            }

        news_analysis = analyze_news_events(events, now)
        if stale_note and news_analysis:
            news_analysis["market_news_note"] = news_analysis["market_news_note"] + stale_note

        if news_analysis["active_blocking_event"]:
            active = news_analysis["active_blocking_event"]
            return {
                "allowed": False,
                "reason": f"High-impact event window: {active['name']}",
                "event": active["name"],
                "event_time": active["time"],
                "events_count": len(events),
                "updated_at": data.get("updated_at"),
                "news_analysis": news_analysis,
            }

        return {
            "allowed": True,
            "reason": "No high-impact event in the configured block window",
            "events_count": len(events),
            "updated_at": data.get("updated_at"),
            "news_analysis": news_analysis,
        }

    except Exception as exc:
        return {
            "allowed": False,
            "reason": f"News calendar could not be validated: {exc}",
            "news_analysis": None,
        }