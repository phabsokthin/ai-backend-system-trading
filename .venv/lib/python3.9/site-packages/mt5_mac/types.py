from __future__ import annotations

from typing import NamedTuple

INVALID_HANDLE = -1


class MqlIndicatorBuffer(NamedTuple):
    time: int
    value: float


class MqlTick(NamedTuple):
    time: int
    bid: float
    ask: float
    last: float
    volume: int
    time_msc: int
    flags: int
    volume_real: float


class MqlRates(NamedTuple):
    time: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int


class MqlAccountInfo(NamedTuple):
    login: int
    trade_mode: int
    leverage: int
    limit_orders: int
    margin_so_mode: int
    trade_allowed: bool
    trade_expert: bool
    margin_mode: int
    currency_digits: int
    fifo_close: bool
    balance: float
    credit: float
    profit: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    margin_so_call: float
    margin_so_so: float
    margin_initial: float
    name: str
    server: str
    currency: str
    company: str


class MqlSymbolInfo(NamedTuple):
    name: str
    time: int
    digits: int
    spread: int
    spread_float: bool
    ticks_bookdepth: int
    trade_calc_mode: int
    trade_mode: int
    trade_exemode: int
    trade_execution: int
    swap_mode: int
    swap_rollover3days: int
    swap_long: float
    swap_short: float
    swap_open_price: int
    swap_open_time: int
    swap_type: int
    start_time: int
    expiration_time: int
    trade_stops_level: int
    trade_freeze_level: int
    trade_exe_flag: int
    swap_variation: float
    margin_hedged: float
    price_change: float
    price_volatility: float
    price_feed_correct: int
    price_feed_open: int
    price_feed_high: int
    price_feed_low: int
    price_feed_last: int
    price_feed_close: int
    price_feed_ask: int
    price_feed_bid: int
    price_feed_volume: int
    bid: float
    ask: float
    volume: int
    volume_real: float
    last: float
    last_volume: int
    last_volume_real: float
    high: float
    low: float
    point: float
    tick_value: float
    tick_size: float
    contract_size: float
    stops_level: int
    freeze_level: int
    currency_base: str
    currency_profit: str
    currency_margin: str
    bank: str
    description: str
    path: str
    margin_maintenance: float
    margin_initial: float
    margin_hedged: float
    margin_divider: int
    margin_rate: float
    background_color: int
    chart_mode: int
    visible: bool
    select: bool
    liquid: bool
    volume_min: float
    volume_max: float
    volume_step: float
    volume_limit: float
    allow_trade: bool
    allow_margin_out: bool
    allow_forward: bool
    allow_option: bool
    allow_spread: bool
    allow_swap: bool
    allow_swap_long: bool
    allow_swap_short: bool
    allow_swap_open_price: bool
    allow_swap_open_time: bool
    allow_swap_type: bool
    allow_swap_variation: bool
    allow_swap_rollover3days: bool
    allow_margin_hedged: bool
    allow_margin_rate: bool
    allow_margin_divider: bool
    allow_margin_maintenance: bool
    allow_margin_initial: bool
    allow_trade_stops_level: bool
    allow_trade_freeze_level: bool
    allow_trade_exe_flag: bool
    allow_ticks_bookdepth: bool
    allow_spread_float: bool
    allow_trade_exemode: bool
    allow_trade_execution: bool
    allow_trade_calc_mode: bool
    allow_trade_mode: bool
    allow_expiration_time: bool
    allow_start_time: bool
    allow_price_feed_correct: bool
    allow_price_feed_open: bool
    allow_price_feed_high: bool
    allow_price_feed_low: bool
    allow_price_feed_last: bool
    allow_price_feed_close: bool
    allow_price_feed_ask: bool
    allow_price_feed_bid: bool
    allow_price_feed_volume: bool


class MqlTradeRequest(NamedTuple):
    action: int = 0
    magic: int = 0
    order: int = 0
    symbol: str = ""
    volume: float = 0.0
    price: float = 0.0
    stoplimit: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    deviation: int = 0
    type: int = 0
    type_filling: int = 0
    type_time: int = 0
    expiration: int = 0
    comment: str = ""
    position: int = 0
    position_by: int = 0


class MqlTradeResult(NamedTuple):
    retcode: int = -1
    deal: int = 0
    order: int = 0
    volume: float = 0.0
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    comment: str = ""
    request_id: int = 0
    retcode_external: int = 0

    @property
    def success(self) -> bool:
        return self.retcode == 10009


class MqlPosition(NamedTuple):
    ticket: int
    time: int
    time_msc: int
    time_update: int
    time_update_msc: int
    type: int
    magic: int
    identifier: int
    reason: int
    symbol: str
    comment: str
    external_id: str
    volume: float
    price_open: float
    sl: float
    tp: float
    price_current: float
    swap: float
    profit: float
    symbol_name: str


class MqlOrder(NamedTuple):
    ticket: int
    time_setup: int
    time_setup_msc: int
    time_done: int
    time_done_msc: int
    time_expiration: int
    type: int
    type_filling: int
    type_time: int
    state: int
    magic: int
    position: int
    position_by: int
    reason: int
    volume_current: float
    volume_initial: float
    price_open: float
    sl: float
    tp: float
    price_current: float
    price_stoplimit: float
    symbol: str
    comment: str
    external_id: str


class MqlDeal(NamedTuple):
    ticket: int
    order: int
    time: int
    time_msc: int
    type: int
    entry: int
    magic: int
    position: int
    reason: int
    symbol: str
    comment: str
    external_id: str
    volume: float
    price: float
    commission: float
    swap: float
    profit: float
    fee: float
    symbol_name: str
