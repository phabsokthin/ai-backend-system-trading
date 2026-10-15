import os
import time
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

from app.market_data import get_gold_candles
from app.indicators import (
    generate_signal,
    generate_multi_timeframe_signal,
    calculate_support_resistance,
    analyze_timeframe_trend,
    format_candles_for_frontend,
)
from app.news_filter import check_news_filter
from app.ai_commentary import get_ai_commentary
from app.risk import calculate_risk_levels
from app.scheduler import scheduled_analysis, get_latest_analysis
from app.news_fetcher import refresh_news_calendar
from app.trade_manager import (
    process_trade_signal,
    load_active_trade,
    reset_active_trade,
)

from app.live_market import market
from pydantic import BaseModel, Field
from app.mt5_bridge import send_order, get_account_summary, get_open_positions, close_position

load_dotenv()

app = FastAPI(
    title="Python Gold Trading Analysis API",
    description="Automated XAU/USD technical analysis, Support & Resistance levels, multi-timeframe trends, auto TP/SL setup, duplicate entry protection, and economic news filter.",
    version="2.1.0",
)

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Background scheduler: one instance per API process
scheduler = BackgroundScheduler(timezone="UTC")


@app.on_event("startup")

def start_background_scheduler():
    # Initial news calendar fetch
    
    try:
        refresh_news_calendar()
        market.start()

        print("Economic news calendar initialized successfully")
    except Exception as e:
        print(f"Warning: Initial news refresh encountered an issue: {e}")

    # Scheduled analysis job (every 5 minutes)
    scheduler.add_job(
        scheduled_analysis,
        trigger="interval",
        minutes=5,
        id="gold_analysis",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc),
    )

    # Scheduled news refresh job (every 30 minutes)
    scheduler.add_job(
        refresh_news_calendar,
        trigger="interval",
        minutes=30,
        id="news_calendar_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    print("Gold analysis and news schedulers started")


@app.on_event("shutdown")
def stop_background_scheduler():
    market.stop()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("Gold analysis scheduler stopped")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "python-gold-trading",
        "scheduler_running": scheduler.running,
        "live_market": {
            "status": market.connection_status,
            "current_price": market.current_price,
            "last_tick_at": market.last_tick_at,
        },
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/live-price")
def get_live_price():
    """Fast real-time price snapshot from Twelve Data WebSocket for Next.js tickers."""
    current_price = market.current_price
    if current_price is None:
        try:
            candles = get_gold_candles(interval="5min", outputsize=5)
            current_price = float(candles.iloc[-1]["close"])
        except Exception:
            pass

    return {
        "success": True,
        "symbol": "XAU/USD",
        "price": current_price,
        "status": market.connection_status,
        "last_tick_at": market.last_tick_at,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.websocket("/ws/live")
async def websocket_live_market(websocket: WebSocket):
    """
    Real-Time WebSocket Feed for Next.js:
    Streams live XAU/USD price ticks directly to the frontend chart.
    """
    await websocket.accept()
    try:
        while True:
            current_price = market.current_price
            await websocket.send_json({
                "type": "tick",
                "symbol": "XAU/USD",
                "price": current_price,
                "status": market.connection_status,
                "last_tick_at": market.last_tick_at,
                "timestamp": time.time(),
            })
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.get("/api/candles")
@app.get("/api/v1/candles")
def get_candles_for_nextjs(
    symbol: str = Query("XAU/USD", description="Symbol name, default XAU/USD"),
    interval: str = Query("5min", description="Timeframe interval: 5min, 15min, 1h, 4h, 1day"),
    limit: int = Query(100, ge=1, le=500, description="Number of recent candlestick records, default 100"),
    include_indicators: bool = Query(True, description="Include EMA9, EMA21, EMA50, RSI14, ATR14 overlays"),
):
    """
    Real-Time Candlestick Endpoint for Next.js Frontend:
    Uses LiveGoldMarket WebSocket tick stream to update the currently forming candle in real time,
    with base historical caching to avoid Twelve Data REST rate limits.
    """
    try:
        # Prefer live real-time market with in-memory WebSocket ticks
        result = market.get_candles(interval=interval, limit=limit)
        return result
    except Exception as exc:
        # Fallback to REST API if WebSocket market is initializing or temporarily busy
        try:
            raw_candles = get_gold_candles(interval=interval, outputsize=max(limit + 50, 100))
            if include_indicators:
                candles_data = format_candles_for_frontend(raw_candles, limit=limit)
            else:
                recent = raw_candles.tail(limit).copy()
                recent["datetime"] = recent["datetime"].astype(str)
                candles_data = recent.to_dict(orient="records")

            current_price = float(raw_candles.iloc[-1]["close"]) if len(raw_candles) > 0 else None

            return {
                "success": True,
                "symbol": symbol,
                "interval": interval,
                "count": len(candles_data),
                "current_price": current_price,
                "connection_status": market.connection_status,
                "candles": candles_data,
            }
        except Exception as fb_exc:
            raise HTTPException(
                status_code=502,
                detail=f"Unable to retrieve candles: {exc} | {fb_exc}",
            )


@app.get("/api/chart-data")
def get_chart_data_for_nextjs(
    interval: str = Query("5min", description="Entry timeframe: 5min or 15min"),
    analysis_timeframe: str = Query("1h", description="Macro trend & S&R timeframe: 1h, 4h, or 1day"),
    limit: int = Query(100, ge=1, le=500, description="Number of candlestick records, default 100"),
):
    """
    Unified Charting Endpoint for Next.js:
    Returns both candlestick history and active chart overlay levels (SL, TP, S&R, Pivot).
    """
    try:
        entry_candles = get_gold_candles(interval=interval, outputsize=max(limit + 50, 100))
        htf_candles = None
        try:
            htf_candles = get_gold_candles(interval=analysis_timeframe, outputsize=100)
        except Exception:
            pass

        analysis = generate_multi_timeframe_signal(
            entry_df=entry_candles,
            htf_df=htf_candles,
            entry_tf=interval,
            htf_tf=analysis_timeframe,
        )

        news = check_news_filter()
        sr_levels = analysis.get("support_resistance", {})
        price = analysis.get("price")
        raw_signal = analysis.get("signal", "WAIT")
        final_signal = "WAIT" if not news.get("allowed", False) else raw_signal

        risk_calc = calculate_risk_levels(
            signal=raw_signal if raw_signal in ("BUY", "SELL") else None,
            price=price,
            atr=analysis.get("technical_indicators", {}).get("atr14"),
            support_resistance=sr_levels,
        )

        trade_exec = process_trade_signal(
            current_price=price,
            signal=final_signal,
            sl=risk_calc.get("sl") if risk_calc else None,
            tp=risk_calc.get("tp") if risk_calc else None,
            reason=analysis.get("reason", ""),
            timeframe=interval,
        )

        try:
            live_res = market.get_candles(interval=interval, limit=limit)
            chart_candles = live_res.get("candles", [])
            if market.current_price:
                price = market.current_price
        except Exception:
            chart_candles = format_candles_for_frontend(entry_candles, limit=limit)

        return {
            "success": True,
            "symbol": "XAU/USD",
            "interval": interval,
            "analysis_timeframe": analysis_timeframe,
            "current_price": price,
            "connection_status": market.connection_status,
            "last_tick_at": market.last_tick_at,
            "signal": final_signal,
            "action": trade_exec.get("action"),
            "trade_status": trade_exec.get("trade_status"),
            "take_profit": trade_exec.get("tp"),
            "stop_loss": trade_exec.get("sl"),
            "chart_levels": {
                "entry_price": trade_exec.get("entry_price", price),
                "take_profit": trade_exec.get("tp"),
                "stop_loss": trade_exec.get("sl"),
                "pivot_point": sr_levels.get("pivot_point"),
                "nearest_support": sr_levels.get("nearest_support"),
                "nearest_resistance": sr_levels.get("nearest_resistance"),
                "support_levels": [
                    sr_levels.get("classic", {}).get("s1"),
                    sr_levels.get("classic", {}).get("s2"),
                    sr_levels.get("classic", {}).get("s3"),
                ],
                "resistance_levels": [
                    sr_levels.get("classic", {}).get("r1"),
                    sr_levels.get("classic", {}).get("r2"),
                    sr_levels.get("classic", {}).get("r3"),
                ],
            },
            "active_position": trade_exec.get("active_position"),
            "candles_count": len(chart_candles),
            "candles": chart_candles,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to retrieve chart data for Next.js: {exc}",
        )


@app.get("/market-data/gold")
def gold_market_data(
    interval: str = Query("5min", description="Timeframe interval: 5min, 15min, 1h, 4h, 1day"),
    limit: int = Query(60, ge=1, le=200, description="Number of recent candles to return"),
    include_indicators: bool = Query(True, description="Include indicator overlays (EMA9, EMA21, EMA50, RSI, ATR)"),
):
    try:
        candles = get_gold_candles(interval=interval, outputsize=max(limit + 50, 100))

        if include_indicators:
            formatted_candles = format_candles_for_frontend(candles, limit=limit)
        else:
            recent = candles.tail(limit).copy()
            recent["datetime"] = recent["datetime"].astype(str)
            formatted_candles = recent.to_dict(orient="records")

        return {
            "symbol": "XAU/USD",
            "interval": interval,
            "count": len(formatted_candles),
            "candles": formatted_candles,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to retrieve valid gold market data: {exc}",
        )


@app.get("/analysis/gold")
def gold_analysis(
    analysis_timeframe: str = Query(
        "1h",
        regex="^(1h|4h|1day|1d)$",
        description="Higher Timeframe for macro trend and Support/Resistance: 1h, 4h, or 1day",
    ),
    entry_timeframe: str = Query(
        "5min",
        regex="^(5min|15min|5m|15m)$",
        description="Entry Timeframe for micro trigger: 5min or 15min",
    ),
):
    """
    Comprehensive Gold (XAU/USD) Analysis with Duplicate Entry Protection:
    - Multi-timeframe analysis (HTF: 1H, 4H, 1D; Entry LTF: 5min or 15min)
    - Full Support & Resistance levels (Pivot Points P, R1-R3, S1-S3, Swing Highs/Lows, Nearest S&R)
    - Auto Trade Setup with confirmed Take Profit (TP, TP1-TP3) and Stop Loss (SL)
    - Duplicate Entry Protection: If a position is active, it prevents duplicate entries and reports HOLD
    - Mandatory Confirmed SL & TP: Entries are blocked if SL or TP are missing
    - Economic News Analysis with real-time volatility notes and safety filter
    - Clear JSON formatted message return
    """
    try:
        # 1. Fetch Entry Timeframe candles
        entry_candles = get_gold_candles(interval=entry_timeframe, outputsize=100)

        # 2. Fetch Higher Timeframe candles for S&R and macro trend
        htf_candles = None
        try:
            htf_candles = get_gold_candles(interval=analysis_timeframe, outputsize=100)
        except Exception as e:
            print(f"Warning: could not fetch {analysis_timeframe} candles: {e}")

        # 3. Generate multi-timeframe technical analysis with S&R and setup
        analysis = generate_multi_timeframe_signal(
            entry_df=entry_candles,
            htf_df=htf_candles,
            entry_tf=entry_timeframe,
            htf_tf=analysis_timeframe,
        )

        # 4. Check Economic News calendar & news analysis
        news = check_news_filter()
        news_analysis = news.get("news_analysis")

        raw_signal = analysis.get("signal", "WAIT")
        raw_reason = analysis.get("reason", "No valid technical signal")
        price = analysis.get("price")
        sr_levels = analysis.get("support_resistance", {})
        technical_setup = analysis.get("setup")
        has_setup = analysis.get("has_setup", False)

        final_signal = raw_signal
        final_reason = raw_reason

        # News / Risk filter has top execution priority
        news_blocked = not news.get("allowed", False)
        if news_blocked:
            final_signal = "WAIT"
            final_reason = f"Execution blocked by news filter: {news.get('reason')}"

        # 5. Calculate SL and TP levels
        risk_calc = calculate_risk_levels(
            signal=raw_signal if raw_signal in ("BUY", "SELL") else None,
            price=price,
            atr=analysis.get("technical_indicators", {}).get("atr14"),
            support_resistance=sr_levels,
        )

        potential_sl = risk_calc.get("sl") if risk_calc else (technical_setup.get("sl") if technical_setup else None)
        potential_tp = risk_calc.get("tp") if risk_calc else (technical_setup.get("tp") if technical_setup else None)

        # 6. Execute Trade Guard: Prevent duplicate entry and require confirmed SL/TP
        trade_exec = process_trade_signal(
            current_price=price,
            signal=final_signal,
            sl=potential_sl,
            tp=potential_tp,
            reason=final_reason,
            timeframe=entry_timeframe,
        )

        effective_sl = trade_exec.get("sl", potential_sl)
        effective_tp = trade_exec.get("tp", potential_tp)

        # Build auto setup structure
        setup_data = None
        if technical_setup or risk_calc:
            base_setup = technical_setup or {}
            setup_status = "PENDING_NEWS_CLEARANCE" if news_blocked else trade_exec.get("trade_status", "ACTIVE")

            setup_data = {
                "status": setup_status,
                "direction": raw_signal,
                "entry_price": trade_exec.get("entry_price", price),
                "stop_loss": effective_sl,
                "take_profit": effective_tp,
                "sl": effective_sl,
                "tp": effective_tp,
                "tp1": risk_calc.get("tp1") if risk_calc else base_setup.get("tp1"),
                "tp2": risk_calc.get("tp2") if risk_calc else base_setup.get("tp2"),
                "tp3": risk_calc.get("tp3") if risk_calc else base_setup.get("tp3"),
                "risk_reward_ratio": risk_calc.get("risk_reward_ratio", 2.0) if risk_calc else 2.0,
                "risk_distance": risk_calc.get("sl_distance") if risk_calc else base_setup.get("risk_distance"),
                "reward_distance": risk_calc.get("tp_distance") if risk_calc else base_setup.get("reward_distance"),
                "confidence": base_setup.get("confidence", "MEDIUM"),
                "reasons": base_setup.get("reasons", [raw_reason]),
            }

            if news_blocked:
                setup_data["news_restriction"] = f"Trade setup is technically ready ({raw_signal}), but entry is delayed until high-impact news window clears."

        # 7. Optional AI commentary
        ai = get_ai_commentary(analysis, news_analysis)

        return {
            "symbol": "XAU/USD",
            "price": price,
            "action": trade_exec.get("action"),
            "signal": final_signal,
            "is_new_entry": trade_exec.get("is_new_entry"),
            "duplicate_prevented": trade_exec.get("duplicate_prevented"),
            "trade_status": trade_exec.get("trade_status"),
            "message": trade_exec.get("message"),
            "reason": final_reason,
            "has_setup": has_setup,
            "take_profit": effective_tp,
            "stop_loss": effective_sl,
            "tp": effective_tp,
            "sl": effective_sl,
            "active_position": trade_exec.get("active_position"),
            "setup": setup_data,
            "support_resistance": sr_levels,
            "multi_timeframe": {
                "analysis_timeframe": analysis_timeframe,
                "htf_trend": analysis.get("higher_timeframe_analysis", {}).get("trend"),
                "htf_rsi": analysis.get("higher_timeframe_analysis", {}).get("rsi14"),
                "entry_timeframe": entry_timeframe,
                "ltf_signal": raw_signal,
                "timeframe_aligned": raw_signal != "WAIT" and (raw_signal in analysis.get("higher_timeframe_analysis", {}).get("trend", "")),
            },
            "technical_indicators": analysis.get("technical_indicators"),
            "news_filter": news,
            "news_analysis": news_analysis,
            "risk_levels": risk_calc,
            "ai_analysis": ai,
            "trading_mode": "analysis_only",
            "real_orders_enabled": False,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Analysis failed; no trade should be placed: {exc}",
        )


@app.get("/trades/active")
def get_active_position():
    """Retrieve the currently open trade position, entry details, and active SL/TP."""
    trade = load_active_trade()
    if trade is None:
        return {
            "status": "FLAT",
            "active": False,
            "message": "No active position currently open",
            "position": None,
        }
    return {
        "status": "ACTIVE_POSITION",
        "active": True,
        "message": f"Active {trade.get('direction')} position open from {trade.get('entry_price')}",
        "position": trade,
    }


@app.post("/trades/reset")
def reset_position():
    """Emergency reset to close/clear the active position state."""
    return reset_active_trade()


class OrderRequest(BaseModel):
    action: str = Field(..., description="BUY or SELL")
    volume: float = Field(default=0.01, description="Lot size, e.g. 0.01")
    symbol: str = Field(default="XAUUSD", description="Symbol, e.g. XAUUSD")
    sl: Optional[float] = Field(default=None, description="Stop Loss price")
    tp: Optional[float] = Field(default=None, description="Take Profit price")
    comment: Optional[str] = Field(default="GoldPilot Web Order", description="Order comment")


@app.post("/api/trades/order")
@app.post("/trades/order")
def execute_trade_order(req: OrderRequest):
    """Execute a real-time BUY or SELL order directly in MetaTrader 5."""
    result = send_order(
        action=req.action,
        volume=req.volume,
        symbol=req.symbol,
        sl=req.sl,
        tp=req.tp,
        comment=req.comment or "GoldPilot Web Order",
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "Order failed to execute in MT5"),
        )
    return result


@app.get("/api/trades/account")
@app.get("/trades/account")
def get_mt5_account():
    """Get connected MT5 account balance, equity, and status."""
    return get_account_summary()


@app.get("/api/trades/positions")
@app.get("/trades/positions")
def get_mt5_positions(symbol: str = ""):
    """Get open MT5 positions."""
    return {"positions": get_open_positions(symbol)}


class ClosePositionRequest(BaseModel):
    ticket: Optional[int] = Field(default=None, description="Position ticket number")
    symbol: Optional[str] = Field(default="XAUUSD", description="Symbol to close positions for")
    side: Optional[str] = Field(default=None, description="Optional side to close: BUY, SELL, or ALL")


@app.post("/api/trades/close")
@app.post("/trades/close")
def close_trade_position(req: ClosePositionRequest):
    """Close an open position, by ticket, side, or all positions for symbol in MetaTrader 5."""
    result = close_position(ticket=req.ticket, symbol=req.symbol or "", side=req.side)
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error") or result.get("message", "Failed to close position"),
        )
    return result



@app.get("/analysis/gold/multi-timeframe")
def gold_multi_timeframe_overview():
    """
    Overview across all key timeframes: 1Day, 4Hour, 1Hour, 15Min, 5Min.
    Shows global macro trend, key S&R levels, and setups on both 5min and 15min.
    """
    try:
        df_1d = get_gold_candles(interval="1day", outputsize=50)
        df_4h = get_gold_candles(interval="4h", outputsize=50)
        df_1h = get_gold_candles(interval="1h", outputsize=50)
        df_15m = get_gold_candles(interval="15min", outputsize=50)
        df_5m = get_gold_candles(interval="5min", outputsize=50)

        # Macro Support & Resistance from Daily and 4H
        sr_macro = calculate_support_resistance(df_1h, df_1d)

        # Timeframe trends
        trend_1d = analyze_timeframe_trend(df_1d, "1day")
        trend_4h = analyze_timeframe_trend(df_4h, "4h")
        trend_1h = analyze_timeframe_trend(df_1h, "1h")

        # Setups for 5m and 15m
        setup_5m = generate_multi_timeframe_signal(df_5m, df_1h, entry_tf="5min", htf_tf="1h")
        setup_15m = generate_multi_timeframe_signal(df_15m, df_4h, entry_tf="15min", htf_tf="4h")

        # News analysis
        news = check_news_filter()

        return {
            "symbol": "XAU/USD",
            "current_price": float(df_5m.iloc[-1]["close"]),
            "timeframe_trends": {
                "1day": trend_1d.get("trend"),
                "4h": trend_4h.get("trend"),
                "1h": trend_1h.get("trend"),
            },
            "support_resistance": sr_macro,
            "entry_5min": {
                "signal": setup_5m.get("signal"),
                "has_setup": setup_5m.get("has_setup"),
                "tp": setup_5m.get("tp"),
                "sl": setup_5m.get("sl"),
                "setup": setup_5m.get("setup"),
            },
            "entry_15min": {
                "signal": setup_15m.get("signal"),
                "has_setup": setup_15m.get("has_setup"),
                "tp": setup_15m.get("tp"),
                "sl": setup_15m.get("sl"),
                "setup": setup_15m.get("setup"),
            },
            "news_analysis": news.get("news_analysis"),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Multi-timeframe overview failed: {exc}",
        )


@app.get("/news/calendar")
def news_calendar():
    """Retrieve verified economic news calendar and impact assessment."""
    news = check_news_filter()
    return news


@app.post("/news/refresh")
def news_refresh():
    """Force an immediate refresh of the economic news calendar from XOOMAR."""
    try:
        res = refresh_news_calendar()
        return res
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"News calendar refresh failed: {exc}",
        )


@app.get("/analysis/latest")
def latest_scheduled_analysis():
    """Retrieve the most recent scheduled analysis result."""
    data = get_latest_analysis()
    if data is None:
        raise HTTPException(status_code=404, detail="No scheduled analysis recorded yet")
    return data