import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple


@dataclass
class DriftRegimeConfig:
    drift_z_threshold: float = 0.6
    drift_confirm_secs: int = 30
    drift_hysteresis_secs: int = 180
    drift_kappa: float = 0.25
    drift_bias_max_bps: float = 20.0
    drift_window_short_secs: int = 60
    drift_window_long_secs: int = 300
    drift_window_vol_secs: int = 300
    inventory_risk_cap_quote: float = 200.0
    defensive_bias_max_bps: float = 35.0
    defensive_hold_secs: int = 300


@dataclass
class DriftMetrics:
    ready: bool
    regime: str
    regime_changed: bool
    z: float
    mu_60: float
    mu_300: float
    sig_300: float
    tau: float
    drift_term_bps: float
    defensive_active: bool
    defensive_triggered: bool


class DriftRegimeEstimator:
    """HJB-compatible drift extension: drift_term = s * mu_log * tau, with regime gating and caps."""

    def __init__(self, config: DriftRegimeConfig):
        self._config = config
        self._last_price: Optional[float] = None
        self._last_ts: Optional[float] = None
        self._returns_short: Deque[Tuple[float, float]] = deque()
        self._returns_long: Deque[Tuple[float, float]] = deque()
        self._returns_vol: Deque[Tuple[float, float]] = deque()
        self._sum_short = 0.0
        self._sum_long = 0.0
        self._sum_vol = 0.0
        self._sumsq_vol = 0.0
        self._regime = "NEUTRAL"
        self._pending_regime: Optional[str] = None
        self._pending_since: Optional[float] = None
        self._last_switch_ts: float = 0.0
        self._defensive_until_ts: float = 0.0

    def update_config(self, config: DriftRegimeConfig):
        self._config = config

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(value, high))

    def _append_return(self, ts: float, value: float):
        self._returns_short.append((ts, value))
        self._returns_long.append((ts, value))
        self._returns_vol.append((ts, value))
        self._sum_short += value
        self._sum_long += value
        self._sum_vol += value
        self._sumsq_vol += value * value

    def _purge(self, now_ts: float):
        short_cutoff = now_ts - self._config.drift_window_short_secs
        long_cutoff = now_ts - self._config.drift_window_long_secs
        vol_cutoff = now_ts - self._config.drift_window_vol_secs

        while self._returns_short and self._returns_short[0][0] < short_cutoff:
            _, v = self._returns_short.popleft()
            self._sum_short -= v
        while self._returns_long and self._returns_long[0][0] < long_cutoff:
            _, v = self._returns_long.popleft()
            self._sum_long -= v
        while self._returns_vol and self._returns_vol[0][0] < vol_cutoff:
            _, v = self._returns_vol.popleft()
            self._sum_vol -= v
            self._sumsq_vol -= v * v

    def _window_ready(self, now_ts: float) -> bool:
        if not self._returns_short or not self._returns_long or not self._returns_vol:
            return False
        short_ready = (now_ts - self._returns_short[0][0]) >= self._config.drift_window_short_secs
        long_ready = (now_ts - self._returns_long[0][0]) >= self._config.drift_window_long_secs
        vol_ready = (now_ts - self._returns_vol[0][0]) >= self._config.drift_window_vol_secs
        return short_ready and long_ready and vol_ready

    def _infer_candidate_regime(self, z: float, mu_300: float) -> str:
        threshold = self._config.drift_z_threshold
        if z > threshold and mu_300 > 0:
            return "UP"
        if z < -threshold and mu_300 < 0:
            return "DOWN"
        return "NEUTRAL"

    def _update_regime(self, now_ts: float, candidate: str) -> bool:
        if candidate == self._regime:
            self._pending_regime = None
            self._pending_since = None
            return False

        if self._pending_regime != candidate:
            self._pending_regime = candidate
            self._pending_since = now_ts
            return False

        confirm_ok = (now_ts - (self._pending_since or now_ts)) >= self._config.drift_confirm_secs
        hysteresis_ok = (now_ts - self._last_switch_ts) >= self._config.drift_hysteresis_secs
        if confirm_ok and hysteresis_ok:
            self._regime = candidate
            self._last_switch_ts = now_ts
            self._pending_regime = None
            self._pending_since = None
            return True
        return False

    def evaluate(self, timestamp: float, reference_price: float, net_base_inventory: float, enabled: bool = True) -> DriftMetrics:
        now_ts = float(timestamp)
        px = float(reference_price)

        if self._last_price is not None and px > 0 and self._last_price > 0 and now_ts > float(self._last_ts or 0):
            log_ret = math.log(px / self._last_price)
            self._append_return(now_ts, log_ret)
            self._purge(now_ts)

        self._last_price = px
        self._last_ts = now_ts

        if len(self._returns_short) == 0 or len(self._returns_long) == 0 or len(self._returns_vol) == 0:
            return DriftMetrics(
                ready=False,
                regime=self._regime,
                regime_changed=False,
                z=0.0,
                mu_60=0.0,
                mu_300=0.0,
                sig_300=0.0,
                tau=self._config.drift_kappa * self._config.drift_window_short_secs,
                drift_term_bps=0.0,
                defensive_active=False,
                defensive_triggered=False,
            )

        mu_60 = self._sum_short / max(1, len(self._returns_short))
        mu_300 = self._sum_long / max(1, len(self._returns_long))
        n_vol = max(1, len(self._returns_vol))
        mean_vol = self._sum_vol / n_vol
        var_vol = max((self._sumsq_vol / n_vol) - (mean_vol * mean_vol), 0.0)
        sig_300 = math.sqrt(var_vol)
        z = mu_60 / (sig_300 + 1e-12)

        regime_changed = self._update_regime(now_ts, self._infer_candidate_regime(z, mu_300))
        ready = self._window_ready(now_ts)
        tau = self._config.drift_kappa * self._config.drift_window_short_secs

        drift_term_frac = 0.0
        if enabled and ready and self._regime != "NEUTRAL":
            drift_term_frac = mu_60 * tau

        inventory_risk_quote = abs(float(net_base_inventory) * px)
        defensive_triggered = False
        if enabled and inventory_risk_quote > self._config.inventory_risk_cap_quote:
            self._defensive_until_ts = max(self._defensive_until_ts, now_ts + self._config.defensive_hold_secs)
            defensive_triggered = True
        defensive_active = enabled and now_ts <= self._defensive_until_ts

        max_bps = self._config.defensive_bias_max_bps if defensive_active else self._config.drift_bias_max_bps
        max_frac = max_bps / 1e4

        # If current drift would worsen inventory, force sign to flatten inventory.
        if defensive_active and net_base_inventory != 0 and drift_term_frac != 0:
            flatten_sign = -1.0 if net_base_inventory > 0 else 1.0
            if drift_term_frac * flatten_sign < 0:
                drift_term_frac = abs(drift_term_frac) * flatten_sign

        drift_term_frac = self._clip(drift_term_frac, -max_frac, max_frac)
        drift_term_bps = drift_term_frac * 1e4

        return DriftMetrics(
            ready=ready,
            regime=self._regime,
            regime_changed=regime_changed,
            z=z,
            mu_60=mu_60,
            mu_300=mu_300,
            sig_300=sig_300,
            tau=tau,
            drift_term_bps=drift_term_bps,
            defensive_active=defensive_active,
            defensive_triggered=defensive_triggered,
        )
