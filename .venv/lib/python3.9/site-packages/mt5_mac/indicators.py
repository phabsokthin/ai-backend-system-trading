from __future__ import annotations

from typing import Any


def _ha_calculate(candles: list[dict]) -> list[dict]:
    if not candles:
        return []
    result = []
    prev_ha_open = None
    prev_ha_close = None
    for c in candles:
        o = c["open"]
        h = c["high"]
        l = c["low"]
        cl = c["close"]
        ha_close = (o + h + l + cl) / 4.0
        if prev_ha_open is None:
            ha_open = (o + cl) / 2.0
        else:
            ha_open = (prev_ha_open + prev_ha_close) / 2.0
        ha_high = max(h, ha_open, ha_close)
        ha_low = min(l, ha_open, ha_close)
        result.append({
            "time": c["time"],
            "open": ha_open,
            "high": ha_high,
            "low": ha_low,
            "close": ha_close,
        })
        prev_ha_open = ha_open
        prev_ha_close = ha_close
    return result


_BUILTIN_COMPUTATIONS: dict[str, Any] = {
    "Heiken_Ashi.ex5": _ha_calculate,
}


def compute_indicator(
    indicator_key: str, candles: list[dict],
) -> list[dict] | None:
    fn = _BUILTIN_COMPUTATIONS.get(indicator_key)
    if fn is None:
        return None
    return fn(candles)


def get_buffer(
    computed: list[dict], buffer_num: int, count: int,
) -> list[float]:
    if not computed:
        return []
    buf_map = {0: "open", 1: "high", 2: "low", 3: "close"}
    key = buf_map.get(buffer_num)
    if key is None:
        return []
    vals = [c[key] for c in computed]
    return vals[-count:]
