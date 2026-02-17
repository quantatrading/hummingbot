import unittest

from hummingbot.strategy.avellaneda_market_making.drift_regime import DriftRegimeConfig, DriftRegimeEstimator


class DriftRegimeEstimatorTests(unittest.TestCase):
    def _make_config(self, **kwargs):
        config = DriftRegimeConfig(
            drift_z_threshold=0.6,
            drift_confirm_secs=3,
            drift_hysteresis_secs=5,
            drift_kappa=0.25,
            drift_bias_max_bps=20.0,
            drift_window_short_secs=10,
            drift_window_long_secs=30,
            drift_window_vol_secs=30,
            inventory_risk_cap_quote=200.0,
            defensive_bias_max_bps=35.0,
            defensive_hold_secs=10,
        )
        for key, value in kwargs.items():
            setattr(config, key, value)
        return config

    def _pump_prices(self, estimator: DriftRegimeEstimator, prices):
        out = None
        ts = 0.0
        for px in prices:
            ts += 1
            out = estimator.evaluate(ts, px, net_base_inventory=0.0, enabled=True)
        return out

    def test_uptrend_positive_drift_term(self):
        estimator = DriftRegimeEstimator(self._make_config())
        px = [100.0]
        for i in range(1, 100):
            px.append(px[-1] * (1.0 + 0.0006 + (0.0001 if i % 2 == 0 else -0.00005)))
        out = self._pump_prices(estimator, px)
        self.assertTrue(out.ready)
        self.assertEqual("UP", out.regime)
        self.assertGreater(out.drift_term_bps, 0.0)
        self.assertEqual(2.5, out.tau)

    def test_downtrend_negative_drift_term(self):
        estimator = DriftRegimeEstimator(self._make_config())
        px = [100.0]
        for i in range(1, 100):
            px.append(px[-1] * (1.0 - 0.0006 + (0.00005 if i % 2 == 0 else -0.0001)))
        out = self._pump_prices(estimator, px)
        self.assertTrue(out.ready)
        self.assertEqual("DOWN", out.regime)
        self.assertLess(out.drift_term_bps, 0.0)

    def test_neutral_when_regime_not_met(self):
        estimator = DriftRegimeEstimator(self._make_config(drift_z_threshold=5.0))
        px = [100.0]
        for i in range(1, 100):
            px.append(px[-1] * (1.0 + (0.0001 if i % 2 == 0 else -0.0001)))
        out = self._pump_prices(estimator, px)
        self.assertEqual("NEUTRAL", out.regime)
        self.assertEqual(0.0, out.drift_term_bps)

    def test_hysteresis_prevents_fast_flip(self):
        estimator = DriftRegimeEstimator(self._make_config(drift_hysteresis_secs=50, drift_confirm_secs=2))
        ts = 0
        p = 100.0
        out = None

        for _ in range(80):
            ts += 1
            p *= 1.001
            out = estimator.evaluate(ts, p, net_base_inventory=0.0, enabled=True)
        self.assertEqual("UP", out.regime)

        for _ in range(8):
            ts += 1
            p *= 0.998
            out = estimator.evaluate(ts, p, net_base_inventory=0.0, enabled=True)
        self.assertNotEqual("DOWN", out.regime)

    def test_drift_term_cap_applied(self):
        estimator = DriftRegimeEstimator(self._make_config(drift_kappa=5.0, drift_bias_max_bps=3.0))
        px = [100.0]
        for _ in range(150):
            px.append(px[-1] * 1.003)
        out = self._pump_prices(estimator, px)
        self.assertLessEqual(abs(out.drift_term_bps), 3.0 + 1e-9)

    def test_defensive_override_sign_for_long_inventory(self):
        estimator = DriftRegimeEstimator(
            self._make_config(drift_bias_max_bps=5.0, defensive_bias_max_bps=15.0, defensive_hold_secs=20)
        )
        ts = 0
        p = 100.0
        out = None

        # Warm-up with uptrend to produce positive drift
        for _ in range(100):
            ts += 1
            p *= 1.001
            out = estimator.evaluate(ts, p, net_base_inventory=0.0, enabled=True)
        self.assertGreater(out.drift_term_bps, 0.0)

        # Long inventory in defensive mode should force flattening sign (negative for long)
        out = estimator.evaluate(ts + 1, p, net_base_inventory=3.0, enabled=True)
        self.assertTrue(out.defensive_triggered)
        self.assertTrue(out.defensive_active)
        self.assertLessEqual(abs(out.drift_term_bps), 15.0 + 1e-9)
        self.assertLess(out.drift_term_bps, 0.0)
