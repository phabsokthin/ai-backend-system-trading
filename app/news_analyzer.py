import re
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("news-analyzer")

# Major indicators and their economic mechanism relative to Gold (XAU/USD)
INDICATOR_RULES = [
    {
        "keywords": ["cpi", "consumer price index", "inflation", "ppi", "pce", "core pce"],
        "category": "INFLATION",
        "description": "Inflation Measure",
        # For inflation:
        # Higher than previous/forecast -> Hawkish Fed -> Stronger USD -> BEARISH for Gold
        # Lower than previous/forecast -> Dovish Fed -> Weaker USD -> BULLISH for Gold
        "higher_bias": "BEARISH",
        "lower_bias": "BULLISH",
        "weight": 1.0,
    },
    {
        "keywords": ["non-farm", "nfp", "employment change", "adp", "payrolls", "average hourly earnings"],
        "category": "LABOR_STRENGTH",
        "description": "Labor Market Strength",
        # Stronger jobs -> Stronger US economy -> Higher yields -> BEARISH for Gold
        # Weaker jobs -> Fed cuts / stimulus -> BULLISH for Gold
        "higher_bias": "BEARISH",
        "lower_bias": "BULLISH",
        "weight": 1.0,
    },
    {
        "keywords": ["unemployment rate", "jobless claims", "initial claims", "continuing claims"],
        "category": "LABOR_SLACK",
        "description": "Labor Market Slack (Inverse)",
        # Higher unemployment / claims -> Economic weakness -> BULLISH for Gold
        # Lower unemployment / claims -> Economic strength -> BEARISH for Gold
        "higher_bias": "BULLISH",
        "lower_bias": "BEARISH",
        "weight": 0.9,
    },
    {
        "keywords": ["gdp", "gross domestic product", "retail sales", "ism manufacturing", "ism services", "pmi", "consumer confidence"],
        "category": "GROWTH",
        "description": "Economic Growth & Consumer Spending",
        # Stronger growth -> Resilient USD -> BEARISH for Gold
        # Weaker growth -> Recession fears / Fed easing -> BULLISH for Gold
        "higher_bias": "BEARISH",
        "lower_bias": "BULLISH",
        "weight": 0.8,
    },
    {
        "keywords": ["interest rate", "fed funds", "federal funds", "cash rate", "rate decision", "fomc", "rba rate", "bank rate"],
        "category": "INTEREST_RATE",
        "description": "Central Bank Policy Rate",
        "higher_bias": "BEARISH",
        "lower_bias": "BULLISH",
        "weight": 1.0,
    },
]


def slugify(text: str) -> str:
    """Generate a clean slug for deduplication and indexing."""
    text = str(text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text.strip("_")


def parse_economic_value(val_str: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """
    Parse strings like '4.85%', '142K', '-0.3%', '$15.2B', '2.5M', '<0.1'
    Returns tuple of (numeric_value, unit).
    Example: '4.85%' -> (4.85, '%')
             '142K'  -> (142.0, 'K')
             '-0.3%' -> (-0.3, '%')
    """
    if val_str is None or not isinstance(val_str, (str, int, float)):
        return None, None

    s = str(val_str).strip()
    if not s or s in ("—", "-", "N/A", "None", "null", ""):
        return None, None

    unit = None
    if s.endswith("%"):
        unit = "%"
        s = s[:-1].strip()
    elif s.upper().endswith("K"):
        unit = "K"
        s = s[:-1].strip()
    elif s.upper().endswith("M"):
        unit = "M"
        s = s[:-1].strip()
    elif s.upper().endswith("B"):
        unit = "B"
        s = s[:-1].strip()

    # Remove currency symbols and comparison operators
    s = re.sub(r"[\$,£,€,¥,<,>]", "", s).strip()

    try:
        val = float(s)
        return val, unit
    except ValueError:
        return None, unit


def format_economic_delta(delta: Optional[float], unit: Optional[str]) -> str:
    """Format numeric difference into human readable format, e.g. '+0.25%' or '-15K'."""
    if delta is None:
        return "—"
    sign = "+" if delta > 0 else ""
    unit_str = unit or ""
    return f"{sign}{delta:.2f}{unit_str}".replace(".00", "")


def classify_indicator_bias(title: str, currency: str = "USD") -> Dict[str, Any]:
    """
    Classify what economic category this indicator belongs to and determine
    how changes against previous news impact Gold (XAU/USD).
    """
    t = str(title).lower()
    is_usd = str(currency).upper() == "USD"

    for rule in INDICATOR_RULES:
        for kw in rule["keywords"]:
            if kw in t:
                # For non-USD rates (e.g. AUD Cash Rate, EUR Interest Rate):
                # A foreign rate hike strengthens that currency vs USD -> mild bullish/cross-currency boost for Gold
                higher_bias = rule["higher_bias"]
                lower_bias = rule["lower_bias"]

                if not is_usd and rule["category"] == "INTEREST_RATE":
                    higher_bias = "BULLISH"
                    lower_bias = "BEARISH"

                return {
                    "category": rule["category"],
                    "description": rule["description"],
                    "higher_bias": higher_bias,
                    "lower_bias": lower_bias,
                    "weight": rule["weight"],
                    "is_key_driver": True,
                }

    # Default category for general news
    return {
        "category": "GENERAL",
        "description": "General Economic Release",
        "higher_bias": "NEUTRAL",
        "lower_bias": "NEUTRAL",
        "weight": 0.5,
        "is_key_driver": False,
    }


def compare_single_event_with_previous(
    current_event: Dict[str, Any],
    historical_releases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Deeply compare a single current news event against:
    1. The immediate previous figure (from Forex Factory calendar)
    2. Prior historical release records stored in MongoDB (if available)
    """
    title = current_event.get("title") or current_event.get("name") or "Economic Event"
    currency = str(current_event.get("currency", "USD")).upper()
    current_date = current_event.get("date") or current_event.get("time", "")[:10]

    raw_forecast = current_event.get("forecast")
    raw_previous = current_event.get("previous")
    raw_actual = current_event.get("actual")

    forecast_val, forecast_unit = parse_economic_value(raw_forecast)
    previous_val, previous_unit = parse_economic_value(raw_previous)
    actual_val, actual_unit = parse_economic_value(raw_actual)

    unit = forecast_unit or previous_unit or actual_unit or ""

    rule = classify_indicator_bias(title, currency)

    # 1. Compare Forecast vs Previous
    forecast_delta = None
    forecast_dir = "UNKNOWN"
    forecast_change_summary = ""

    if forecast_val is not None and previous_val is not None:
        forecast_delta = round(forecast_val - previous_val, 4)
        if abs(forecast_delta) < 0.0001:
            forecast_dir = "UNCHANGED"
            forecast_change_summary = f"Forecast unchanged from previous ({raw_previous})"
        elif forecast_delta > 0:
            forecast_dir = "HIGHER"
            forecast_change_summary = f"Forecast expecting increase of {format_economic_delta(forecast_delta, unit)} vs previous ({raw_previous})"
        else:
            forecast_dir = "LOWER"
            forecast_change_summary = f"Forecast expecting decrease of {format_economic_delta(forecast_delta, unit)} vs previous ({raw_previous})"
    elif raw_forecast and raw_previous:
        forecast_change_summary = f"Forecast: {raw_forecast} vs Previous: {raw_previous}"

    # 2. Compare Actual vs Forecast & Actual vs Previous (if released)
    actual_delta_vs_previous = None
    actual_dir_vs_previous = "PENDING"
    actual_surprise_delta = None
    actual_surprise_status = "PENDING"
    actual_summary = "Release pending"

    if actual_val is not None:
        if previous_val is not None:
            actual_delta_vs_previous = round(actual_val - previous_val, 4)
            if abs(actual_delta_vs_previous) < 0.0001:
                actual_dir_vs_previous = "UNCHANGED"
            elif actual_delta_vs_previous > 0:
                actual_dir_vs_previous = "HIGHER"
            else:
                actual_dir_vs_previous = "LOWER"

        if forecast_val is not None:
            actual_surprise_delta = round(actual_val - forecast_val, 4)
            if abs(actual_surprise_delta) < 0.0001:
                actual_surprise_status = "IN_LINE"
                actual_summary = f"Actual ({raw_actual}) released strictly in-line with forecast"
            elif actual_surprise_delta > 0:
                actual_surprise_status = "BEAT"
                actual_summary = f"Actual ({raw_actual}) beat forecast by {format_economic_delta(actual_surprise_delta, unit)}"
            else:
                actual_surprise_status = "MISS"
                actual_summary = f"Actual ({raw_actual}) missed forecast by {format_economic_delta(actual_surprise_delta, unit)}"
        else:
            actual_summary = f"Actual released: {raw_actual}"

    # 3. Determine Gold (XAU/USD) Market Sentiment Bias
    # If actual has released, use actual; otherwise use forecast expectation vs previous!
    effective_direction = actual_dir_vs_previous if actual_val is not None else forecast_dir
    effective_surprise = actual_surprise_status if actual_val is not None else None

    gold_bias = "NEUTRAL"
    gold_rationale = ""

    if effective_direction == "HIGHER":
        gold_bias = rule["higher_bias"]
        if rule["category"] == "INFLATION":
            gold_rationale = f"Higher {rule['description']} ({format_economic_delta(forecast_delta, unit)}) elevates Fed hawkish pressure, posing bearish headwinds for Gold."
        elif rule["category"] in ("LABOR_STRENGTH", "GROWTH"):
            gold_rationale = f"Economic expansion indicators ({format_economic_delta(forecast_delta, unit)}) reinforce USD resilience, exerting downward pressure on XAU/USD."
        elif rule["category"] == "LABOR_SLACK":
            gold_rationale = f"Rising labor slack/unemployment is dollar-dilutive, offering a bullish catalyst for Gold."
        elif rule["category"] == "INTEREST_RATE":
            gold_rationale = f"Rate increase expectation ({format_economic_delta(forecast_delta, unit)}) drives monetary tightening."
        else:
            gold_rationale = f"Indicator is tracking higher than previous release ({raw_previous})."

    elif effective_direction == "LOWER":
        gold_bias = rule["lower_bias"]
        if rule["category"] == "INFLATION":
            gold_rationale = f"Cooling inflation ({format_economic_delta(forecast_delta, unit)}) encourages rate cut expectations, acting as a bullish tailwind for Gold."
        elif rule["category"] in ("LABOR_STRENGTH", "GROWTH"):
            gold_rationale = f"Slowing economic metrics ({format_economic_delta(forecast_delta, unit)}) weaken the US Dollar, favoring bullish Gold momentum."
        elif rule["category"] == "LABOR_SLACK":
            gold_rationale = f"Declining jobless metrics highlight tight labor markets, favoring the Dollar over Gold."
        elif rule["category"] == "INTEREST_RATE":
            gold_rationale = f"Dovish rate trajectory lowers opportunity cost of holding non-yielding Gold."
        else:
            gold_rationale = f"Indicator is tracking lower than previous release ({raw_previous})."

    else:
        gold_bias = "NEUTRAL"
        gold_rationale = f"Data expected or delivered in-line with previous baseline ({raw_previous or 'baseline'}), minimizing standalone directional disruption."

    # If actual surprised against forecast, that supercedes forecast expectation:
    if actual_surprise_status == "BEAT":
        gold_bias = rule["higher_bias"]
        gold_rationale = f"Upside surprise: Actual ({raw_actual}) beat forecast ({raw_forecast}), triggering {gold_bias.lower()} reaction for Gold."
    elif actual_surprise_status == "MISS":
        gold_bias = rule["lower_bias"]
        gold_rationale = f"Downside miss: Actual ({raw_actual}) fell short of forecast ({raw_forecast}), sparking {gold_bias.lower()} boost for Gold."

    # 4. Integrate Multi-Release MongoDB History (if available)
    historical_summary = []
    historical_trend = "INSUFFICIENT_HISTORY"

    if historical_releases and len(historical_releases) > 0:
        clean_hist = []
        for hr in historical_releases[:5]:
            clean_hist.append({
                "date": hr.get("date"),
                "time": hr.get("time"),
                "actual": hr.get("actual"),
                "forecast": hr.get("forecast"),
                "previous": hr.get("previous"),
                "surprise": hr.get("surprise_direction") or hr.get("surprise"),
            })
        historical_summary = clean_hist

        # Detect trend across previous actuals
        past_actuals = [parse_economic_value(h.get("actual"))[0] for h in clean_hist if parse_economic_value(h.get("actual"))[0] is not None]
        if len(past_actuals) >= 2:
            if past_actuals[0] > past_actuals[-1]:
                historical_trend = "UPWARD_TREND"
            elif past_actuals[0] < past_actuals[-1]:
                historical_trend = "DOWNWARD_TREND"
            else:
                historical_trend = "RANGE_BOUND"

    return {
        "title": title,
        "currency": currency,
        "date": current_date,
        "impact": current_event.get("impact", "high"),
        "category": rule["category"],
        "category_description": rule["description"],
        "is_key_gold_driver": rule["is_key_driver"],
        "raw_figures": {
            "forecast": raw_forecast,
            "previous": raw_previous,
            "actual": raw_actual,
        },
        "numeric_figures": {
            "forecast": forecast_val,
            "previous": previous_val,
            "actual": actual_val,
            "unit": unit,
        },
        "forecast_vs_previous": {
            "direction": forecast_dir,
            "delta": forecast_delta,
            "formatted_delta": format_economic_delta(forecast_delta, unit),
            "summary": forecast_change_summary,
        },
        "actual_vs_previous": {
            "direction": actual_dir_vs_previous,
            "delta": actual_delta_vs_previous,
            "formatted_delta": format_economic_delta(actual_delta_vs_previous, unit),
        },
        "actual_vs_forecast": {
            "status": actual_surprise_status,
            "surprise_delta": actual_surprise_delta,
            "formatted_surprise": format_economic_delta(actual_surprise_delta, unit),
            "summary": actual_summary,
        },
        "gold_impact": {
            "bias": gold_bias,
            "rationale": gold_rationale,
            "weight": rule["weight"],
        },
        "historical_context": {
            "previous_releases_count": len(historical_summary),
            "trend_across_releases": historical_trend,
            "previous_records": historical_summary,
        },
    }


def analyze_all_events_with_previous_news(
    events: List[Dict[str, Any]],
    db_lookup: bool = True,
) -> Dict[str, Any]:
    """
    Perform complete comparative analysis of all events for today against previous news.
    Queries MongoDB for historical occurrences of each event to build multi-cycle trend analysis.
    """
    if not events:
        return {
            "has_comparisons": False,
            "total_events_analyzed": 0,
            "overall_gold_sentiment": "NEUTRAL",
            "sentiment_score": 0.0,
            "macro_summary": "No economic events scheduled today for previous news comparison.",
            "comparisons": [],
            "key_driver_comparisons": [],
            "bullish_catalysts": [],
            "bearish_catalysts": [],
        }

    # Optional historical lookup in MongoDB
    history_lookup_fn = None
    if db_lookup:
        try:
            from app.mongo_db import get_previous_events_for_event
            history_lookup_fn = get_previous_events_for_event
        except Exception as e:
            logger.debug("MongoDB historical lookup unavailable: %s", e)

    comparisons = []
    key_driver_comparisons = []
    bullish_catalysts = []
    bearish_catalysts = []
    sentiment_score = 0.0
    total_weights = 0.0

    today_str = datetime.now(timezone.utc).date().isoformat()

    for evt in events:
        title = evt.get("title") or evt.get("name", "")
        currency = str(evt.get("currency", "USD")).upper()

        historical_records = []
        if history_lookup_fn:
            try:
                historical_records = history_lookup_fn(
                    title=title,
                    currency=currency,
                    before_date=evt.get("date") or today_str,
                    limit=5,
                )
            except Exception as hist_err:
                logger.debug("Failed querying previous events for %s: %s", title, hist_err)

        comp = compare_single_event_with_previous(evt, historical_records)
        comparisons.append(comp)

        bias = comp["gold_impact"]["bias"]
        weight = comp["gold_impact"]["weight"]
        total_weights += weight

        if bias == "BULLISH":
            sentiment_score += weight
            bullish_catalysts.append({
                "title": comp["title"],
                "currency": comp["currency"],
                "reason": comp["gold_impact"]["rationale"],
            })
        elif bias == "BEARISH":
            sentiment_score -= weight
            bearish_catalysts.append({
                "title": comp["title"],
                "currency": comp["currency"],
                "reason": comp["gold_impact"]["rationale"],
            })

        if comp["is_key_gold_driver"]:
            key_driver_comparisons.append(comp)

    normalized_score = round(sentiment_score / max(total_weights, 1.0), 2)

    if normalized_score >= 0.25:
        overall_sentiment = "BULLISH"
    elif normalized_score <= -0.25:
        overall_sentiment = "BEARISH"
    else:
        overall_sentiment = "NEUTRAL"

    # Build coherent macro narrative
    narrative_parts = []
    if overall_sentiment == "BULLISH":
        narrative_parts.append(
            f"PREVIOUS NEWS COMPARISON: Current forecasts and releases demonstrate a net BULLISH bias for Gold (XAU/USD). "
            f"Key metrics show cooling economic momentum or inflation deceleration relative to previous releases."
        )
    elif overall_sentiment == "BEARISH":
        narrative_parts.append(
            f"PREVIOUS NEWS COMPARISON: Current forecasts and releases indicate a net BEARISH bias for Gold (XAU/USD). "
            f"Economic data is forecasted or releasing stronger than previous prints, favoring US Dollar strength."
        )
    else:
        narrative_parts.append(
            f"PREVIOUS NEWS COMPARISON: Economic figures reflect balanced or neutral baseline shifts against previous news. "
            f"Gold price action is expected to remain largely technically driven unless significant surprises emerge."
        )

    if key_driver_comparisons:
        top_driver = key_driver_comparisons[0]
        narrative_parts.append(
            f"Primary catalyst '{top_driver['title']}' ({top_driver['currency']}): {top_driver['forecast_vs_previous']['summary']}."
        )

    macro_summary = " ".join(narrative_parts)

    return {
        "has_comparisons": True,
        "total_events_analyzed": len(comparisons),
        "overall_gold_sentiment": overall_sentiment,
        "sentiment_score": normalized_score,
        "macro_summary": macro_summary,
        "comparisons": comparisons,
        "key_driver_comparisons": key_driver_comparisons,
        "bullish_catalysts": bullish_catalysts,
        "bearish_catalysts": bearish_catalysts,
        "analyzed_at_utc": datetime.now(timezone.utc).isoformat(),
    }

