"""
Market Hours & Weekend Guard Utility for Gold (XAU/USD) Trading System.

Forex and Gold markets are officially closed on weekends:
- Friday Close: ~22:00 UTC (17:00 EST)
- Saturday: Completely CLOSED all 24 hours
- Sunday: CLOSED until 22:00 UTC (17:00 EST)

During weekend / market closed periods:
1. All open positions must be closed.
2. Technical analysis, candle fetches, and automated trade execution are halted.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any


def is_market_closed(dt: Optional[datetime] = None) -> Tuple[bool, str]:
    """
    Check if the Gold (XAU/USD) market is closed for the weekend or outside regular trading hours.
    Returns (is_closed: bool, reason: str).
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    weekday = dt.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday

    # Saturday: Completely closed all day
    if weekday == 5:
        return True, "Market is closed on Saturday. Trading and analysis are suspended."

    # Sunday: Closed until 22:00 UTC
    if weekday == 6:
        if dt.hour < 22:
            return True, f"Market is closed on Sunday until 22:00 UTC (Current UTC: {dt.strftime('%H:%M')}). Trading and analysis are suspended."

    # Friday: Closes at 22:00 UTC for the weekend
    if weekday == 4:
        if dt.hour >= 22:
            return True, f"Market is closed for the weekend (Friday closed at 22:00 UTC, current UTC: {dt.strftime('%H:%M')})."

    return False, "Market is open for regular trading."


def is_weekend(dt: Optional[datetime] = None) -> bool:
    """Return True if current time is Saturday or Sunday."""
    closed, _ = is_market_closed(dt)
    return closed


def get_market_status(dt: Optional[datetime] = None) -> Dict[str, Any]:
    """Return structured market status report for frontend and monitoring APIs."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    closed, reason = is_market_closed(dt)
    day_name = dt.strftime("%A")

    return {
        "symbol": "XAU/USD",
        "market_status": "CLOSED" if closed else "OPEN",
        "is_market_closed": closed,
        "is_weekend": dt.weekday() in (5, 6) or (dt.weekday() == 4 and dt.hour >= 22),
        "day_of_week": day_name,
        "reason": reason,
        "trading_allowed": not closed,
        "analysis_allowed": not closed,
        "timestamp_utc": dt.isoformat(),
    }
