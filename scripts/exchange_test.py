import os
from collections import deque
from decimal import Decimal
from itertools import islice
from typing import Any, Dict, List, Optional

from pydantic import Field

from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class ExchangeTestConfig(BaseClientModel):
    script_file_name: str = os.path.basename(__file__)
    exchange: str = Field(
        "cryptocom",
        json_schema_extra={"prompt": "Exchange connector (e.g. binance, cryptocom, bybit): ", "prompt_on_new": True},
    )
    trading_pair: str = Field(
        "BTC-USD",
        json_schema_extra={"prompt": "Trading pair (e.g. BTC-USDT): ", "prompt_on_new": True},
    )
    refresh_interval: int = Field(
        5,
        json_schema_extra={"prompt": "Refresh interval in seconds (e.g. 5): ", "prompt_on_new": True},
    )
    order_book_depth: int = Field(
        5,
        json_schema_extra={"prompt": "Order book depth rows to display (e.g. 5): ", "prompt_on_new": True},
    )

    show_public_prices: bool = Field(
        True,
        json_schema_extra={"prompt": "Show public prices? (Yes/No): ", "prompt_on_new": True},
    )
    show_public_trades: bool = Field(
        True,
        json_schema_extra={"prompt": "Show public trades? (Yes/No): ", "prompt_on_new": True},
    )
    public_trades_display_count: int = Field(
        10,
        json_schema_extra={"prompt": "Number of recent public trades to display (e.g. 10): ", "prompt_on_new": True},
    )
    show_public_order_book: bool = Field(
        True,
        json_schema_extra={"prompt": "Show public order book? (Yes/No): ", "prompt_on_new": True},
    )
    show_private_open_orders: bool = Field(
        True,
        json_schema_extra={"prompt": "Show private open orders? (Yes/No): ", "prompt_on_new": True},
    )
    show_private_order_history: bool = Field(
        True,
        json_schema_extra={"prompt": "Show private order history cache? (Yes/No): ", "prompt_on_new": True},
    )
    show_private_balance: bool = Field(
        True,
        json_schema_extra={"prompt": "Show private balances? (Yes/No): ", "prompt_on_new": True},
    )


class ExchangeTest(ScriptStrategyBase):
    """
    Non-trading diagnostics strategy to inspect exchange data outputs.
    Supports selectable public/private output sections and reports whether
    each section is driven by websocket state and/or REST polling.
    """

    # Safe defaults when script is started without --conf.
    markets = {"cryptocom": {"BTC-USD"}}

    @classmethod
    def init_markets(cls, config: ExchangeTestConfig):
        cls.markets = {config.exchange: {config.trading_pair}}

    def __init__(self, connectors: Dict[str, ConnectorBase], config: Optional[ExchangeTestConfig] = None):
        resolved_config = config or ExchangeTestConfig()
        super().__init__(connectors, resolved_config)
        self.config = resolved_config
        self._next_refresh_ts: float = 0
        self._refresh_task = None
        self._last_not_ready_warning_ts: float = 0

        self._public_rest_last_ok_ts: float = 0
        self._private_rest_last_ok_ts: float = 0
        self._last_public_rest_price: Optional[float] = None
        self._last_private_error: str = ""
        self._last_public_error: str = ""
        self._public_ws_ticks: int = 0
        self._private_ws_ticks: int = 0
        self._last_public_ws_recv: float = 0
        self._last_private_ws_recv: float = 0
        self._last_ob_trade_price: Optional[Decimal] = None
        self._recent_public_trades = deque(maxlen=25)
        self._public_trade_seq: int = 0
        self._last_ob_diff_uid: int = 0
        self._order_book_ws_updates: int = 0
        self._last_order_book_update_ts: float = 0
        self._last_best_bid: Optional[Decimal] = None
        self._last_best_ask: Optional[Decimal] = None
        self._order_book_top_changes: int = 0

    def tick(self, timestamp: float):
        # Diagnostic scripts should keep running even if connector readiness is partial.
        # We still log readiness gaps periodically, but do not block on them.
        not_ready = [con for con in self.connectors.values() if not con.ready]
        if len(not_ready) > 0 and (timestamp - self._last_not_ready_warning_ts) >= 5:
            for con in not_ready:
                status = getattr(con, "status_dict", {})
                self.logger().warning(f"{con.name} is not ready. status={status}")
            self._last_not_ready_warning_ts = timestamp
        self._update_ws_counters_and_trade_cache()
        self.on_tick()

    def _update_ws_counters_and_trade_cache(self):
        connector = self.connectors.get(self.config.exchange)
        if connector is None:
            return

        # Count WS message flow by tracking last_recv_time changes.
        try:
            data_source = connector.order_book_tracker.data_source
            ws = getattr(data_source, "_ws_assistant", None)
            public_recv = float(getattr(ws, "last_recv_time", 0) or 0)
            if public_recv > 0 and public_recv != self._last_public_ws_recv:
                self._public_ws_ticks += 1
                self._last_public_ws_recv = public_recv
        except Exception:
            pass

        try:
            user_stream_tracker = getattr(connector, "_user_stream_tracker", None)
            private_recv = float(getattr(user_stream_tracker.data_source, "last_recv_time", 0) or 0)
            if private_recv > 0 and private_recv != self._last_private_ws_recv:
                self._private_ws_ticks += 1
                self._last_private_ws_recv = private_recv
        except Exception:
            pass

        # Cache recent public trades inferred from live order book trade price changes.
        try:
            ob = connector.order_book_tracker.order_books.get(self.config.trading_pair)
            if ob is None:
                return

            diff_uid = int(getattr(ob, "last_diff_uid", 0) or 0)
            if diff_uid > 0 and diff_uid != self._last_ob_diff_uid:
                self._order_book_ws_updates += 1
                self._last_order_book_update_ts = self.current_timestamp
                self._last_ob_diff_uid = diff_uid

            best_bid_row = next(ob.bid_entries(), None)
            best_ask_row = next(ob.ask_entries(), None)
            best_bid = Decimal(str(best_bid_row.price)) if best_bid_row is not None else None
            best_ask = Decimal(str(best_ask_row.price)) if best_ask_row is not None else None
            if (
                (best_bid is not None and best_bid != self._last_best_bid)
                or (best_ask is not None and best_ask != self._last_best_ask)
            ):
                self._order_book_top_changes += 1
            self._last_best_bid = best_bid
            self._last_best_ask = best_ask

            trade_price = ob.last_trade_price
            if trade_price is None:
                return
            trade_price_decimal = Decimal(str(trade_price))
            if trade_price_decimal.is_nan():
                return
            if self._last_ob_trade_price is None or trade_price_decimal != self._last_ob_trade_price:
                self._public_trade_seq += 1
                self._recent_public_trades.append(
                    {
                        "seq": self._public_trade_seq,
                        "ts": int(self.current_timestamp),
                        "price": trade_price_decimal,
                    }
                )
                self._last_ob_trade_price = trade_price_decimal
        except Exception:
            pass

    def on_tick(self):
        if self.current_timestamp < self._next_refresh_ts:
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        # REST probes are intentionally throttled by refresh_interval.
        # WS-backed values are read live in format_status() on each render.
        self._next_refresh_ts = self.current_timestamp + max(1, int(self.config.refresh_interval))
        self._refresh_task = safe_ensure_future(self._refresh_rest_state())

    async def _refresh_rest_state(self):
        connector = self.connectors[self.config.exchange]

        if any([self.config.show_public_prices, self.config.show_public_trades, self.config.show_public_order_book]):
            try:
                prices = await connector.get_last_traded_prices(trading_pairs=[self.config.trading_pair])
                self._last_public_rest_price = prices.get(self.config.trading_pair)
                self._public_rest_last_ok_ts = self.current_timestamp
                self._last_public_error = ""
            except Exception as e:
                self._last_public_error = str(e)

        if any([self.config.show_private_balance, self.config.show_private_open_orders, self.config.show_private_order_history]):
            try:
                # REST private probe + updates cached balances.
                await connector._update_balances()
                self._private_rest_last_ok_ts = self.current_timestamp
                self._last_private_error = ""
            except Exception as e:
                self._last_private_error = str(e)

    def _public_ws_status(self) -> str:
        try:
            data_source = self.connectors[self.config.exchange].order_book_tracker.data_source
            ws = getattr(data_source, "_ws_assistant", None)
            if ws is None:
                return "DISCONNECTED"
            return "CONNECTED" if getattr(ws, "last_recv_time", 0) > 0 else "DISCONNECTED"
        except Exception:
            return "UNKNOWN"

    def _public_ws_last_recv_age(self) -> str:
        try:
            data_source = self.connectors[self.config.exchange].order_book_tracker.data_source
            ws = getattr(data_source, "_ws_assistant", None)
            if ws is None:
                return "n/a"
            last_recv = float(getattr(ws, "last_recv_time", 0) or 0)
            if last_recv <= 0:
                return "n/a"
            return f"{int(self.current_timestamp - last_recv)}s ago"
        except Exception:
            return "n/a"

    def _private_ws_status(self) -> str:
        try:
            user_stream_tracker = getattr(self.connectors[self.config.exchange], "_user_stream_tracker", None)
            if user_stream_tracker is None:
                return "DISCONNECTED"
            last_recv = user_stream_tracker.data_source.last_recv_time
            return "CONNECTED" if last_recv > 0 else "DISCONNECTED"
        except Exception:
            return "UNKNOWN"

    def _private_ws_last_recv_age(self) -> str:
        try:
            user_stream_tracker = getattr(self.connectors[self.config.exchange], "_user_stream_tracker", None)
            if user_stream_tracker is None:
                return "n/a"
            last_recv = float(user_stream_tracker.data_source.last_recv_time or 0)
            if last_recv <= 0:
                return "n/a"
            return f"{int(self.current_timestamp - last_recv)}s ago"
        except Exception:
            return "n/a"

    def _rest_status(self, last_ok_ts: float) -> str:
        if last_ok_ts <= 0:
            return "NO_DATA"
        age = self.current_timestamp - last_ok_ts
        stale_threshold = max(5, int(self.config.refresh_interval) * 3)
        return f"OK ({int(age)}s ago)" if age <= stale_threshold else f"STALE ({int(age)}s ago)"

    def format_status(self) -> str:
        try:
            lines: List[str] = []
            connector = self.connectors.get(self.config.exchange)
            if connector is None:
                return f"Exchange Test\nConnector '{self.config.exchange}' is not loaded."

            lines.append("")
            lines.append("Exchange Test")
            lines.append(f"Connector: {self.config.exchange}")
            lines.append(f"Pair: {self.config.trading_pair}")
            lines.append(f"Connector ready: {connector.ready}")
            lines.append(f"REST poll interval: {self.config.refresh_interval}s")
            lines.append("WS mode: live (updates on every status refresh)")
            status = getattr(connector, "status_dict", {})
            if len(status) > 0:
                lines.append("Readiness detail:")
                for key, val in status.items():
                    lines.append(f"  {key}: {val}")

            lines.append("")
            lines.append("Transport")
            lines.append(
                f"Public WS: {self._public_ws_status()} (last recv {self._public_ws_last_recv_age()}, ticks={self._public_ws_ticks})"
            )
            lines.append(f"Public REST: {self._rest_status(self._public_rest_last_ok_ts)}")
            lines.append(
                f"Private WS: {self._private_ws_status()} (last recv {self._private_ws_last_recv_age()}, ticks={self._private_ws_ticks})"
            )
            lines.append(f"Private REST: {self._rest_status(self._private_rest_last_ok_ts)}")

            if self._last_public_error:
                lines.append(f"Last public error: {self._last_public_error}")
            if self._last_private_error:
                lines.append(f"Last private error: {self._last_private_error}")

            if self.config.show_public_prices:
                lines.append("")
                lines.append("Public: Prices [WS+REST]")
                try:
                    mid_price = connector.get_mid_price(self.config.trading_pair)
                    bid = connector.get_price(self.config.trading_pair, is_buy=False)
                    ask = connector.get_price(self.config.trading_pair, is_buy=True)
                    lines.append(f"REST last traded: {self._last_public_rest_price} [REST]")
                    lines.append(f"Mid: {mid_price} | Bid: {bid} | Ask: {ask} [WS]")
                except Exception as e:
                    lines.append(f"Price read error: {e}")

            ob = connector.order_book_tracker.order_books.get(self.config.trading_pair)

            if self.config.show_public_trades:
                lines.append("")
                lines.append("Public: Trades [WS]")
                if ob is None:
                    lines.append("Order book not initialized for pair yet.")
                else:
                    lines.append(f"Last trade price (from order book): {ob.last_trade_price} [WS]")
                    if len(self._recent_public_trades) == 0:
                        lines.append("No recent trade updates captured yet.")
                    else:
                        show_n = max(1, int(self.config.public_trades_display_count))
                        lines.append(f"Recent trades (live cache, last {show_n}) [WS]:")
                        for t in list(self._recent_public_trades)[-show_n:]:
                            lines.append(f"  n={t['seq']} t={t['ts']} price={t['price']}")

                    # Connector-level parsed trade payload validation (the exact fields Avellaneda depends on).
                    data_source = connector.order_book_tracker.data_source
                    parsed_getter = getattr(data_source, "get_recent_parsed_trades", None)
                    if callable(parsed_getter):
                        parsed_trades = parsed_getter(self.config.trading_pair, max(1, int(self.config.public_trades_display_count)))
                        lines.append(f"Parsed trade payloads (last {len(parsed_trades)}) [WS]:")
                        if len(parsed_trades) == 0:
                            lines.append("  No parsed trades available from connector data source yet.")
                        else:
                            ids = [str(t.get("trade_id", "")) for t in parsed_trades]
                            unique_count = len(set(ids))
                            lines.append(f"  trade_id unique check: {unique_count}/{len(ids)} unique")
                            now_ts = float(self.current_timestamp)
                            for t in parsed_trades:
                                trade_id = str(t.get("trade_id", ""))
                                price_raw = t.get("price")
                                amount_raw = t.get("amount")
                                trade_type = str(t.get("trade_type", "")).lower()
                                ts = float(t.get("timestamp", 0) or 0)

                                # Field validations expected by strategy logic.
                                price_ok = False
                                amount_ok = False
                                ts_ok = False
                                try:
                                    price_ok = Decimal(str(price_raw)) > Decimal("0")
                                except Exception:
                                    price_ok = False
                                try:
                                    amount_ok = Decimal(str(amount_raw)) > Decimal("0")
                                except Exception:
                                    amount_ok = False
                                age = now_ts - ts if ts > 0 else 999999.0
                                ts_ok = ts > 0 and abs(age) <= 15
                                type_ok = trade_type in {"buy", "sell"}
                                id_ok = len(trade_id) > 0

                                lines.append(
                                    f"  id={trade_id} id_ok={id_ok} price={price_raw} price_ok={price_ok} "
                                    f"amount={amount_raw} amount_ok={amount_ok} type={trade_type} type_ok={type_ok} "
                                    f"ts={ts:.3f} age={age:.2f}s ts_ok={ts_ok}"
                                )

            if self.config.show_public_order_book:
                lines.append("")
                lines.append("Public: Order Book [WS]")
                if ob is None:
                    lines.append("Order book not initialized for pair yet.")
                else:
                    ob_update_age = (
                        f"{int(self.current_timestamp - self._last_order_book_update_ts)}s ago"
                        if self._last_order_book_update_ts > 0
                        else "n/a"
                    )
                    lines.append(
                        f"OB ws updates={self._order_book_ws_updates} top_changes={self._order_book_top_changes} "
                        f"last_diff_uid={int(getattr(ob, 'last_diff_uid', 0) or 0)} "
                        f"snapshot_uid={int(getattr(ob, 'snapshot_uid', 0) or 0)} "
                        f"last_update={ob_update_age}"
                    )
                    depth = max(1, int(self.config.order_book_depth))
                    # Requested output layout: asks above bids, asks in descending price order.
                    ask_rows = list(islice(ob.ask_entries(), depth))
                    ask_rows_desc = sorted(ask_rows, key=lambda r: Decimal(str(r.price)), reverse=True)
                    bid_rows = list(islice(ob.bid_entries(), depth))
                    lines.append("Top asks (desc):")
                    for row in ask_rows_desc:
                        lines.append(f"  {row.price} x {row.amount}")
                    lines.append("Top bids:")
                    for row in bid_rows:
                        lines.append(f"  {row.price} x {row.amount}")

            if self.config.show_private_open_orders:
                lines.append("")
                lines.append("Private: Open Orders [LOCAL CACHE <- WS+REST]")
                open_orders = self.get_active_orders(self.config.exchange)
                if len(open_orders) == 0:
                    lines.append("No active open orders.")
                else:
                    for order in open_orders:
                        side = "BUY" if order.is_buy else "SELL"
                        lines.append(
                            f"  {order.client_order_id} | {side} | {order.trading_pair} | qty={order.quantity} | px={order.price}"
                        )

            if self.config.show_private_order_history:
                lines.append("")
                lines.append("Private: Order History (local cache) [LOCAL CACHE <- WS+REST]")
                order_tracker = getattr(connector, "_order_tracker", None)
                if order_tracker is None:
                    lines.append("Order tracker not available.")
                else:
                    cached = list(order_tracker.cached_orders.values())[-10:]
                    lost = list(order_tracker.lost_orders.values())[-10:]
                    if len(cached) == 0 and len(lost) == 0:
                        lines.append("No cached/lost historical orders.")
                    else:
                        for o in cached:
                            lines.append(f"  CACHED {o.client_order_id} | {o.trading_pair} | state={o.current_state}")
                        for o in lost:
                            lines.append(f"  LOST   {o.client_order_id} | {o.trading_pair} | state={o.current_state}")

            if self.config.show_private_balance:
                lines.append("")
                lines.append("Private: Balance [WS+REST CACHE]")
                lines.append(f"Balance WS stream: {self._private_ws_status()} (last recv {self._private_ws_last_recv_age()})")
                lines.append(f"Balance REST refresh: {self._rest_status(self._private_rest_last_ok_ts)}")
                lines.append("Balance cache note: connector stores balances in a shared cache updated by WS events and REST polling.")
                balances = connector.get_all_balances()
                if len(balances) == 0:
                    lines.append("No balances loaded.")
                else:
                    for asset, total in sorted(balances.items()):
                        available = connector.get_available_balance(asset)
                        lines.append(f"  {asset}: total={Decimal(str(total))} available={Decimal(str(available))}")

            return "\n".join(lines)
        except Exception as e:
            return f"Exchange Test\nformat_status error: {e}"
