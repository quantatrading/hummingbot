import math


def compute_inventory_stress(inv_quote: float, cap_quote: float) -> float:
    cap = float(cap_quote)
    if cap <= 0:
        return 0.0
    return max(0.0, min(1.0, float(inv_quote) / cap))


def compute_size_multiplier(stress: float, m_min: float, beta: float) -> float:
    bounded_stress = max(0.0, min(1.0, float(stress)))
    bounded_min = max(0.0, min(1.0, float(m_min)))
    bounded_beta = max(0.0, float(beta))
    return bounded_min + (1.0 - bounded_min) * math.exp(-bounded_beta * bounded_stress)
