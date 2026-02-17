import math
import unittest

from hummingbot.strategy.avellaneda_market_making.drift_regime import DriftRegimeConfig, DriftRegimeEstimator


class DriftRegimeEstimatorTests(unittest.TestCase):
    def _fast_config(self, **kwargs):
        cfg = DriftRegimeConfig(
            z_threshold=0.4,
            confirm_secs=3,
            hysteresis_secs=5,
            kappa=0.25,
            z_clip=2.0,
            bias_max_bps=20.0,
            window_short_secs=10,
            window_long_secs=20,
            window_vol_secs=20,
            spread_adjust_enabled=False,
            spread_multiplier_max=1.15,
            inventory_risk_cap_quote=200.0,
            defensive_bias_max_bps=35.0,
            defensive_hold_secs=10,
        )
        for k, v in kwargs.items():
            setattr(cfg, k, v)
        return cfg

    def _run_series(self, est: DriftRegimeEstimator, prices, start_ts=0.0):
        out = None
        ts = start_ts
        for p in prices:
            ts += 1.0
            out = est.update(ts, p, net_base_inventory=0.0, inventory_risk_quote=0.0, enabled=True)
        return out

    def test_rising_series_enters_up_with_positive_bias(self):
        est = DriftRegimeEstimator(self._fast_config())
        prices = [100.0]
        for i in range(1, 120):
            prices.append(prices[-1] * (1 + 0.0008 + (0.0002 if i % 3 == 0 else -0.0001)))
        out = self._run_series(est, prices)
        self.assertTrue(out["ready"])
        self.assertEqual("UP", out["regime"])
        self.assertGreater(out["bias_bps"], 0.0)

    def test_falling_series_enters_down_with_negative_bias(self):
        est = DriftRegimeEstimator(self._fast_config())
        prices = [100.0]
        for i in range(1, 120):
            prices.append(prices[-1] * (1 - 0.0008 + (0.0001 if i % 4 == 0 else -0.0002)))
        out = self._run_series(est, prices)
        self.assertTrue(out["ready"])
        self.assertEqual("DOWN", out["regime"])
        self.assertLess(out["bias_bps"], 0.0)

    def test_mean_reverting_series_stays_neutral_with_small_bias(self):
        est = DriftRegimeEstimator(self._fast_config(z_threshold=0.7))
        prices = []
        base = 100.0
        for i in range(160):
            # bounded, oscillatory, near-zero drift
            p = base + (0.4 * math.sin(i / 2.0))
            prices.append(p)
        out = self._run_series(est, prices)
        self.assertEqual("NEUTRAL", out["regime"])
        self.assertLess(abs(out["bias_bps"]), 2.0)

    def test_hysteresis_prevents_flip_flops(self):
        est = DriftRegimeEstimator(self._fast_config(confirm_secs=3, hysteresis_secs=30))
        ts = 0.0
        price = 100.0

        # Strong up trend to lock UP
        for _ in range(80):
            ts += 1.0
            price *= 1.001
            out = est.update(ts, price, enabled=True)
        self.assertEqual("UP", out["regime"])

        # Immediate sharp down segment should not instantly flip due to hysteresis
        for _ in range(8):
            ts += 1.0
            price *= 0.998
            out = est.update(ts, price, enabled=True)
        self.assertNotEqual("DOWN", out["regime"])

    def test_bias_caps_applied(self):
        est = DriftRegimeEstimator(self._fast_config(kappa=10.0, bias_max_bps=5.0))
        prices = [100.0]
        for _ in range(150):
            prices.append(prices[-1] * 1.002)
        out = self._run_series(est, prices)
        self.assertLessEqual(abs(out["bias_bps"]), 5.0 + 1e-9)

    def test_defensive_override_respects_cap_and_hold(self):
        est = DriftRegimeEstimator(self._fast_config(bias_max_bps=20.0, defensive_bias_max_bps=35.0, defensive_hold_secs=20))
        ts = 0.0
        price = 100.0
        # warm up windows
        for _ in range(120):
            ts += 1.0
            price *= 1.0003
            est.update(ts, price, enabled=True)

        out = est.update(
            ts + 1,
            price,
            enabled=True,
            net_base_inventory=0.01,
            inventory_risk_quote=400.0,
        )
        self.assertTrue(out["defensive_active"])
        self.assertTrue(out["defensive_triggered"])
        self.assertLess(out["bias_bps"], 0.0)  # long inventory => down bias to encourage sells
        self.assertLessEqual(abs(out["bias_bps"]), 35.0 + 1e-9)

        out2 = est.update(
            ts + 5,
            price * 1.0001,
            enabled=True,
            net_base_inventory=0.01,
            inventory_risk_quote=0.0,  # below cap, should still be active during hold
        )
        self.assertTrue(out2["defensive_active"])

