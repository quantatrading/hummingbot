import math
from typing import Optional, Tuple


def compute_inventory_stress(inv_quote: float, cap_quote: float) -> float:
    cap = float(cap_quote)
    if cap <= 0:
        return 0.0
    return max(0.0, min(1.0, float(inv_quote) / cap))


def compute_inventory_risk_ema(
    inv_quote: float,
    prev_inv_quote_ema: Optional[float],
    dt_secs: Optional[float],
    halflife_secs: float,
) -> float:
    inv_value = max(0.0, float(inv_quote))
    if prev_inv_quote_ema is None or dt_secs is None:
        return inv_value

    dt = float(dt_secs)
    half_life = float(halflife_secs)
    if dt <= 0.0 or half_life <= 0.0:
        return inv_value

    alpha = 1.0 - math.exp(-math.log(2.0) * dt / half_life)
    prev_value = max(0.0, float(prev_inv_quote_ema))
    return alpha * inv_value + (1.0 - alpha) * prev_value


def compute_size_multiplier(stress: float, m_min: float, beta: float, power: float) -> float:
    bounded_stress = max(0.0, min(1.0, float(stress)))
    bounded_min = max(0.0, min(1.0, float(m_min)))
    bounded_beta = max(0.0, float(beta))
    bounded_power = max(0.0, float(power))
    return bounded_min + (1.0 - bounded_min) * math.exp(-bounded_beta * (bounded_stress ** bounded_power))


def compute_directional_size_multipliers(
    q_base: float,
    reference_price: float,
    cap_quote: float,
    bias_k: float,
    enabled: bool,
) -> Tuple[float, float]:
    if not enabled:
        return 1.0, 1.0

    cap = float(cap_quote)
    if cap <= 0.0:
        return 1.0, 1.0

    k = max(0.0, float(bias_k))
    q_norm = max(0.0, min(1.0, abs(float(q_base)) * max(0.0, float(reference_price)) / cap))
    direction = 0.0
    if q_base > 0:
        direction = 1.0
    elif q_base < 0:
        direction = -1.0

    bid_mult = 1.0 + k * max(0.0, min(1.0, -direction * q_norm))
    ask_mult = 1.0 + k * max(0.0, min(1.0, direction * q_norm))

    scale = 2.0 / max(1e-12, bid_mult + ask_mult)
    return bid_mult * scale, ask_mult * scale
