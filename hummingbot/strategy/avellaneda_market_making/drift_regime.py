import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple


@dataclass
class DriftRegimeConfig:
    z_threshold: float = 0.6
    confirm_secs: int = 30
    hysteresis_secs: int = 180
    kappa: float = 0.25
    z_clip: float = 2.0
    bias_max_bps: float = 20.0
    window_short_secs: int = 60
    window_long_secs: int = 300
    window_vol_secs: int = 300
    spread_adjust_enabled: bool = False
    spread_multiplier_max: float = 1.15
    inventory_risk_cap_quote: float = 200.0
    defensive_bias_max_bps: float = 35.0
    defensive_hold_secs: int = 300


class DriftRegimeEstimator:
    """
    Tracks short-horizon drift and emits an additive reservation-price bias in bps.
    """

    def __init__(self, config: DriftRegimeConfig):
        self._config = config
        self._last_price: Optional[float] = None
        self._last_sample_ts: Optional[float] = None

        self._short_returns: Deque[Tuple[float, float]] = deque()
        self._long_returns: Deque[Tuple[float, float]] = deque()
        self._vol_returns: Deque[Tuple[float, float]] = deque()

        self._short_sum = 0.0
        self._long_sum = 0.0
        self._vol_sum = 0.0
        self._vol_sumsq = 0.0

        self._regime = "NEUTRAL"
        self._pending_regime: Optional[str] = None
        self._pending_since_ts: Optional[float] = None
        self._last_switch_ts: float = 0.0

        self._defensive_until_ts: float = 0.0

    @property
    def regime(self) -> str:
        return self._regime

    def update_config(self, config: DriftRegimeConfig):
        self._config = config

    def _append_return(self, ts: float, value: float):
        self._short_returns.append((ts, value))
        self._short_sum += value
        self._long_returns.append((ts, value))
        self._long_sum += value
        self._vol_returns.append((ts, value))
        self._vol_sum += value
        self._vol_sumsq += value * value

    def _purge(self, now_ts: float):
        short_cutoff = now_ts - self._config.window_short_secs
        while self._short_returns and self._short_returns[0][0] < short_cutoff:
            _, v = self._short_returns.popleft()
            self._short_sum -= v

        long_cutoff = now_ts - self._config.window_long_secs
        while self._long_returns and self._long_returns[0][0] < long_cutoff:
            _, v = self._long_returns.popleft()
            self._long_sum -= v

        vol_cutoff = now_ts - self._config.window_vol_secs
        while self._vol_returns and self._vol_returns[0][0] < vol_cutoff:
            _, v = self._vol_returns.popleft()
            self._vol_sum -= v
            self._vol_sumsq -= v * v

    def _has_sufficient_history(self, now_ts: float) -> bool:
        if not self._short_returns or not self._long_returns or not self._vol_returns:
            return False
        short_span_ok = (now_ts - self._short_returns[0][0]) >= self._config.window_short_secs
        long_span_ok = (now_ts - self._long_returns[0][0]) >= self._config.window_long_secs
        vol_span_ok = (now_ts - self._vol_returns[0][0]) >= self._config.window_vol_secs
        return short_span_ok and long_span_ok and vol_span_ok

    @staticmethod
    def _clip(v: float, v_min: float, v_max: float) -> float:
        return max(v_min, min(v_max, v))

    def _update_regime(self, now_ts: float, z: float, mu_300: float) -> Tuple[bool, str]:
        threshold = self._config.z_threshold
        if z > threshold and mu_300 > 0:
            candidate = "UP"
        elif z < -threshold and mu_300 < 0:
            candidate = "DOWN"
        else:
            candidate = "NEUTRAL"

        if candidate == self._regime:
            self._pending_regime = None
            self._pending_since_ts = None
            return False, self._regime

        if self._pending_regime != candidate:
            self._pending_regime = candidate
            self._pending_since_ts = now_ts
            return False, self._regime

        confirm_ready = (now_ts - (self._pending_since_ts or now_ts)) >= self._config.confirm_secs
        hysteresis_ready = (now_ts - self._last_switch_ts) >= self._config.hysteresis_secs
        if confirm_ready and hysteresis_ready:
            self._regime = candidate
            self._last_switch_ts = now_ts
            self._pending_regime = None
            self._pending_since_ts = None
            return True, self._regime
        return False, self._regime

    def _base_bias_bps(self, z: float, sig_300: float) -> float:
        vol_bps = sig_300 * 1e4
        clipped_z = self._clip(z, -self._config.z_clip, self._config.z_clip)
        bias_bps = self._config.kappa * clipped_z * vol_bps
        return self._clip(bias_bps, -self._config.bias_max_bps, self._config.bias_max_bps)

    def update(
        self,
        timestamp: float,
        reference_price: float,
        net_base_inventory: float = 0.0,
        inventory_risk_quote: float = 0.0,
        enabled: bool = True,
    ) -> Dict[str, float | str | bool]:
        now_ts = float(timestamp)
        price = float(reference_price)

        if self._last_price is not None and price > 0 and self._last_price > 0 and now_ts > (self._last_sample_ts or 0):
            log_ret = math.log(price / self._last_price)
            self._append_return(now_ts, log_ret)
            self._purge(now_ts)

        self._last_price = price
        self._last_sample_ts = now_ts

        if len(self._short_returns) == 0 or len(self._long_returns) == 0 or len(self._vol_returns) == 0:
            return {
                "enabled": enabled,
                "ready": False,
                "regime": self._regime,
                "z": 0.0,
                "mu_60": 0.0,
                "mu_300": 0.0,
                "sig_300": 0.0,
                "bias_bps": 0.0,
                "regime_changed": False,
                "defensive_active": False,
                "defensive_triggered": False,
                "spread_multiplier": 1.0,
            }

        mu_60 = self._short_sum / max(1, len(self._short_returns))
        mu_300 = self._long_sum / max(1, len(self._long_returns))
        vol_n = max(1, len(self._vol_returns))
        vol_mean = self._vol_sum / vol_n
        vol_var = max((self._vol_sumsq / vol_n) - (vol_mean * vol_mean), 0.0)
        sig_300 = math.sqrt(vol_var)
        z = mu_60 / (sig_300 + 1e-12)
        regime_changed, regime = self._update_regime(now_ts, z, mu_300)

        ready = self._has_sufficient_history(now_ts)
        bias_bps = self._base_bias_bps(z, sig_300) if (enabled and ready) else 0.0

        defensive_triggered = False
        if enabled and inventory_risk_quote > self._config.inventory_risk_cap_quote:
            self._defensive_until_ts = max(self._defensive_until_ts, now_ts + self._config.defensive_hold_secs)
            defensive_triggered = True

        defensive_active = enabled and now_ts <= self._defensive_until_ts
        if defensive_active:
            flatten_sign = -1.0 if net_base_inventory > 0 else (1.0 if net_base_inventory < 0 else 0.0)
            if flatten_sign != 0.0:
                defensive_cap = max(self._config.bias_max_bps, self._config.defensive_bias_max_bps)
                min_abs_bias = min(defensive_cap, max(abs(bias_bps), self._config.bias_max_bps))
                bias_bps = flatten_sign * min_abs_bias

        # Final global cap under defensive/non-defensive modes
        cap_bps = self._config.defensive_bias_max_bps if defensive_active else self._config.bias_max_bps
        bias_bps = self._clip(bias_bps, -cap_bps, cap_bps)

        spread_multiplier = 1.0
        if enabled and self._config.spread_adjust_enabled:
            z_abs_ratio = min(abs(z) / max(self._config.z_clip, 1e-12), 1.0)
            spread_multiplier = 1.0 + (self._config.spread_multiplier_max - 1.0) * z_abs_ratio

        return {
            "enabled": enabled,
            "ready": ready,
            "regime": regime,
            "z": z,
            "mu_60": mu_60,
            "mu_300": mu_300,
            "sig_300": sig_300,
            "bias_bps": bias_bps,
            "regime_changed": regime_changed,
            "defensive_active": defensive_active,
            "defensive_triggered": defensive_triggered,
            "spread_multiplier": spread_multiplier,
        }
