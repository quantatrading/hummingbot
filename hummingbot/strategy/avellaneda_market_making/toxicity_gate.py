import heapq
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple


REGIME_NORMAL = "NORMAL"
REGIME_TOXIC = "TOXIC"


@dataclass
class ToxicityGateConfig:
    enabled: bool = False
    horizons_secs: List[int] = field(default_factory=lambda: [5, 10, 30])
    ewma_halflife_secs: float = 120.0
    weights: Dict[int, float] = field(default_factory=lambda: {5: 0.5, 10: 0.3, 30: 0.2})
    trigger_bps: float = 1.5
    release_bps: float = 0.8
    confirm_secs: float = 20.0
    hysteresis_secs: float = 120.0
    hold_secs: float = 60.0
    action_mode: str = "widen_only"
    spread_mult_min: float = 1.0
    spread_mult_max: float = 4.0
    size_mult_min: float = 0.2
    size_mult_max: float = 1.0
    curve_power: float = 1.0
    debug: bool = False

    def normalized(self) -> "ToxicityGateConfig":
        horizons = sorted(set(max(1, int(h)) for h in self.horizons_secs))
        parsed_weights: Dict[int, float] = {}
        for horizon in horizons:
            parsed_weights[horizon] = max(0.0, float(self.weights.get(horizon, 0.0)))
        if sum(parsed_weights.values()) <= 0:
            uniform_weight = 1.0 / max(len(horizons), 1)
            parsed_weights = {horizon: uniform_weight for horizon in horizons}
        else:
            total_weight = sum(parsed_weights.values())
            parsed_weights = {horizon: weight / total_weight for horizon, weight in parsed_weights.items()}

        return ToxicityGateConfig(
            enabled=bool(self.enabled),
            horizons_secs=horizons,
            ewma_halflife_secs=max(float(self.ewma_halflife_secs), 1e-9),
            weights=parsed_weights,
            trigger_bps=max(float(self.trigger_bps), 1e-9),
            release_bps=max(float(self.release_bps), 0.0),
            confirm_secs=max(float(self.confirm_secs), 0.0),
            hysteresis_secs=max(float(self.hysteresis_secs), 0.0),
            hold_secs=max(float(self.hold_secs), 0.0),
            action_mode=str(self.action_mode),
            spread_mult_min=max(float(self.spread_mult_min), 0.0),
            spread_mult_max=max(float(self.spread_mult_max), 0.0),
            size_mult_min=max(float(self.size_mult_min), 0.0),
            size_mult_max=max(float(self.size_mult_max), 0.0),
            curve_power=max(float(self.curve_power), 1e-9),
            debug=bool(self.debug),
        )


class ToxicityGate:
    _WINDOW_SECS = 600.0

    def __init__(self, config: ToxicityGateConfig):
        self._config = config.normalized()
        self._pending: List[Tuple[float, int, int, str, float]] = []
        self._fill_seq: int = 0
        self._ewma_adv_bps: Dict[int, float] = {}
        self._ewma_last_ts: Dict[int, float] = {}
        self._evaluated_ts: Dict[int, Deque[float]] = {h: deque() for h in self._config.horizons_secs}

        self._tox_bps: float = 0.0
        self._regime: str = REGIME_NORMAL
        self._spread_mult: float = 1.0
        self._size_mult: float = 1.0
        self._pause_until_ts: float = 0.0
        self._last_update_ts: float = 0.0

        self._above_trigger_since: Optional[float] = None
        self._below_release_since: Optional[float] = None
        self._last_switch_ts: Optional[float] = None

    def update_config(self, config: ToxicityGateConfig):
        previous_horizons = set(self._config.horizons_secs)
        self._config = config.normalized()
        current_horizons = set(self._config.horizons_secs)

        self._ewma_adv_bps = {h: v for h, v in self._ewma_adv_bps.items() if h in current_horizons}
        self._ewma_last_ts = {h: v for h, v in self._ewma_last_ts.items() if h in current_horizons}

        new_eval_ts: Dict[int, Deque[float]] = {}
        for horizon in current_horizons:
            new_eval_ts[horizon] = self._evaluated_ts.get(horizon, deque())
        self._evaluated_ts = new_eval_ts

        if current_horizons != previous_horizons:
            self._pending = [entry for entry in self._pending if entry[2] in current_horizons]
            heapq.heapify(self._pending)

    @property
    def tox_bps(self) -> float:
        return self._tox_bps

    @property
    def regime(self) -> str:
        return self._regime

    @property
    def spread_mult(self) -> float:
        return self._spread_mult

    @property
    def size_mult(self) -> float:
        return self._size_mult

    @property
    def pause_until_ts(self) -> float:
        return self._pause_until_ts

    def ewma_adv_bps_by_horizon(self) -> Dict[int, float]:
        return {h: self._ewma_adv_bps.get(h, 0.0) for h in self._config.horizons_secs}

    def evaluated_counts(self, window_secs: float = _WINDOW_SECS) -> Dict[int, int]:
        counts: Dict[int, int] = {}
        cutoff = self._last_update_ts - max(0.0, float(window_secs))
        for horizon in self._config.horizons_secs:
            timestamps = self._evaluated_ts.get(horizon, deque())
            counts[horizon] = sum(1 for ts in timestamps if ts >= cutoff)
        return counts

    def should_pause(self, now: float) -> bool:
        if not self._config.enabled:
            return False
        if self._config.action_mode != "pause_quote":
            return False
        return float(now) < self._pause_until_ts

    def on_fill(self, trade_type, fill_price: float, timestamp: float):
        if not self._config.enabled:
            return

        side = self._normalize_trade_side(trade_type)
        price = float(fill_price)
        ts = float(timestamp)
        if side is None or price <= 0 or ts < 0:
            return

        for horizon in self._config.horizons_secs:
            self._fill_seq += 1
            due_ts = ts + float(horizon)
            heapq.heappush(self._pending, (due_ts, self._fill_seq, horizon, side, price))

    def update(self, now: float, mid_price: Optional[float]):
        now_ts = float(now)
        mid = float(mid_price) if mid_price is not None else 0.0

        if not self._config.enabled:
            self._tox_bps = 0.0
            self._regime = REGIME_NORMAL
            self._spread_mult = 1.0
            self._size_mult = 1.0
            self._last_update_ts = now_ts
            return

        self._last_update_ts = now_ts
        while self._pending and self._pending[0][0] <= now_ts:
            due_ts, _, horizon, side, fill_price = heapq.heappop(self._pending)
            if mid <= 0 or fill_price <= 0:
                continue
            adv_bps = self._adverse_selection_bps(side=side, fill_price=fill_price, mid_price=mid)
            self._update_horizon_ewma(horizon=horizon, value=adv_bps, timestamp=now_ts)
            self._record_evaluation(horizon=horizon, timestamp=now_ts)

        self._tox_bps = self._compute_toxicity_bps()
        self._update_regime(now_ts)
        self._update_actions()

    def _adverse_selection_bps(self, side: str, fill_price: float, mid_price: float) -> float:
        if side == "buy":
            return 1e4 * (mid_price - fill_price) / fill_price
        return 1e4 * (fill_price - mid_price) / fill_price

    def _update_horizon_ewma(self, horizon: int, value: float, timestamp: float):
        if horizon not in self._ewma_adv_bps:
            self._ewma_adv_bps[horizon] = float(value)
            self._ewma_last_ts[horizon] = float(timestamp)
            return

        last_ts = self._ewma_last_ts.get(horizon, float(timestamp))
        dt_secs = max(0.0, float(timestamp) - float(last_ts))
        alpha = 1.0 - math.exp(-math.log(2.0) * dt_secs / self._config.ewma_halflife_secs)
        prev = self._ewma_adv_bps[horizon]
        self._ewma_adv_bps[horizon] = alpha * float(value) + (1.0 - alpha) * float(prev)
        self._ewma_last_ts[horizon] = float(timestamp)

    def _record_evaluation(self, horizon: int, timestamp: float):
        q = self._evaluated_ts.setdefault(horizon, deque())
        q.append(float(timestamp))
        cutoff = float(timestamp) - self._WINDOW_SECS
        while q and q[0] < cutoff:
            q.popleft()

    def _compute_toxicity_bps(self) -> float:
        toxicity = 0.0
        for horizon in self._config.horizons_secs:
            ewma = self._ewma_adv_bps.get(horizon)
            if ewma is None:
                continue
            weight = self._config.weights.get(horizon, 0.0)
            toxicity += weight * max(0.0, -float(ewma))
        return max(0.0, toxicity)

    def _can_switch(self, now_ts: float) -> bool:
        if self._last_switch_ts is None:
            return True
        return (now_ts - self._last_switch_ts) >= self._config.hysteresis_secs

    def _update_regime(self, now_ts: float):
        if self._tox_bps > self._config.trigger_bps:
            if self._above_trigger_since is None:
                self._above_trigger_since = now_ts
        else:
            self._above_trigger_since = None

        if self._tox_bps < self._config.release_bps:
            if self._below_release_since is None:
                self._below_release_since = now_ts
        else:
            self._below_release_since = None

        if self._regime == REGIME_NORMAL:
            if (
                self._above_trigger_since is not None
                and (now_ts - self._above_trigger_since) >= self._config.confirm_secs
                and self._can_switch(now_ts)
            ):
                self._regime = REGIME_TOXIC
                self._last_switch_ts = now_ts
                self._below_release_since = None
                if self._config.action_mode == "pause_quote":
                    self._pause_until_ts = now_ts + self._config.hold_secs
        else:
            if (
                self._below_release_since is not None
                and (now_ts - self._below_release_since) >= self._config.confirm_secs
                and self._can_switch(now_ts)
            ):
                self._regime = REGIME_NORMAL
                self._last_switch_ts = now_ts
                self._above_trigger_since = None

    def _update_actions(self):
        self._spread_mult = 1.0
        self._size_mult = 1.0
        if self._regime != REGIME_TOXIC:
            return

        trigger = max(self._config.trigger_bps, 1e-9)
        x = max(0.0, min(1.0, (self._tox_bps - trigger) / trigger))
        x_curve = x ** self._config.curve_power

        spread_min = min(self._config.spread_mult_min, self._config.spread_mult_max)
        spread_max = max(self._config.spread_mult_min, self._config.spread_mult_max)
        self._spread_mult = spread_min + (spread_max - spread_min) * x_curve

        if self._config.action_mode == "widen_and_shrink":
            size_min = min(self._config.size_mult_min, self._config.size_mult_max)
            size_max = max(self._config.size_mult_min, self._config.size_mult_max)
            self._size_mult = size_max - (size_max - size_min) * x_curve
        else:
            self._size_mult = 1.0

    def _normalize_trade_side(self, trade_type) -> Optional[str]:
        value = ""
        if isinstance(trade_type, str):
            value = trade_type.lower()
        elif hasattr(trade_type, "name"):
            value = str(trade_type.name).lower()
        else:
            value = str(trade_type).lower()

        if "buy" in value:
            return "buy"
        if "sell" in value:
            return "sell"
        return None
