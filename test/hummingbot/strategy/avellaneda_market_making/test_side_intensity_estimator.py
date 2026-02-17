import random
import unittest

from hummingbot.strategy.avellaneda_market_making.side_intensity_estimator import (
    SideIntensityConfig,
    SideIntensityEstimator,
)


class SideIntensityEstimatorTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SideIntensityConfig(
            window_secs=900,
            update_interval_secs=1,
            smoothing_beta=1.0,
            k_min=10.0,
            k_max=20000.0,
            min_events=5,
            use_censoring=True,
        )

    def test_recovers_k_from_synthetic_censored_data(self):
        random.seed(7)
        estimator = SideIntensityEstimator(self.cfg, k_initial=500.0, a_initial=0.5)
        true_A = 0.8
        true_k = 1200.0
        t = 0.0
        order_num = 0
        for _ in range(600):
            t += 1.0
            delta = random.uniform(0.0001, 0.0020)
            hazard = true_A * (2.718281828 ** (-true_k * delta))
            duration = 3.0
            fill_prob = 1.0 - (2.718281828 ** (-hazard * duration))
            oid = f"bid-{order_num}"
            order_num += 1
            estimator.register_order(oid, SideIntensityEstimator.SIDE_BID, delta, t)
            if random.random() < fill_prob:
                estimator.register_fill(oid, t + duration)
            else:
                estimator.register_cancel(oid, t + duration)
        m = estimator.update(t + 100.0)
        self.assertGreater(m.e_bid, self.cfg.min_events)
        # Relaxed tolerance for noisy censored online fit
        self.assertGreater(m.k_bid, 500.0)
        self.assertLess(m.k_bid, 2500.0)

    def test_no_events_keeps_previous_parameters(self):
        estimator = SideIntensityEstimator(self.cfg, k_initial=321.0, a_initial=0.7)
        t = 0.0
        for i in range(30):
            t += 1
            oid = f"ask-{i}"
            estimator.register_order(oid, SideIntensityEstimator.SIDE_ASK, 0.001, t)
            estimator.register_cancel(oid, t + 2)
        m = estimator.update(t + 100)
        self.assertEqual(321.0, m.k_ask)
        self.assertAlmostEqual(0.7, m.A_ask, places=6)

    def test_window_trimming_and_smoothing(self):
        cfg = SideIntensityConfig(
            window_secs=60,
            update_interval_secs=1,
            smoothing_beta=0.2,
            k_min=10.0,
            k_max=20000.0,
            min_events=1,
            use_censoring=True,
        )
        estimator = SideIntensityEstimator(cfg, k_initial=1000.0, a_initial=1.0)
        # Old observations outside window
        for i in range(10):
            estimator.register_order(f"old-{i}", SideIntensityEstimator.SIDE_BID, 0.002, i)
            estimator.register_fill(f"old-{i}", i + 2)
        # New observations in window
        base = 1000.0
        for i in range(20):
            ts = base + i
            estimator.register_order(f"new-{i}", SideIntensityEstimator.SIDE_BID, 0.0004, ts)
            estimator.register_fill(f"new-{i}", ts + 2)
        m = estimator.update(base + 40)
        self.assertLess(m.n_bid, 25)
        self.assertGreater(m.e_bid, 0)
