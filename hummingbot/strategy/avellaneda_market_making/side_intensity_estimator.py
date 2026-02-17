import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple


@dataclass
class SideIntensityConfig:
    window_secs: int = 900
    update_interval_secs: int = 30
    smoothing_beta: float = 0.2
    k_min: float = 10.0
    k_max: float = 20000.0
    min_events: int = 5
    use_censoring: bool = True


@dataclass
class SideIntensityMetrics:
    k_bid: float
    k_ask: float
    A_bid: float
    A_ask: float
    n_bid: int
    n_ask: int
    e_bid: int
    e_ask: int
    updated: bool


class SideIntensityEstimator:
    SIDE_BID = "bid"
    SIDE_ASK = "ask"

    def __init__(self, config: SideIntensityConfig, k_initial: float = 100.0, a_initial: float = 1.0):
        self._config = config
        self._active_orders: Dict[str, Tuple[str, float, float]] = {}
        self._obs_bid: Deque[Tuple[float, float, float, int]] = deque()  # (end_ts, delta, duration, event)
        self._obs_ask: Deque[Tuple[float, float, float, int]] = deque()
        self._k_bid = float(k_initial)
        self._k_ask = float(k_initial)
        self._A_bid = max(float(a_initial), 1e-12)
        self._A_ask = max(float(a_initial), 1e-12)
        self._last_fit_ts = 0.0

    def update_config(self, config: SideIntensityConfig):
        self._config = config

    def register_order(self, order_id: str, side: str, delta: float, timestamp: float):
        if side not in {self.SIDE_BID, self.SIDE_ASK}:
            return
        self._active_orders[order_id] = (side, max(float(delta), 0.0), float(timestamp))

    def register_fill(self, order_id: str, timestamp: float):
        self._close_active_order(order_id, timestamp, event=1)

    def register_cancel(self, order_id: str, timestamp: float):
        self._close_active_order(order_id, timestamp, event=0)

    def _close_active_order(self, order_id: str, timestamp: float, event: int):
        rec = self._active_orders.pop(order_id, None)
        if rec is None:
            return
        side, delta, start_ts = rec
        duration = max(float(timestamp) - start_ts, 1e-6)
        end_ts = float(timestamp)
        obs = (end_ts, delta, duration if self._config.use_censoring else (1.0 if event == 1 else 0.0), event)
        if side == self.SIDE_BID:
            self._obs_bid.append(obs)
        else:
            self._obs_ask.append(obs)

    def _trim(self, now_ts: float):
        cutoff = now_ts - self._config.window_secs
        while self._obs_bid and self._obs_bid[0][0] < cutoff:
            self._obs_bid.popleft()
        while self._obs_ask and self._obs_ask[0][0] < cutoff:
            self._obs_ask.popleft()

    def _fit_side(self, obs: Deque[Tuple[float, float, float, int]], prev_k: float, prev_A: float) -> Tuple[float, float, int]:
        if len(obs) == 0:
            return prev_k, prev_A, 0

        event_count = sum(o[3] for o in obs)
        if event_count < self._config.min_events:
            return prev_k, prev_A, event_count

        # maximize log-likelihood over k with closed-form A(k)
        k_lo = max(self._config.k_min, 1e-9)
        k_hi = max(k_lo + 1e-9, self._config.k_max)
        n_grid = 60
        best_k = prev_k
        best_A = prev_A
        best_ll = -math.inf
        event_delta_sum = sum((o[3] * o[1]) for o in obs)

        def evaluate(k: float) -> Tuple[float, float]:
            S = 0.0
            for _, delta, duration, _ in obs:
                S += math.exp(-k * delta) * duration
            if S <= 0:
                return -math.inf, prev_A
            A = event_count / S
            if A <= 0:
                return -math.inf, prev_A
            logA = math.log(A)
            ll = event_count * logA - k * event_delta_sum - A * S
            return ll, A

        for i in range(n_grid + 1):
            k = k_lo + (k_hi - k_lo) * i / n_grid
            ll, A = evaluate(k)
            if ll > best_ll:
                best_ll = ll
                best_k = k
                best_A = A

        beta = min(max(self._config.smoothing_beta, 0.0), 1.0)
        k_new = beta * best_k + (1.0 - beta) * prev_k
        A_new = beta * best_A + (1.0 - beta) * prev_A
        return k_new, max(A_new, 1e-12), event_count

    def update(self, timestamp: float) -> SideIntensityMetrics:
        now_ts = float(timestamp)
        self._trim(now_ts)
        updated = False
        if (now_ts - self._last_fit_ts) >= self._config.update_interval_secs:
            old_k_bid, old_k_ask = self._k_bid, self._k_ask
            old_A_bid, old_A_ask = self._A_bid, self._A_ask
            self._k_bid, self._A_bid, e_bid = self._fit_side(self._obs_bid, self._k_bid, self._A_bid)
            self._k_ask, self._A_ask, e_ask = self._fit_side(self._obs_ask, self._k_ask, self._A_ask)
            updated = (
                old_k_bid != self._k_bid
                or old_k_ask != self._k_ask
                or old_A_bid != self._A_bid
                or old_A_ask != self._A_ask
            )
            self._last_fit_ts = now_ts
        else:
            e_bid = sum(o[3] for o in self._obs_bid)
            e_ask = sum(o[3] for o in self._obs_ask)

        return SideIntensityMetrics(
            k_bid=self._k_bid,
            k_ask=self._k_ask,
            A_bid=self._A_bid,
            A_ask=self._A_ask,
            n_bid=len(self._obs_bid),
            n_ask=len(self._obs_ask),
            e_bid=e_bid,
            e_ask=e_ask,
            updated=updated,
        )
