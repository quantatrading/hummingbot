import unittest

from hummingbot.strategy.avellaneda_market_making.toxicity_gate import (
    REGIME_NORMAL,
    REGIME_TOXIC,
    ToxicityGate,
    ToxicityGateConfig,
)


class ToxicityGateTests(unittest.TestCase):
    def test_loss_bps_sign_correctness_for_buy_and_sell(self):
        gate = ToxicityGate(ToxicityGateConfig(enabled=True))

        # BUY fill is toxic only if future mid moves below fill.
        self.assertAlmostEqual(100.0, gate._adverse_selection_loss_bps("buy", 100.0, 99.0), places=6)
        self.assertAlmostEqual(0.0, gate._adverse_selection_loss_bps("buy", 100.0, 101.0), places=6)

        # SELL fill is toxic only if future mid moves above fill.
        self.assertAlmostEqual(100.0, gate._adverse_selection_loss_bps("sell", 100.0, 101.0), places=6)
        self.assertAlmostEqual(0.0, gate._adverse_selection_loss_bps("sell", 100.0, 99.0), places=6)

    def test_ewma_updates_with_known_dt(self):
        gate = ToxicityGate(
            ToxicityGateConfig(
                enabled=True,
                horizons_secs=[5],
                ewma_halflife_secs=10.0,
                weights={5: 1.0},
                trigger_bps=1000.0,
                release_bps=500.0,
                confirm_secs=100.0,
                hysteresis_secs=0.0,
                hold_secs=0.0,
            )
        )

        gate.on_fill("BUY", 100.0, 0.0)
        gate.update(now=5.0, mid_price=99.0)
        self.assertAlmostEqual(100.0, gate.ewma_loss_bps_by_horizon()[5], places=6)

        gate.on_fill("BUY", 100.0, 10.0)
        gate.update(now=15.0, mid_price=98.0)
        self.assertAlmostEqual(150.0, gate.ewma_loss_bps_by_horizon()[5], places=5)

    def test_state_transitions_and_pause_hold(self):
        gate = ToxicityGate(
            ToxicityGateConfig(
                enabled=True,
                horizons_secs=[1],
                ewma_halflife_secs=0.1,
                weights={1: 1.0},
                trigger_bps=1.5,
                release_bps=0.8,
                confirm_secs=2.0,
                hysteresis_secs=1.0,
                hold_secs=3.0,
                action_mode="pause_quote",
            )
        )

        gate.on_fill("BUY", 100.0, 0.0)
        gate.update(now=1.0, mid_price=99.0)
        self.assertEqual(REGIME_NORMAL, gate.regime)
        gate.update(now=3.0, mid_price=99.0)
        self.assertEqual(REGIME_TOXIC, gate.regime)
        self.assertTrue(gate.should_pause(3.5))
        self.assertFalse(gate.should_pause(6.1))

        gate.on_fill("BUY", 100.0, 7.0)
        gate.update(now=8.0, mid_price=101.0)
        gate.update(now=10.1, mid_price=101.0)
        self.assertEqual(REGIME_NORMAL, gate.regime)

    def test_widen_and_shrink_actions(self):
        gate = ToxicityGate(
            ToxicityGateConfig(
                enabled=True,
                horizons_secs=[1],
                ewma_halflife_secs=10.0,
                weights={1: 1.0},
                trigger_bps=1.0,
                release_bps=0.5,
                confirm_secs=0.0,
                hysteresis_secs=0.0,
                hold_secs=0.0,
                action_mode="widen_and_shrink",
                spread_mult_min=1.0,
                spread_mult_max=4.0,
                size_mult_min=0.2,
                size_mult_max=1.0,
                curve_power=1.0,
            )
        )

        gate.on_fill("BUY", 100.0, 0.0)
        gate.update(now=1.0, mid_price=99.0)

        self.assertEqual(REGIME_TOXIC, gate.regime)
        self.assertAlmostEqual(4.0, gate.spread_mult, places=6)
        self.assertAlmostEqual(0.2, gate.size_mult, places=6)

    def test_severe_toxicity_side_suppression(self):
        gate = ToxicityGate(
            ToxicityGateConfig(
                enabled=True,
                horizons_secs=[1],
                ewma_halflife_secs=1.0,
                weights={1: 1.0},
                trigger_bps=1.0,
                release_bps=0.5,
                confirm_secs=0.0,
                hysteresis_secs=0.0,
                hold_secs=5.0,
                action_mode="widen_and_shrink",
            )
        )

        gate.on_fill("BUY", 100.0, 0.0)
        gate.update(now=1.0, mid_price=99.0, inventory_stress=0.7)
        self.assertTrue(gate.is_side_suppressed("buy", now=1.0))
        self.assertFalse(gate.is_side_suppressed("sell", now=1.0))

        gate.update(now=7.0, mid_price=100.0, inventory_stress=0.0)
        self.assertFalse(gate.suppression_active(now=7.0))

