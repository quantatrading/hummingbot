import math
from typing import Tuple


def compute_inventory_gate(
    q_base: float,
    mid: float,
    inv_cap_quote: float,
    scale_pct: float,
    mode: str,
    gate_min: float,
) -> float:
    inv_quote = abs(float(q_base)) * max(float(mid), 0.0)
    scale = max(float(scale_pct) * max(float(inv_cap_quote), 0.0), 1e-9)
    gate_floor = max(0.0, min(1.0, float(gate_min)))

    if str(mode).lower() == "linear":
        g = max(0.0, 1.0 - (inv_quote / scale))
    else:
        g = math.exp(-inv_quote / scale)

    return max(gate_floor, min(1.0, g))


def apply_cross_suppression(
    gate: float,
    q_base: float,
    prev_q_base: float,
    now_ts: float,
    suppress_until_ts: float,
    deadband_base: float,
    suppress_factor: float,
    hold_secs: int,
    enabled: bool,
) -> Tuple[float, float]:
    g = max(0.0, min(1.0, float(gate)))
    until_ts = float(suppress_until_ts)
    if enabled:
        q_now = float(q_base)
        q_prev = float(prev_q_base)
        if (
            q_now != 0.0
            and q_prev != 0.0
            and (q_now > 0) != (q_prev > 0)
            and abs(q_now) > float(deadband_base)
        ):
            until_ts = max(until_ts, float(now_ts) + max(int(hold_secs), 0))
        if float(now_ts) < until_ts:
            g = min(g, max(0.0, min(1.0, float(suppress_factor))))
    return g, until_ts
