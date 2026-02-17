import math
from typing import Tuple


def compute_a_skew_price(
    r_base: float,
    gamma: float,
    A_b: float,
    A_a: float,
    delta_mode: str,
    max_bps: float,
    deadband_bps: float,
    eps: float,
    prev: float,
    alpha: float,
) -> Tuple[float, float, float]:
    """
    Compute bounded/smoothed A-asymmetry reservation-price skew in absolute price units.
    Returns (skew_price_final, skew_price_smoothed, ratio_bps).
    """
    r_abs = abs(float(r_base))
    gamma_safe = max(abs(float(gamma)), max(float(eps), 1e-12))
    eps_safe = max(float(eps), 1e-12)
    a_b_safe = max(float(A_b), 0.0)
    a_a_safe = max(float(A_a), 0.0)

    ratio_log = math.log((a_b_safe + eps_safe) / (a_a_safe + eps_safe))
    ratio_bps = 1e4 * ratio_log

    if abs(ratio_bps) < float(deadband_bps):
        skew_raw = 0.0
    else:
        skew_raw = (1.0 / (2.0 * gamma_safe)) * ratio_log

    if str(delta_mode).lower() == "relative_to_r":
        skew_price = float(r_base) * skew_raw
    else:
        skew_price = skew_raw

    alpha_clamped = min(max(float(alpha), 0.0), 1.0)
    skew_smoothed = alpha_clamped * skew_price + (1.0 - alpha_clamped) * float(prev)

    cap_price = r_abs * max(float(max_bps), 0.0) / 1e4
    if cap_price <= 0:
        skew_final = 0.0
    else:
        skew_final = max(-cap_price, min(cap_price, skew_smoothed))

    return skew_final, skew_smoothed, ratio_bps
