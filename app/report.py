"""
Trading Report & Analytics Engine for AI Gold Trading System.

Calculates comprehensive performance statistics, win rate, profit factor,
drawdown, directional analysis, and integrates active positions and scheduled signals.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from app.trade_manager import get_trade_history, load_active_trade
from app.scheduler import get_latest_analysis

logger = logging.getLogger("trading-report")

POINT_USD_VALUE = 100.0  # Estimated USD per point on standard 1.0 lot (configurable)


def calculate_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate key trading performance metrics from a list of closed trades."""
    if not trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "breakeven_trades": 0,
            "win_rate_pct": 0.0,
            "loss_rate_pct": 0.0,
            "total_pnl_points": 0.0,
            "total_pnl_usd": 0.0,
            "gross_profit_points": 0.0,
            "gross_loss_points": 0.0,
            "profit_factor": 0.0,
            "avg_trade_points": 0.0,
            "avg_win_points": 0.0,
            "avg_loss_points": 0.0,
            "risk_reward_ratio": 0.0,
            "largest_win_points": 0.0,
            "largest_loss_points": 0.0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
            "max_drawdown_points": 0.0,
            "direction_breakdown": {
                "BUY": {"count": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "pnl_points": 0.0},
                "SELL": {"count": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "pnl_points": 0.0},
            },
            "status_breakdown": {},
        }

    total_trades = len(trades)
    winning_trades = 0
    losing_trades = 0
    breakeven_trades = 0

    gross_profit = 0.0
    gross_loss = 0.0
    total_pnl = 0.0

    wins_list: List[float] = []
    losses_list: List[float] = []

    buy_stats = {"count": 0, "wins": 0, "losses": 0, "pnl_points": 0.0}
    sell_stats = {"count": 0, "wins": 0, "losses": 0, "pnl_points": 0.0}
    status_counts: Dict[str, int] = {}

    # Sort chronological (oldest to newest) to calculate streaks and drawdown
    chronological_trades = sorted(
        trades,
        key=lambda x: str(x.get("exit_time") or x.get("entry_time") or x.get("created_at") or "")
    )

    current_win_streak = 0
    max_win_streak = 0
    current_loss_streak = 0
    max_loss_streak = 0

    cumulative_pnl = 0.0
    peak_pnl = 0.0
    max_drawdown = 0.0

    for t in chronological_trades:
        pnl = float(t.get("final_pnl_points") or t.get("pnl_points") or 0.0)
        direction = str(t.get("direction", "BUY")).upper()
        status = str(t.get("status", "CLOSED"))

        status_counts[status] = status_counts.get(status, 0) + 1
        total_pnl += pnl

        # Cumulative drawdown calculation
        cumulative_pnl += pnl
        if cumulative_pnl > peak_pnl:
            peak_pnl = cumulative_pnl
        drawdown = peak_pnl - cumulative_pnl
        if drawdown > max_drawdown:
            max_drawdown = drawdown

        # Streaks & Win/Loss classification
        if pnl > 0.0:
            winning_trades += 1
            gross_profit += pnl
            wins_list.append(pnl)
            current_win_streak += 1
            current_loss_streak = 0
            if current_win_streak > max_win_streak:
                max_win_streak = current_win_streak

            if direction == "BUY":
                buy_stats["wins"] += 1
            elif direction == "SELL":
                sell_stats["wins"] += 1

        elif pnl < 0.0:
            losing_trades += 1
            gross_loss += abs(pnl)
            losses_list.append(abs(pnl))
            current_loss_streak += 1
            current_win_streak = 0
            if current_loss_streak > max_loss_streak:
                max_loss_streak = current_loss_streak

            if direction == "BUY":
                buy_stats["losses"] += 1
            elif direction == "SELL":
                sell_stats["losses"] += 1
        else:
            breakeven_trades += 1

        if direction == "BUY":
            buy_stats["count"] += 1
            buy_stats["pnl_points"] += pnl
        elif direction == "SELL":
            sell_stats["count"] += 1
            sell_stats["pnl_points"] += pnl

    win_rate_pct = round((winning_trades / total_trades) * 100, 1) if total_trades > 0 else 0.0
    loss_rate_pct = round((losing_trades / total_trades) * 100, 1) if total_trades > 0 else 0.0

    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (round(gross_profit, 2) if gross_profit > 0 else 0.0)
    avg_trade_points = round(total_pnl / total_trades, 2) if total_trades > 0 else 0.0
    avg_win = round(sum(wins_list) / len(wins_list), 2) if wins_list else 0.0
    avg_loss = round(sum(losses_list) / len(losses_list), 2) if losses_list else 0.0
    rr_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

    largest_win = round(max(wins_list), 2) if wins_list else 0.0
    largest_loss = round(max(losses_list), 2) if losses_list else 0.0

    buy_win_rate = round((buy_stats["wins"] / buy_stats["count"] * 100), 1) if buy_stats["count"] > 0 else 0.0
    sell_win_rate = round((sell_stats["wins"] / sell_stats["count"] * 100), 1) if sell_stats["count"] > 0 else 0.0

    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "breakeven_trades": breakeven_trades,
        "win_rate_pct": win_rate_pct,
        "loss_rate_pct": loss_rate_pct,
        "total_pnl_points": round(total_pnl, 2),
        "total_pnl_usd": round(total_pnl * POINT_USD_VALUE, 2),
        "gross_profit_points": round(gross_profit, 2),
        "gross_loss_points": round(gross_loss, 2),
        "profit_factor": profit_factor,
        "avg_trade_points": avg_trade_points,
        "avg_win_points": avg_win,
        "avg_loss_points": avg_loss,
        "risk_reward_ratio": rr_ratio,
        "largest_win_points": largest_win,
        "largest_loss_points": largest_loss,
        "max_consecutive_wins": max_win_streak,
        "max_consecutive_losses": max_loss_streak,
        "max_drawdown_points": round(max_drawdown, 2),
        "direction_breakdown": {
            "BUY": {
                "count": buy_stats["count"],
                "wins": buy_stats["wins"],
                "losses": buy_stats["losses"],
                "win_rate_pct": buy_win_rate,
                "pnl_points": round(buy_stats["pnl_points"], 2),
            },
            "SELL": {
                "count": sell_stats["count"],
                "wins": sell_stats["wins"],
                "losses": sell_stats["losses"],
                "win_rate_pct": sell_win_rate,
                "pnl_points": round(sell_stats["pnl_points"], 2),
            },
        },
        "status_breakdown": status_counts,
    }


def generate_trading_report(trade_limit: int = 100) -> Dict[str, Any]:
    """
    Generate comprehensive trading report for frontend dashboard and analytics display.
    """
    try:
        # 1. Retrieve closed trade history
        raw_trades = get_trade_history(limit=trade_limit)

        # Deduplicate trades by ID
        seen_ids = set()
        trades = []
        for t in raw_trades:
            t_id = t.get("id")
            if t_id and t_id in seen_ids:
                continue
            if t_id:
                seen_ids.add(t_id)
            trades.append(t)

        metrics = calculate_metrics(trades)

        # 2. Get active position
        active_trade = load_active_trade()

        # 3. Get latest analysis
        latest_analysis = get_latest_analysis() or {}

        # 4. Format 6 most recent trades for dashboard quick list
        sorted_recent = sorted(
            trades,
            key=lambda x: str(x.get("exit_time") or x.get("entry_time") or x.get("created_at") or ""),
            reverse=True
        )[:6]

        formatted_recent = []
        for t in sorted_recent:
            pnl_pts = float(t.get("final_pnl_points") or t.get("pnl_points") or 0.0)
            formatted_recent.append({
                "id": t.get("id", "TRD"),
                "side": t.get("direction", "BUY"),
                "entry": f"{float(t.get('entry_price', 0.0)):.2f}" if t.get("entry_price") else "—",
                "exit": f"{float(t.get('exit_price', 0.0)):.2f}" if t.get("exit_price") else "—",
                "pnl_points": round(pnl_pts, 2),
                "pnl": f"{'+' if pnl_pts >= 0 else ''}${pnl_pts * POINT_USD_VALUE:,.2f}",
                "status": t.get("status", "CLOSED"),
                "exit_time": t.get("exit_time"),
                "exit_reason": t.get("exit_reason"),
            })

        # 5. Extract upcoming news if available
        upcoming_news: List[Dict[str, Any]] = []
        from app.news_filter import get_events_file
        news_file = get_events_file()
        if news_file.exists():
            try:
                raw_json = json.loads(news_file.read_text(encoding="utf-8"))
                events_list = raw_json.get("events") if isinstance(raw_json, dict) else (raw_json if isinstance(raw_json, list) else [])
                if isinstance(events_list, list):
                    for evt in events_list[:6]:
                        upcoming_news.append({
                            "time": evt.get("time") or evt.get("date", "Today"),
                            "currency": evt.get("currency") or evt.get("country", "USD"),
                            "impact": str(evt.get("impact", "HIGH")).upper(),
                            "title": evt.get("title") or evt.get("name", "High Impact Event"),
                            "forecast": f"F: {evt.get('forecast')}" if evt.get('forecast') else (evt.get("previous") or "High Volatility"),
                        })
            except Exception as e:
                logger.warning("Could not read events.json for report: %s", e)

        report = {
            "success": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "active_trade": active_trade,
            "market_snapshot": {
                "symbol": latest_analysis.get("symbol", "XAU/USD"),
                "price": latest_analysis.get("price"),
                "macro_trend": latest_analysis.get("multi_timeframe", {}).get("htf_trend", "BULLISH"),
                "htf_rsi": latest_analysis.get("multi_timeframe", {}).get("htf_rsi"),
                "entry_signal": latest_analysis.get("multi_timeframe", {}).get("entry_signal", "WAIT"),
                "action": latest_analysis.get("action", "HOLD"),
                "pivot_point": latest_analysis.get("support_resistance", {}).get("pivot_point"),
            },
            "recent_trades": formatted_recent,
            "upcoming_news": upcoming_news,
        }
        return report

    except Exception as exc:
        logger.error("Failed generating trading report: %s", exc, exc_info=True)
        return {
            "success": False,
            "error": str(exc),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": calculate_metrics([]),
        }


def get_performance_summary() -> Dict[str, Any]:
    """Quick KPI summary of trading performance."""
    report = generate_trading_report(trade_limit=50)
    metrics = report.get("metrics", {})
    return {
        "total_trades": metrics.get("total_trades", 0),
        "win_rate_pct": metrics.get("win_rate_pct", 0.0),
        "total_pnl_points": metrics.get("total_pnl_points", 0.0),
        "total_pnl_usd": metrics.get("total_pnl_usd", 0.0),
        "profit_factor": metrics.get("profit_factor", 0.0),
        "max_drawdown_points": metrics.get("max_drawdown_points", 0.0),
        "active_position": report.get("active_trade") is not None,
    }


if __name__ == "__main__":
    report_data = generate_trading_report()
    print("Report Summary:")
    print(json.dumps(report_data.get("metrics"), indent=2))
