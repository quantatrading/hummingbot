import unittest

from hummingbot.strategy.avellaneda_market_making.a_skew import compute_a_skew_price


class ASkewTests(unittest.TestCase):
    def test_equal_A_gives_zero_skew(self):
        skew_final, skew_smoothed, ratio_bps = compute_a_skew_price(
            r_base=70000.0,
            gamma=1.5,
            A_b=2.0,
            A_a=2.0,
            delta_mode="relative_to_r",
            max_bps=5.0,
            deadband_bps=0.2,
            eps=1e-9,
            prev=0.0,
            alpha=0.2,
        )
        self.assertAlmostEqual(0.0, ratio_bps, places=8)
        self.assertAlmostEqual(0.0, skew_smoothed, places=8)
        self.assertAlmostEqual(0.0, skew_final, places=8)

    def test_ab_greater_than_aa_positive_and_capped(self):
        skew_final, _, ratio_bps = compute_a_skew_price(
            r_base=70000.0,
            gamma=1.5,
            A_b=10.0,
            A_a=0.1,
            delta_mode="relative_to_r",
            max_bps=1.0,
            deadband_bps=0.2,
            eps=1e-9,
            prev=0.0,
            alpha=1.0,
        )
        cap = 70000.0 * 1.0 / 1e4
        self.assertGreater(ratio_bps, 0.0)
        self.assertGreater(skew_final, 0.0)
        self.assertLessEqual(abs(skew_final), cap + 1e-9)

    def test_aa_greater_than_ab_negative_and_capped(self):
        skew_final, _, ratio_bps = compute_a_skew_price(
            r_base=70000.0,
            gamma=1.5,
            A_b=0.1,
            A_a=10.0,
            delta_mode="relative_to_r",
            max_bps=1.0,
            deadband_bps=0.2,
            eps=1e-9,
            prev=0.0,
            alpha=1.0,
        )
        cap = 70000.0 * 1.0 / 1e4
        self.assertLess(ratio_bps, 0.0)
        self.assertLess(skew_final, 0.0)
        self.assertLessEqual(abs(skew_final), cap + 1e-9)

    def test_deadband_suppresses_small_ratios(self):
        skew_final, _, ratio_bps = compute_a_skew_price(
            r_base=1000.0,
            gamma=1.0,
            A_b=1.000001,
            A_a=1.0,
            delta_mode="relative_to_r",
            max_bps=5.0,
            deadband_bps=1.0,
            eps=1e-9,
            prev=0.0,
            alpha=1.0,
        )
        self.assertLess(abs(ratio_bps), 1.0)
        self.assertAlmostEqual(0.0, skew_final, places=10)

    def test_ewma_smoothing(self):
        skew_final, skew_smoothed, _ = compute_a_skew_price(
            r_base=1000.0,
            gamma=1.0,
            A_b=2.0,
            A_a=1.0,
            delta_mode="absolute_price",
            max_bps=1000.0,
            deadband_bps=0.0,
            eps=1e-9,
            prev=10.0,
            alpha=0.2,
        )
        self.assertNotEqual(skew_smoothed, 10.0)
        self.assertAlmostEqual(skew_final, skew_smoothed, places=8)

    def test_cap_price_respected(self):
        skew_final, _, _ = compute_a_skew_price(
            r_base=500.0,
            gamma=0.5,
            A_b=100.0,
            A_a=0.0001,
            delta_mode="absolute_price",
            max_bps=2.0,
            deadband_bps=0.0,
            eps=1e-9,
            prev=0.0,
            alpha=1.0,
        )
        cap = 500.0 * 2.0 / 1e4
        self.assertLessEqual(abs(skew_final), cap + 1e-9)

    def test_integration_smoke_r_shift_and_quotes_direction(self):
        r_base = 100.0
        delta_b = 2.0
        delta_a = 2.0
        skew_final, _, _ = compute_a_skew_price(
            r_base=r_base,
            gamma=1.0,
            A_b=4.0,
            A_a=1.0,
            delta_mode="relative_to_r",
            max_bps=100.0,
            deadband_bps=0.0,
            eps=1e-9,
            prev=0.0,
            alpha=1.0,
        )
        r_final = r_base + skew_final
        bid = r_final - delta_b
        ask = r_final + delta_a
        self.assertGreater(r_final, r_base)
        self.assertGreater(bid, r_base - delta_b)
        self.assertGreater(ask, r_base + delta_a)

