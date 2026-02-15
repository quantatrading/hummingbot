import os
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import Field

from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class ExchangeTestConfig(BaseClientModel):
    script_file_name: str = os.path.basename(__file__)
    exchange: str = Field(
        "binance_paper_trade",
        json_schema_extra={"prompt": "Exchange connector (e.g. binance, cryptocom, bybit): ", "prompt_on_new": True},
    )
    trading_pair: str = Field(
        "BTC-USDT",
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

    markets = {"binance_paper_trade": {"BTC-USDT"}}

    @classmethod
    def init_markets(cls, config: ExchangeTestConfig):
        cls.markets = {config.exchange: {config.trading_pair}}

    def __init__(self, connectors: Dict[str, ConnectorBase], config: ExchangeTestConfig):
        super().__init__(connectors, config)
        self.config = config
        self._next_refresh_ts: float = 0
        self._refresh_task = None

        self._public_rest_last_ok_ts: float = 0
        self._private_rest_last_ok_ts: float = 0
        self._last_public_rest_price: Optional[float] = None
        self._last_private_error: str = ""
        self._last_public_error: str = ""

    def on_tick(self):
        if self.current_timestamp < self._next_refresh_ts:
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._next_refresh_ts = self.current_timestamp + max(1, int(self.config.refresh_interval))
        self._refresh_task = safe_ensure_future(self._refresh_state())

    async def _refresh_state(self):
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

    def _private_ws_status(self) -> str:
        try:
            user_stream_tracker = getattr(self.connectors[self.config.exchange], "_user_stream_tracker", None)
            if user_stream_tracker is None:
                return "DISCONNECTED"
            last_recv = user_stream_tracker.data_source.last_recv_time
            return "CONNECTED" if last_recv > 0 else "DISCONNECTED"
        except Exception:
            return "UNKNOWN"

    def _rest_status(self, last_ok_ts: float) -> str:
        if last_ok_ts <= 0:
            return "NO_DATA"
        age = self.current_timestamp - last_ok_ts
        stale_threshold = max(5, int(self.config.refresh_interval) * 3)
        return f"OK ({int(age)}s ago)" if age <= stale_threshold else f"STALE ({int(age)}s ago)"

    def format_status(self) -> str:
        lines: List[str] = []
        connector = self.connectors[self.config.exchange]

        lines.append("")
        lines.append("Exchange Test")
        lines.append(f"Connector: {self.config.exchange}")
        lines.append(f"Pair: {self.config.trading_pair}")
        lines.append(f"Connector ready: {connector.ready}")
        lines.append(f"Refresh interval: {self.config.refresh_interval}s")

        lines.append("")
        lines.append("Transport")
        lines.append(f"Public WS: {self._public_ws_status()}")
        lines.append(f"Public REST: {self._rest_status(self._public_rest_last_ok_ts)}")
        lines.append(f"Private WS: {self._private_ws_status()}")
        lines.append(f"Private REST: {self._rest_status(self._private_rest_last_ok_ts)}")

        if self._last_public_error:
            lines.append(f"Last public error: {self._last_public_error}")
        if self._last_private_error:
            lines.append(f"Last private error: {self._last_private_error}")

        if self.config.show_public_prices:
            lines.append("")
            lines.append("Public: Prices")
            mid_price = connector.get_mid_price(self.config.trading_pair)
            bid = connector.get_price(self.config.trading_pair, is_buy=False)
            ask = connector.get_price(self.config.trading_pair, is_buy=True)
            lines.append(f"REST last traded: {self._last_public_rest_price}")
            lines.append(f"Mid: {mid_price} | Bid: {bid} | Ask: {ask}")

        if self.config.show_public_trades:
            lines.append("")
            lines.append("Public: Trades")
            ob = connector.order_book_tracker.order_books.get(self.config.trading_pair)
            if ob is None:
                lines.append("Order book not initialized for pair yet.")
            else:
                lines.append(f"Last trade price (from order book): {ob.last_trade_price}")

        if self.config.show_public_order_book:
            lines.append("")
            lines.append("Public: Order Book")
            ob = connector.order_book_tracker.order_books.get(self.config.trading_pair)
            if ob is None:
                lines.append("Order book not initialized for pair yet.")
            else:
                bids_df, asks_df = ob.snapshot
                depth = max(1, int(self.config.order_book_depth))
                top_bids = bids_df.head(depth)
                top_asks = asks_df.head(depth)
                lines.append("Top bids:")
                for _, row in top_bids.iterrows():
                    lines.append(f"  {row['price']} x {row['amount']}")
                lines.append("Top asks:")
                for _, row in top_asks.iterrows():
                    lines.append(f"  {row['price']} x {row['amount']}")

        if self.config.show_private_open_orders:
            lines.append("")
            lines.append("Private: Open Orders")
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
            lines.append("Private: Order History (local cache)")
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
            lines.append("Private: Balance")
            balances = connector.get_all_balances()
            if len(balances) == 0:
                lines.append("No balances loaded.")
            else:
                for asset, total in sorted(balances.items()):
                    available = connector.get_available_balance(asset)
                    lines.append(f"  {asset}: total={Decimal(str(total))} available={Decimal(str(available))}")

        return "\n".join(lines)
