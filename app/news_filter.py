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

# Major US Market Movers that heavily impact Gold (XAU/USD)
KEY_GOLD_CATALYSTS = [
    "non-farm", "nfp", "cpi", "consumer price index", "fomc", "fed chairman", "federal funds",
    "interest rate", "gdp", "retail sales", "ppi", "core pce", "employment change", "unemployment rate"
]


def get_events_file() -> Path:
    candidates = [APP_DIR / "events.json", ROOT_DIR / "events.json"]
    valid = [c for c in candidates if c.exists() and c.stat().st_size > 10]
    if valid:
        return max(valid, key=lambda f: f.stat().st_mtime)
    return ROOT_DIR / "events.json"


def is_key_gold_driver(title: str) -> bool:
    """Check if the event title represents a tier-1 high-volatility driver for Gold."""
    t = title.lower()
    return any(catalyst in t for catalyst in KEY_GOLD_CATALYSTS)


def analyze_news_events(events: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    """
    Perform deep analysis of economic news events FOR TODAY ONLY:
    - Identifies today's events (separated by USD and other global currencies)
    - Identifies upcoming events scheduled for today with countdown
    - Assesses market volatility risk level for Gold (XAU/USD)
    - Formulates an actionable news note and recommendation for today's trading
    """
    today_utc = now.date()
    todays_events: List[Dict[str, Any]] = []
    todays_usd_events: List[Dict[str, Any]] = []
    upcoming_events: List[Dict[str, Any]] = []
    upcoming_usd_events: List[Dict[str, Any]] = []
    recent_past_events: List[Dict[str, Any]] = []
    key_gold_drivers: List[Dict[str, Any]] = []

    active_blocking_event: Optional[Dict[str, Any]] = None
    next_event: Optional[Dict[str, Any]] = None
    next_usd_event: Optional[Dict[str, Any]] = None

    min_future_delta_minutes: Optional[float] = None
    min_future_usd_delta_minutes: Optional[float] = None

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

            # STRICT TODAY-ONLY FILTER: Discard any events from other days
            if event_time.date() != today_utc and str(item.get("date", "")) != today_utc.isoformat():
                continue

            currency = str(item.get("currency", "USD")).upper()
            title = item.get("name") or item.get("title", "High Impact Event")
            is_all_day = bool(item.get("all_day", False))
            is_usd = (currency == "USD")

            event_record = {
                "name": title,
                "title": title,
                "time": event_time.isoformat(),
                "date": today_utc.isoformat(),
                "currency": currency,
                "impact": "high",
                "all_day": is_all_day,
                "raw_time": item.get("raw_time") or ("All Day" if is_all_day else ""),
                "forecast": item.get("forecast") or "—",
                "previous": item.get("previous") or "—",
                "actual": item.get("actual"),
                "is_usd": is_usd,
                "is_gold_driver": is_key_gold_driver(title),
            }

            delta_minutes = (event_time - now).total_seconds() / 60.0

            todays_events.append(event_record)
            if is_usd:
                todays_usd_events.append(event_record)

            if delta_minutes <= 0 and not is_all_day:
                recent_past_events.append({
                    **event_record,
                    "minutes_ago": round(abs(delta_minutes), 1),
                })

            # Check blocking window (ONLY for USD high-impact events with clock times)
            if is_usd and not is_all_day:
                if -BLOCK_AFTER_MINUTES <= delta_minutes <= BLOCK_BEFORE_MINUTES:
                    active_blocking_event = {
                        **event_record,
                        "delta_minutes": round(delta_minutes, 1),
                    }

            # Future upcoming events today
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

                if is_usd:
                    upcoming_usd_events.append(future_record)
                    if min_future_usd_delta_minutes is None or delta_minutes < min_future_usd_delta_minutes:
                        min_future_usd_delta_minutes = delta_minutes
                        next_usd_event = future_record

            if is_usd and event_record["is_gold_driver"]:
                key_gold_drivers.append(event_record)

        except Exception as exc:
            logger.debug("Error analyzing event %s: %s", item, exc)
            continue

    upcoming_events.sort(key=lambda x: x["minutes_until"])
    upcoming_usd_events.sort(key=lambda x: x["minutes_until"])

    # Determine Volatility Risk Rating for Today's Gold (XAU/USD) Trading
    if active_blocking_event:
        volatility_risk = "EXTREME"
        status = "BLOCKED"
        market_news_note = (
            f"HIGH-IMPACT USD NEWS WINDOW ACTIVE TODAY: '{active_blocking_event['name']}' "
            f"is currently executing or within the ±15 min window. "
            f"Gold (XAU/USD) is subject to extreme spreads and wild price spikes. Trading suspended."
        )
        recommendation = "SUSPEND_TRADING"

    elif next_usd_event and next_usd_event["minutes_until"] <= 60 and not next_usd_event.get("all_day"):
        volatility_risk = "HIGH"
        status = "CAUTION"
        market_news_note = (
            f"APPROACHING HIGH-IMPACT USD EVENT TODAY: '{next_usd_event['name']}' scheduled in "
            f"{int(next_usd_event['minutes_until'])} minutes. "
            f"XAU/USD volatility is rising. Monitor positions ahead of release."
        )
        recommendation = "MONITOR_POSITIONS_TIGHTEN_STOPS"

    elif len(todays_usd_events) > 0:
        volatility_risk = "HIGH"
        status = "CLEAR"
        usd_names = ", ".join([e["name"] for e in todays_usd_events[:3]])
        next_note = f" Next USD catalyst: '{next_usd_event['name']}' in {next_usd_event['hours_until']}h." if next_usd_event else ""
        market_news_note = (
            f"HIGH-IMPACT US ECONOMIC DATA TODAY: {usd_names}.{next_note} "
            f"Technical setups are permitted, but exercise caution ahead of releases."
        )
        recommendation = "TECHNICAL_TRADING_PERMITTED_WITH_AWARENESS"

    elif len(todays_events) > 0:
        volatility_risk = "MEDIUM"
        status = "CLEAR"
        non_usd_names = ", ".join([f"{e['name']} ({e['currency']})" for e in todays_events[:3]])
        market_news_note = (
            f"GLOBAL HIGH-IMPACT NEWS TODAY: {non_usd_names}. "
            f"No direct US high-impact data scheduled today. Gold (XAU/USD) operates under optimal technical conditions."
        )
        recommendation = "OPTIMAL_TECHNICAL_CONDITIONS"

    else:
        volatility_risk = "LOW"
        status = "CLEAR"
        market_news_note = (
            f"CLEAR NEWS CALENDAR TODAY: No high-impact economic events scheduled for today ({today_utc.isoformat()}). "
            f"Gold (XAU/USD) price action is primarily driven by technical structure, Pivot Points, and Support/Resistance."
        )
        recommendation = "OPTIMAL_TECHNICAL_CONDITIONS"

    # Deep comparative analysis against previous news from MongoDB and calendar
    previous_news_comparison = None
    try:
        from app.news_analyzer import analyze_all_events_with_previous_news
        previous_news_comparison = analyze_all_events_with_previous_news(todays_events, db_lookup=True)
    except Exception as comp_err:
        logger.debug("Failed performing comparative news analysis: %s", comp_err)

    return {
        "status": status,
        "date": today_utc.isoformat(),
        "volatility_risk": volatility_risk,
        "recommendation": recommendation,
        "has_news_today": len(todays_events) > 0,
        "has_usd_news_today": len(todays_usd_events) > 0,
        "todays_events_count": len(todays_events),
        "todays_usd_events_count": len(todays_usd_events),
        "todays_events": todays_events,
        "todays_usd_events": todays_usd_events,
        "active_blocking_event": active_blocking_event,
        "next_event": next_event,
        "next_usd_event": next_usd_event,
        "key_gold_drivers": key_gold_drivers,
        "upcoming_events": upcoming_events,
        "upcoming_usd_events": upcoming_usd_events,
        "recent_past_events": recent_past_events,
        "market_news_note": market_news_note,
        "previous_news_comparison": previous_news_comparison,
    }


def check_news_filter() -> dict:
    """
    Check today's economic news calendar status, enforce the ±15 min trade guard,
    and generate a focused news analysis for today.
    """
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
                "has_usd_news_today": False,
                "volatility_risk": "HIGH",
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

        raw_updated_at = data.get("updated_at")
        updated_at = datetime.fromisoformat(str(raw_updated_at).replace("Z", "+00:00")) if raw_updated_at else datetime.now(timezone.utc)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        # Check staleness (> 24 hours or calendar from a previous date) - auto-refresh for today
        stale_note = ""
        calendar_age_hours = round((now - updated_at).total_seconds() / 3600.0, 1)
        calendar_date_str = data.get("date_queried")

        is_different_day = (calendar_date_str and calendar_date_str != now.date().isoformat())
        if now - updated_at > timedelta(hours=MAX_CALENDAR_AGE_HOURS) or is_different_day:
            try:
                from app.news_fetcher import refresh_news_calendar
                refresh_news_calendar()
                data = json.loads(events_file.read_text(encoding="utf-8"))
                updated_at = datetime.fromisoformat(str(data["updated_at"]).replace("Z", "+00:00"))
                calendar_age_hours = round((now - updated_at).total_seconds() / 3600.0, 1)
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

        news_analysis["calendar_age_hours"] = calendar_age_hours
        news_analysis["calendar_updated_at"] = data.get("updated_at")

        if news_analysis["active_blocking_event"]:
            active = news_analysis["active_blocking_event"]
            return {
                "allowed": False,
                "reason": f"High-impact USD event window today: {active['name']}",
                "event": active["name"],
                "event_time": active["time"],
                "events_count": len(news_analysis["todays_events"]),
                "usd_events_count": len(news_analysis["todays_usd_events"]),
                "updated_at": data.get("updated_at"),
                "news_analysis": news_analysis,
            }

        return {
            "allowed": True,
            "reason": "No high-impact USD event in the configured block window today",
            "events_count": len(news_analysis["todays_events"]),
            "usd_events_count": len(news_analysis["todays_usd_events"]),
            "updated_at": data.get("updated_at"),
            "news_analysis": news_analysis,
        }

    except Exception as exc:
        return {
            "allowed": False,
            "reason": f"News calendar could not be validated: {exc}",
            "news_analysis": None,
        }


def get_detailed_news_analysis() -> Dict[str, Any]:
    """Convenience helper to extract structured news analysis for TODAY ONLY."""
    res = check_news_filter()
    analysis = res.get("news_analysis") or {}
    return {
        "success": True,
        "date": analysis.get("date", datetime.now(timezone.utc).date().isoformat()),
        "scope": "today_only",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "allowed": res.get("allowed", True),
        "reason": res.get("reason", "Analysis completed"),
        "status": analysis.get("status", "CLEAR"),
        "volatility_risk": analysis.get("volatility_risk", "LOW"),
        "recommendation": analysis.get("recommendation", "OPTIMAL_TECHNICAL_CONDITIONS"),
        "market_news_note": analysis.get("market_news_note", ""),
        "has_news_today": analysis.get("has_news_today", False),
        "has_usd_news_today": analysis.get("has_usd_news_today", False),
        "todays_events_count": analysis.get("todays_events_count", 0),
        "todays_usd_events_count": analysis.get("todays_usd_events_count", 0),
        "todays_events": analysis.get("todays_events", []),
        "todays_usd_events": analysis.get("todays_usd_events", []),
        "active_blocking_event": analysis.get("active_blocking_event"),
        "next_event": analysis.get("next_event"),
        "next_usd_event": analysis.get("next_usd_event"),
        "upcoming_events": analysis.get("upcoming_events", []),
        "upcoming_usd_events": analysis.get("upcoming_usd_events", []),
        "previous_news_comparison": analysis.get("previous_news_comparison"),
        "calendar_updated_at": res.get("updated_at"),
    }
