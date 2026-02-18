import math
import unittest

from hummingbot.strategy.avellaneda_market_making.inventory_size import (
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
        m_min = 0.15
        beta = 3.0
        self.assertAlmostEqual(1.0, compute_size_multiplier(0.0, m_min, beta), places=8)
        self.assertAlmostEqual(m_min + (1.0 - m_min) * math.exp(-1.5), compute_size_multiplier(0.5, m_min, beta), places=8)
        self.assertAlmostEqual(m_min + (1.0 - m_min) * math.exp(-3.0), compute_size_multiplier(1.0, m_min, beta), places=8)

    def test_compute_size_multiplier_monotonic_and_bounds(self):
        m_min = 0.15
        beta = 3.0
        m0 = compute_size_multiplier(0.0, m_min, beta)
        m1 = compute_size_multiplier(0.25, m_min, beta)
        m2 = compute_size_multiplier(0.5, m_min, beta)
        m3 = compute_size_multiplier(1.0, m_min, beta)
        self.assertGreaterEqual(m0, m1)
        self.assertGreaterEqual(m1, m2)
        self.assertGreaterEqual(m2, m3)
        for mult in (m0, m1, m2, m3):
            self.assertGreaterEqual(mult, m_min)
            self.assertLessEqual(mult, 1.0)

