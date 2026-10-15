from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional
import pandas as pd

logger = logging.getLogger("mt5-bridge")

# Try importing mt5_mac
try:
    import mt5_mac as mt5
except ImportError:
    mt5 = None

_lock = threading.Lock()
_connected = False
_terminal_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"

TIMEFRAME_MT5_MAP = {
    "1min": 1,
    "1m": 1,
    "5min": 5,
    "5m": 5,
    "15min": 15,
    "15m": 15,
    "30min": 30,
    "30m": 30,
    "1h": 16385,
    "1H": 16385,
    "4h": 16388,
    "4H": 16388,
    "1day": 16408,
    "1d": 16408,
    "1D": 16408,
}


_cached_terminal_path: Optional[str] = None
_cached_symbols: Dict[str, str] = {}


def _get_wine_terminal_path() -> str:
    """Find the terminal64.exe path in Wine prefix (cached for instant performance)."""
    global _cached_terminal_path
    if _cached_terminal_path is not None:
        return _cached_terminal_path

    wineprefix = os.path.expanduser(
        "~/Library/Application Support/net.metaquotes.wine.metatrader5"
    )
    standard_p = os.path.join(wineprefix, "drive_c", "Program Files", "MetaTrader 5", "terminal64.exe")
    if os.path.exists(standard_p):
        _cached_terminal_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
        return _cached_terminal_path

    _cached_terminal_path = _terminal_path
    return _cached_terminal_path


def ensure_connected(force_reconnect: bool = False) -> bool:
    """Ensure MT5 connection is active and synchronized (instant check)."""
    global _connected
    if mt5 is None:
        logger.error("mt5_mac is not installed")
        return False

    with _lock:
        if _connected and not force_reconnect:
            return True

        if force_reconnect:
            try:
                mt5.shutdown()
            except Exception:
                pass
            _connected = False

        term_path = _get_wine_terminal_path()
        ok = mt5.initialize(path=term_path)
        if not ok:
            ok = mt5.initialize()
        _connected = bool(ok)
        if _connected:
            logger.info("MT5 initialized successfully")
        else:
            logger.error("Failed to initialize MT5")
        return _connected


def get_account_summary() -> Dict[str, Any]:
    """Return connected MT5 account details."""
    if not ensure_connected():
        return {
            "connected": False,
            "error": "MT5 terminal not connected",
        }

    info = mt5.account_info()
    if info is None:
        return {
            "connected": False,
            "error": "Account info unavailable",
        }

    return {
        "connected": True,
        "login": info.login,
        "server": info.server,
        "company": info.company,
        "currency": info.currency,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "margin_level": info.margin_level,
        "profit": info.profit,
        "trade_mode": info.trade_mode,
        "trade_allowed": info.trade_allowed,
    }


def resolve_symbol(symbol: str) -> str:
    """Find matching symbol name in MT5 with cache for instant resolution."""
    clean_sym = symbol.replace("/", "").strip().upper()
    if clean_sym in _cached_symbols:
        return _cached_symbols[clean_sym]

    candidates = [clean_sym, f"{clean_sym}m", f"{clean_sym}.a", f"{clean_sym}_i"]
    for candidate in candidates:
        info = mt5.symbol_info(candidate)
        if info is not None:
            if not info.visible:
                mt5.symbol_select(candidate, True)
            _cached_symbols[clean_sym] = candidate
            return candidate

    mt5.symbol_select(clean_sym, True)
    _cached_symbols[clean_sym] = clean_sym
    return clean_sym


def get_mt5_live_tick(symbol: str = "XAUUSD") -> Optional[Dict[str, Any]]:
    """Return the latest bid/ask/price tick directly from connected MT5 terminal."""
    if not ensure_connected():
        return None
    target_symbol = resolve_symbol(symbol)
    tick = mt5.symbol_info_tick(target_symbol)
    if not tick:
        return None
    bid = round(float(tick.bid), 3)
    ask = round(float(tick.ask), 3)
    price = round((bid + ask) / 2.0, 3)
    return {
        "symbol": target_symbol,
        "bid": bid,
        "ask": ask,
        "price": price,
        "spread": round(ask - bid, 3),
        "timestamp": float(getattr(tick, "time", time.time())),
    }


def get_mt5_candles(symbol: str = "XAUUSD", interval: str = "5min", limit: int = 100) -> Optional[pd.DataFrame]:
    """Fetch real-time historical candlesticks directly from MT5 terminal."""
    if not ensure_connected():
        return None
    target_symbol = resolve_symbol(symbol)
    tf = TIMEFRAME_MT5_MAP.get(interval.lower(), 5)
    rates = mt5.copy_rates_from_pos(target_symbol, tf, 0, max(limit + 50, 100))
    if rates is None or len(rates) == 0:
        return None

    df = pd.DataFrame(rates)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    return df


def send_order(
    action: str,
    volume: float = 0.01,
    symbol: str = "XAUUSD",
    expected_price: Optional[float] = None,
    max_slippage: float = 2.0,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    comment: str = "Web Order",
) -> Dict[str, Any]:
    """Execute a market BUY or SELL order in MetaTrader 5 with chart price verification."""
    if not ensure_connected():
        return {
            "success": False,
            "error": "MetaTrader 5 terminal is not connected or not running.",
        }

    action_upper = action.strip().upper()
    if action_upper not in ("BUY", "SELL"):
        return {
            "success": False,
            "error": f"Invalid action: {action}. Must be 'BUY' or 'SELL'.",
        }

    target_symbol = resolve_symbol(symbol)
    tick = mt5.symbol_info_tick(target_symbol)
    if tick is None:
        return {
            "success": False,
            "error": f"Failed to get price tick for {target_symbol}.",
        }

    price = tick.ask if action_upper == "BUY" else tick.bid
    # Prioritize the available price clicked by user, or latest live MT5 price
    order_price = float(expected_price) if (expected_price is not None and expected_price > 0) else float(price)
    deviation_points = max(int(max_slippage * 100), 50)

    order_type = 0 if action_upper == "BUY" else 1  # 0: ORDER_TYPE_BUY, 1: ORDER_TYPE_SELL

    # Try different filling types (IOC -> FOK -> RETURN) as brokers differ
    filling_types = [1, 0, 2]  # IOC=1, FOK=0, RETURN=2
    last_result = None

    for filling in filling_types:
        request = {
            "action": 1,  # TRADE_ACTION_DEAL
            "symbol": target_symbol,
            "volume": float(volume),
            "type": order_type,
            "price": order_price,
            "sl": float(sl) if sl else 0.0,
            "tp": float(tp) if tp else 0.0,
            "deviation": deviation_points,
            "magic": 108888,
            "comment": comment,
            "type_time": 0,  # ORDER_TIME_GTC
            "type_filling": filling,
        }

        trade_result = mt5.order_send(request)
        # If broker insists on current live tick price (retcode 10015), retry with tick price
        if trade_result is not None and trade_result.retcode == 10015 and order_price != price:
            request["price"] = float(price)
            trade_result = mt5.order_send(request)

        if trade_result is not None:
            last_result = trade_result
            # Check success (10009 = TRADE_RETCODE_DONE, 10008 = TRADE_RETCODE_PLACED)
            if trade_result.retcode in (10009, 10008) or getattr(trade_result, "deal", 0) > 0 or getattr(trade_result, "order", 0) > 0:
                acc = get_account_summary()
                return {
                    "success": True,
                    "action": action_upper,
                    "symbol": target_symbol,
                    "volume": float(volume),
                    "price": trade_result.price or order_price,
                    "deal": getattr(trade_result, "deal", 0),
                    "order": getattr(trade_result, "order", 0),
                    "retcode": trade_result.retcode,
                    "comment": trade_result.comment or "Order placed successfully",
                    "account": acc,
                }
            # If rejected because of unsupported filling mode, try next
            if trade_result.retcode == 10030:  # Unsupported filling mode
                continue
            break

    # If all failed initially, check if AutoTrading was just enabled and retry with fresh session
    if last_result and getattr(last_result, "retcode", -1) == 10027:
        logger.info("AutoTrading 10027 detected. Reconnecting session to sync with MT5 terminal...")
        ensure_connected(force_reconnect=True)
        for filling in filling_types:
            request["type_filling"] = filling
            retry_res = mt5.order_send(request)
            if retry_res is not None:
                last_result = retry_res
                if retry_res.retcode in (10009, 10008) or getattr(retry_res, "deal", 0) > 0 or getattr(retry_res, "order", 0) > 0:
                    acc = get_account_summary()
                    return {
                        "success": True,
                        "action": action_upper,
                        "symbol": target_symbol,
                        "volume": float(volume),
                        "price": retry_res.price or price,
                        "deal": getattr(retry_res, "deal", 0),
                        "order": getattr(retry_res, "order", 0),
                        "retcode": retry_res.retcode,
                        "comment": retry_res.comment or "Order placed successfully",
                        "account": acc,
                    }
                if retry_res.retcode == 10030:
                    continue
                break

    retcode = getattr(last_result, "retcode", -1) if last_result else -1
    comment_err = getattr(last_result, "comment", "") if last_result else "No response from MT5"

    if retcode == 10027:
        err_msg = (
            "AutoTrading is disabled in MetaTrader 5. "
            "Please click the 'Algo Trading' button on the MT5 top toolbar (or in Tools -> Options -> Expert Advisors -> check 'Allow Algo Trading')."
        )
    elif retcode == 10018:
        err_msg = "Market is closed for this symbol right now."
    elif retcode == 10019:
        err_msg = "Not enough money / margin on your account to execute this lot size."
    elif retcode == 10014:
        err_msg = f"Invalid lot volume ({volume}). Check minimum lot size allowed by your broker."
    elif retcode == 10015:
        err_msg = f"Invalid price ({price}) for this order."
    elif retcode == 10016:
        err_msg = "Invalid Stop Loss or Take Profit values."
    else:
        err_msg = f"Order rejected by MT5 (retcode {retcode}): {comment_err}"

    return {
        "success": False,
        "error": err_msg,
        "retcode": retcode,
        "symbol": target_symbol,
        "price": price,
    }


def get_open_positions(symbol: str = "") -> list[Dict[str, Any]]:
    """Return currently open positions from MT5."""
    if not ensure_connected():
        return []

    target_symbol = resolve_symbol(symbol) if symbol else ""
    positions = mt5.positions_get(symbol=target_symbol) if target_symbol else mt5.positions_get()
    if not positions:
        return []

    return [
        {
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "BUY" if p.type == 0 else "SELL",
            "volume": p.volume,
            "price_open": p.price_open,
            "price_current": p.price_current,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "time": p.time,
            "comment": p.comment,
        }
        for p in positions
    ]


def close_position(
    ticket: Optional[int] = None,
    symbol: str = "",
    side: Optional[str] = None,
) -> Dict[str, Any]:
    """Close an open position by ticket, side (BUY/SELL), or close all open positions for symbol."""
    if not ensure_connected():
        return {
            "success": False,
            "error": "MT5 terminal not connected",
        }

    target_symbol = resolve_symbol(symbol) if symbol else ""
    if ticket:
        positions = mt5.positions_get(ticket=int(ticket))
    elif target_symbol:
        positions = mt5.positions_get(symbol=target_symbol)
    else:
        positions = mt5.positions_get()

    if not positions:
        return {
            "success": False,
            "message": "No open positions found to close.",
            "closed_count": 0,
        }

    if side:
        side_upper = side.upper()
        if side_upper == "BUY":
            positions = [p for p in positions if p.type == 0]
        elif side_upper == "SELL":
            positions = [p for p in positions if p.type == 1]

    if not positions:
        return {
            "success": False,
            "message": f"No open {side.upper()} positions found to close." if side else "No open positions found to close.",
            "closed_count": 0,
        }

    closed_results = []
    filling_types = [1, 0, 2]
    last_error = ""

    for pos in positions:
        pos_ticket = pos.ticket
        pos_type = pos.type  # 0: BUY, 1: SELL
        pos_symbol = pos.symbol
        pos_volume = pos.volume
        close_type = 1 if pos_type == 0 else 0  # 1: SELL closes BUY, 0: BUY closes SELL

        tick = mt5.symbol_info_tick(pos_symbol)
        if not tick:
            continue
        price = tick.bid if close_type == 1 else tick.ask

        for filling in filling_types:
            request = {
                "action": 1,  # TRADE_ACTION_DEAL
                "position": pos_ticket,
                "symbol": pos_symbol,
                "volume": pos_volume,
                "type": close_type,
                "price": price,
                "deviation": 30,
                "magic": 108888,
                "comment": "Close Web",
                "type_time": 0,
                "type_filling": filling,
            }

            res = mt5.order_send(request)
            if res and (res.retcode in (10009, 10008) or getattr(res, "deal", 0) > 0 or getattr(res, "order", 0) > 0):
                closed_results.append({
                    "ticket": pos_ticket,
                    "symbol": pos_symbol,
                    "volume": pos_volume,
                    "price": res.price or price,
                    "deal": getattr(res, "deal", 0),
                    "retcode": res.retcode,
                    "success": True,
                })
                break
            if res and res.retcode == 10030:
                continue
            if res:
                last_error = getattr(res, "comment", "") or f"Retcode {res.retcode}"
            break

    if closed_results:
        return {
            "success": True,
            "message": f"Successfully closed {len(closed_results)} position(s).",
            "closed_count": len(closed_results),
            "details": closed_results,
            "account": get_account_summary(),
        }

    return {
        "success": False,
        "error": f"Failed to close position(s): {last_error}" if last_error else "Failed to close position(s) in MT5.",
        "closed_count": 0,
    }

