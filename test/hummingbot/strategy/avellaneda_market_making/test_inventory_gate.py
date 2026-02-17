import math
import unittest

from hummingbot.strategy.avellaneda_market_making.inventory_gate import apply_cross_suppression, compute_inventory_gate


class InventoryGateTests(unittest.TestCase):
    def test_zero_inventory_gate_is_one(self):
        g = compute_inventory_gate(
            q_base=0.0,
            mid=70000.0,
            inv_cap_quote=200.0,
            scale_pct=0.35,
            mode="exp",
            gate_min=0.05,
        )
        self.assertAlmostEqual(1.0, g, places=8)

    def test_scale_point_behaviour(self):
        mid = 100.0
        inv_cap_quote = 200.0
        scale_pct = 0.5
        scale = inv_cap_quote * scale_pct
        q_base = scale / mid

        g_exp = compute_inventory_gate(q_base, mid, inv_cap_quote, scale_pct, "exp", 0.0)
        self.assertAlmostEqual(math.exp(-1.0), g_exp, places=6)

        g_lin = compute_inventory_gate(q_base, mid, inv_cap_quote, scale_pct, "linear", 0.0)
        self.assertAlmostEqual(0.0, g_lin, places=8)

    def test_large_inventory_approaches_gate_min(self):
        g = compute_inventory_gate(
            q_base=1000.0,
            mid=70000.0,
            inv_cap_quote=200.0,
            scale_pct=0.35,
            mode="exp",
            gate_min=0.05,
        )
        self.assertGreaterEqual(g, 0.05)
        self.assertLessEqual(g, 1.0)

    def test_gate_bounds(self):
        g = compute_inventory_gate(
            q_base=1.0,
            mid=100.0,
            inv_cap_quote=200.0,
            scale_pct=0.35,
            mode="linear",
            gate_min=0.05,
        )
        self.assertGreaterEqual(g, 0.05)
        self.assertLessEqual(g, 1.0)

    def test_cross_suppression_hold_window(self):
        gate = 0.8
        suppress_until = 0.0

        # before sign flip, unchanged
        gate, suppress_until = apply_cross_suppression(
            gate=gate,
            q_base=-0.001,
            prev_q_base=-0.002,
            now_ts=100.0,
            suppress_until_ts=suppress_until,
            deadband_base=0.0002,
            suppress_factor=0.2,
            hold_secs=120,
            enabled=True,
        )
        self.assertAlmostEqual(0.8, gate, places=8)

        # sign flip should trigger suppression
        gate, suppress_until = apply_cross_suppression(
            gate=0.9,
            q_base=0.001,
            prev_q_base=-0.001,
            now_ts=101.0,
            suppress_until_ts=suppress_until,
            deadband_base=0.0002,
            suppress_factor=0.2,
            hold_secs=120,
            enabled=True,
        )
        self.assertAlmostEqual(0.2, gate, places=8)
        self.assertGreater(suppress_until, 101.0)

        # still in hold window => suppression applied
        gate, _ = apply_cross_suppression(
            gate=0.7,
            q_base=0.0015,
            prev_q_base=0.001,
            now_ts=150.0,
            suppress_until_ts=suppress_until,
            deadband_base=0.0002,
            suppress_factor=0.2,
            hold_secs=120,
            enabled=True,
        )
        self.assertAlmostEqual(0.2, gate, places=8)
