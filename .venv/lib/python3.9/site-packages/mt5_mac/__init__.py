from __future__ import annotations

__version__ = "0.3.0"
__author__ = "Abdul Rafay Jiwani"
__email__ = "abdulrafayjiwani@proton.me"
__license__ = "MIT"

from mt5_mac.backend import MT5Backend
from mt5_mac.types import (
    MqlTick, MqlRates, MqlAccountInfo, MqlSymbolInfo,
    MqlTradeRequest, MqlTradeResult, MqlPosition, MqlOrder, MqlDeal,
    MqlIndicatorBuffer, INVALID_HANDLE,
)

_backend: MT5Backend | None = None

# Timeframes
TIMEFRAME_M1 = 1
TIMEFRAME_M2 = 2
TIMEFRAME_M3 = 3
TIMEFRAME_M4 = 4
TIMEFRAME_M5 = 5
TIMEFRAME_M6 = 6
TIMEFRAME_M10 = 10
TIMEFRAME_M12 = 12
TIMEFRAME_M15 = 15
TIMEFRAME_M20 = 20
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 60
TIMEFRAME_H2 = 120
TIMEFRAME_H3 = 180
TIMEFRAME_H4 = 240
TIMEFRAME_H6 = 360
TIMEFRAME_H8 = 480
TIMEFRAME_H12 = 720
TIMEFRAME_D1 = 1440
TIMEFRAME_W1 = 10080
TIMEFRAME_MN1 = 43200

# Trade actions
TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 2
TRADE_ACTION_MODIFY = 7
TRADE_ACTION_REMOVE = 6
TRADE_ACTION_CLOSE_BY = 3

# Order types
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5
ORDER_TYPE_BUY_STOP_LIMIT = 6
ORDER_TYPE_SELL_STOP_LIMIT = 7
ORDER_TYPE_CLOSE_BY = 8

# Order filling
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

# Order time
ORDER_TIME_GTC = 0
ORDER_TIME_DAY = 1
ORDER_TIME_SPECIFIED = 2
ORDER_TIME_SPECIFIED_DAY = 3

# Position types
POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1

# Deal types
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1
DEAL_TYPE_BALANCE = 2
DEAL_TYPE_CREDIT = 3
DEAL_TYPE_CHARGE = 4
DEAL_TYPE_CORRECTION = 5
DEAL_TYPE_BONUS = 6
DEAL_TYPE_COMMISSION = 7
DEAL_TYPE_COMMISSION_DAILY = 8
DEAL_TYPE_COMMISSION_MONTHLY = 9
DEAL_TYPE_COMMISSION_AGENT_DAILY = 10
DEAL_TYPE_COMMISSION_AGENT_MONTHLY = 11
DEAL_TYPE_INTEREST = 12
DEAL_TYPE_BUY_CANCELED = 13
DEAL_TYPE_SELL_CANCELED = 14
DEAL_DIVIDEND = 15
DEAL_DIVIDEND_FRANKFURT = 16
DEAL_TAX = 17

# Deal entries
DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2
DEAL_ENTRY_OUT_BY = 3

# Trade modes
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2

# Margin modes
ACCOUNT_MARGIN_MODE_RETAIL_NETTING = 0
ACCOUNT_MARGIN_MODE_EXCHANGE = 1
ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2

# Order reasons
ORDER_REASON_CLIENT = 0
ORDER_REASON_MOBILE = 1
ORDER_REASON_WEB = 2
ORDER_REASON_EXPERT = 3
ORDER_REASON_SL = 4
ORDER_REASON_TP = 5
ORDER_REASON_SO = 6

# Reason for positions/deals
POSITION_REASON_CLIENT = 0
POSITION_REASON_MOBILE = 1
POSITION_REASON_WEB = 2
POSITION_REASON_EXPERT = 3

# Symbol trade modes
SYMBOL_TRADE_MODE_DISABLED = 0
SYMBOL_TRADE_MODE_LONG = 1
SYMBOL_TRADE_MODE_SHORT = 2
SYMBOL_TRADE_MODE_CLOSE = 4

# Symbol trade execution
SYMBOL_TRADE_EXECUTION_REQUEST = 0
SYMBOL_TRADE_EXECUTION_INSTANT = 1
SYMBOL_TRADE_EXECUTION_MARKET = 2
SYMBOL_TRADE_EXECUTION_EXCHANGE = 3

# Trade calc modes
SYMBOL_TRADE_CALC_MODE_FOREX = 0
SYMBOL_TRADE_CALC_MODE_CFD = 1
SYMBOL_TRADE_CALC_MODE_FUTURES = 2
SYMBOL_TRADE_CALC_MODE_CFDINDEX = 3
SYMBOL_TRADE_CALC_MODE_CFDLEVERAGE = 4

# Error codes
RES_E_SUCCESS = 10009
RES_E_FAIL = -1
RES_E_INVALID_PARAMS = -2
RES_E_NO_MONEY = -3
RES_E_NOT_FOUND = -4
RES_E_MARKET_CLOSED = -5
RES_E_TRADE_DISABLED = -6
RES_E_PRICE_LIMIT = -7
RES_E_TOO_MANY_REQUESTS = -8
RES_E_SERVER_BUSY = -9
RES_E_NO_CONNECTION = -10


# Indicator buffer indices for Heiken Ashi (built-in MT5)
HEIKEN_ASHI_BUFFER_OPEN  = 0
HEIKEN_ASHI_BUFFER_HIGH  = 1
HEIKEN_ASHI_BUFFER_LOW   = 2
HEIKEN_ASHI_BUFFER_CLOSE = 3

# Common built-in indicator paths
INDICATOR_HEIKEN_ASHI = "Indicators\\Examples\\Heiken_Ashi.ex5"


def initialize(
    path: str | None = None,
    login: int | str | None = None,
    password: str | None = None,
    server: str = "",
    timeout: int | None = None,
    portable: bool | None = None,
    quiet: bool = False,
    **kwargs,
) -> bool:
    global _backend
    if _backend is not None:
        _backend.stop()
    _backend = MT5Backend(terminal_path=path or "", quiet=quiet)
    ok = _backend.start(path=path)
    if ok and login is not None and password is not None:
        ok = _backend.login(str(login), password, server)
    return ok


def shutdown() -> None:
    global _backend
    if _backend is not None:
        _backend.stop()
        _backend = None


def login(login: int | str, password: str, server: str = "", timeout: int = 30000) -> bool:
    _require_backend()
    return _backend.login(str(login), password, server)


def account_info() -> MqlAccountInfo | None:
    _require_backend()
    return _backend.account_info()


def symbols_get(symbol: str | None = None) -> tuple[MqlSymbolInfo, ...] | None:
    _require_backend()
    return _backend.symbols_get(symbol)


def symbol_info(symbol: str) -> MqlSymbolInfo | None:
    _require_backend()
    return _backend.symbol_info(symbol)


def symbol_info_tick(symbol: str) -> MqlTick | None:
    _require_backend()
    return _backend.symbol_info_tick(symbol)


def symbol_select(symbol: str, enable: bool = True) -> bool:
    _require_backend()
    return _backend.symbol_select(symbol, enable)


def copy_rates_from_pos(
    symbol: str,
    timeframe: int,
    start_pos: int,
    count: int,
) -> tuple[MqlRates, ...] | None:
    _require_backend()
    return _backend.copy_rates_from_pos(symbol, timeframe, start_pos, count)


def copy_rates_from(
    symbol: str,
    timeframe: int,
    date_from: int,
    count: int,
) -> tuple[MqlRates, ...] | None:
    _require_backend()
    return _backend.copy_rates_from(symbol, timeframe, date_from, count)


def copy_rates_range(
    symbol: str,
    timeframe: int,
    date_from: int,
    date_to: int,
) -> tuple[MqlRates, ...] | None:
    _require_backend()
    return _backend.copy_rates_range(symbol, timeframe, date_from, date_to)


def order_send(request: dict) -> MqlTradeResult:
    _require_backend()
    return _backend.order_send(request)


def positions_get(symbol: str = "") -> tuple[MqlPosition, ...] | None:
    _require_backend()
    return _backend.positions_get(symbol)


def orders_get(symbol: str = "") -> tuple[MqlOrder, ...] | None:
    _require_backend()
    return _backend.orders_get(symbol)


def history_deals_get(date_from: int = 0, date_to: int = 0) -> tuple[MqlDeal, ...] | None:
    _require_backend()
    return _backend.history_deals_get(date_from, date_to)


def history_orders_get(date_from: int = 0, date_to: int = 0) -> tuple[MqlOrder, ...] | None:
    _require_backend()
    return _backend.history_orders_get(date_from, date_to)


def last_error() -> int:
    return 0


def indicator_init(
    symbol: str,
    timeframe: int,
    indicator_path: str,
    buffer_count: int = 4,
    params: list | None = None,
    ea_timeframe: int | None = None,
) -> int:
    _require_backend()
    return _backend.indicator_init(
        symbol, timeframe, indicator_path,
        buffer_count=buffer_count,
        params=params or [],
        ea_timeframe=ea_timeframe or timeframe,
    )


def indicator_read(
    handle: int,
    buffer_num: int,
    count: int,
) -> tuple[MqlIndicatorBuffer, ...] | None:
    _require_backend()
    return _backend.indicator_read(handle, buffer_num, count)


def indicator_deinit(handle: int) -> bool:
    _require_backend()
    return _backend.indicator_deinit(handle)


def indicator_list() -> dict[int, dict]:
    _require_backend()
    return _backend.indicator_list()


def _require_backend() -> None:
    if _backend is None:
        raise RuntimeError(
            "MT5 not initialized. Call mt5_mac.initialize() first."
        )
