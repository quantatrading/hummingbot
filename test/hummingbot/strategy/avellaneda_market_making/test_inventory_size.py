import math
import unittest

from hummingbot.strategy.avellaneda_market_making.inventory_size import (
    compute_directional_size_multipliers,
    compute_inventory_risk_ema,
    compute_inventory_stress,
    compute_size_multiplier,
)


class InventorySizeTests(unittest.TestCase):
    def test_compute_inventory_stress_values(self):
        self.assertAlmostEqual(0.0, compute_inventory_stress(0.0, 200.0), places=8)
        self.assertAlmostEqual(0.5, compute_inventory_stress(100.0, 200.0), places=8)
        self.assertAlmostEqual(1.0, compute_inventory_stress(200.0, 200.0), places=8)
        self.assertAlmostEqual(1.0, compute_inventory_stress(400.0, 200.0), places=8)

    def test_compute_inventory_stress_zero_or_negative_cap(self):
        self.assertEqual(0.0, compute_inventory_stress(100.0, 0.0))
        self.assertEqual(0.0, compute_inventory_stress(100.0, -1.0))

    def test_compute_size_multiplier_default_points(self):
        m_min = 0.20
        beta = 3.0
        power = 2.0
        self.assertAlmostEqual(1.0, compute_size_multiplier(0.0, m_min, beta, power), places=8)
        self.assertAlmostEqual(
            m_min + (1.0 - m_min) * math.exp(-beta * (0.5 ** power)),
            compute_size_multiplier(0.5, m_min, beta, power),
            places=8,
        )
        self.assertAlmostEqual(
            m_min + (1.0 - m_min) * math.exp(-beta * (1.0 ** power)),
            compute_size_multiplier(1.0, m_min, beta, power),
            places=8,
        )

    def test_compute_size_multiplier_monotonic_and_bounds(self):
        m_min = 0.20
        beta = 3.0
        power = 2.0
        m0 = compute_size_multiplier(0.0, m_min, beta, power)
        m1 = compute_size_multiplier(0.25, m_min, beta, power)
        m2 = compute_size_multiplier(0.5, m_min, beta, power)
        m3 = compute_size_multiplier(1.0, m_min, beta, power)
        self.assertGreaterEqual(m0, m1)
        self.assertGreaterEqual(m1, m2)
        self.assertGreaterEqual(m2, m3)
        for mult in (m0, m1, m2, m3):
            self.assertGreaterEqual(mult, m_min)
            self.assertLessEqual(mult, 1.0)

    def test_compute_inventory_risk_ema_with_known_dt(self):
        # dt == half-life => alpha = 0.5
        ema = compute_inventory_risk_ema(
            inv_quote=100.0,
            prev_inv_quote_ema=50.0,
            dt_secs=60.0,
            halflife_secs=60.0,
        )
        self.assertAlmostEqual(75.0, ema, places=8)

    def test_compute_inventory_risk_ema_initialization(self):
        ema = compute_inventory_risk_ema(
            inv_quote=42.0,
            prev_inv_quote_ema=None,
            dt_secs=None,
            halflife_secs=60.0,
        )
        self.assertAlmostEqual(42.0, ema, places=8)

    def test_directional_size_multipliers_long_inventory(self):
        bid_mult, ask_mult = compute_directional_size_multipliers(
            q_base=0.01,
            reference_price=10000.0,
            cap_quote=200.0,
            bias_k=0.5,
            enabled=True,
        )
        self.assertLess(bid_mult, ask_mult)
        self.assertAlmostEqual(2.0, bid_mult + ask_mult, places=8)

    def test_directional_size_multipliers_short_inventory(self):
        bid_mult, ask_mult = compute_directional_size_multipliers(
            q_base=-0.01,
            reference_price=10000.0,
            cap_quote=200.0,
            bias_k=0.5,
            enabled=True,
        )
        self.assertGreater(bid_mult, ask_mult)
        self.assertAlmostEqual(2.0, bid_mult + ask_mult, places=8)

    def test_directional_size_multipliers_disabled(self):
        bid_mult, ask_mult = compute_directional_size_multipliers(
            q_base=0.01,
            reference_price=10000.0,
            cap_quote=200.0,
            bias_k=0.5,
            enabled=False,
        )
        self.assertAlmostEqual(1.0, bid_mult, places=8)
        self.assertAlmostEqual(1.0, ask_mult, places=8)
