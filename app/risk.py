import os
from typing import Optional, Dict, Any


def calculate_risk_levels(
    signal: Optional[str],
    price: Optional[float],
    atr: Optional[float],
    risk_reward: Optional[float] = None,
    atr_multiplier: Optional[float] = None,
    support_resistance: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Calculate Stop Loss (SL) and Take Profit (TP1, TP2, TP3) levels
    based on ATR, Risk:Reward ratio, and Support & Resistance levels.
    """
    if not signal or signal not in ("BUY", "SELL"):
        return None

    if price is None or atr is None or atr <= 0:
        return None

    if risk_reward is None:
        try:
            risk_reward = float(os.getenv("RISK_REWARD_RATIO", "2.0"))
        except ValueError:
            risk_reward = 2.0

    if atr_multiplier is None:
        try:
            atr_multiplier = float(os.getenv("ATR_STOP_MULTIPLIER", "1.5"))
        except ValueError:
            atr_multiplier = 1.5

    distance = atr * atr_multiplier

    nearest_sup = None
    nearest_res = None
    if support_resistance:
        nearest_sup = support_resistance.get("nearest_support")
        nearest_res = support_resistance.get("nearest_resistance")

    if signal == "BUY":
        # Stop loss below nearest support or ATR buffer
        if nearest_sup and nearest_sup < price:
            stop_loss = min(price - distance, nearest_sup - (atr * 0.2))
        else:
            stop_loss = price - distance

        actual_risk = max(price - stop_loss, 0.5)
        # Ensure 1.5R, 2.0R, 3.0R progression: TP1 < TP2 < TP3
        tp1 = price + (actual_risk * 1.5)
        tp2 = max(tp1 + (actual_risk * 0.5), price + (actual_risk * risk_reward))
        tp3_target = max(nearest_res, price + (actual_risk * 3.0)) if nearest_res else (price + (actual_risk * 3.0))
        tp3 = max(tp2 + (actual_risk * 0.5), tp3_target)
        take_profit = tp2

    elif signal == "SELL":
        # Stop loss above nearest resistance or ATR buffer
        if nearest_res and nearest_res > price:
            stop_loss = max(price + distance, nearest_res + (atr * 0.2))
        else:
            stop_loss = price + distance

        actual_risk = max(stop_loss - price, 0.5)
        # Ensure 1.5R, 2.0R, 3.0R progression: TP1 > TP2 > TP3
        tp1 = price - (actual_risk * 1.5)
        tp2 = min(tp1 - (actual_risk * 0.5), price - (actual_risk * risk_reward))
        tp3_target = min(nearest_sup, price - (actual_risk * 3.0)) if nearest_sup else (price - (actual_risk * 3.0))
        tp3 = min(tp2 - (actual_risk * 0.5), tp3_target)
        take_profit = tp2

    else:
        return None

    return {
        "signal": signal,
        "entry_price": round(price, 3),
        "stop_loss": round(stop_loss, 3),
        "take_profit": round(take_profit, 3),
        "sl": round(stop_loss, 3),
        "tp": round(take_profit, 3),
        "tp1": round(tp1, 3),
        "tp2": round(tp2, 3),
        "tp3": round(tp3, 3),
        "distance": round(distance, 3),
        "sl_distance": round(abs(price - stop_loss), 3),
        "tp_distance": round(abs(take_profit - price), 3),
        "risk_reward_ratio": risk_reward,
        "atr_multiplier": atr_multiplier,
        "method": "ATR and Support/Resistance hybrid levels",
    }
