import asyncio
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.cryptocom import cryptocom_constants as CONSTANTS, cryptocom_web_utils as web_utils
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.order_book_message import OrderBookMessage, OrderBookMessageType
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.cryptocom.cryptocom_exchange import CryptocomExchange


class CryptocomAPIOrderBookDataSource(OrderBookTrackerDataSource):
    HEARTBEAT_TIME_INTERVAL = 30.0
    WS_BOOK_DEPTH = 50

    _logger: Optional[HummingbotLogger] = None

    def __init__(
        self,
        trading_pairs: List[str],
        connector: "CryptocomExchange",
        api_factory: WebAssistantsFactory,
        domain: str = CONSTANTS.DEFAULT_DOMAIN,
    ):
        super().__init__(trading_pairs)
        self._connector = connector
        self._trade_messages_queue_key = CONSTANTS.TRADE_EVENT_TYPE
        self._diff_messages_queue_key = CONSTANTS.DIFF_EVENT_TYPE
        self._domain = domain
        self._api_factory = api_factory
        self._last_book_update_id: Dict[str, int] = {}
        self._recent_parsed_trades: Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))

    async def get_last_traded_prices(self, trading_pairs: List[str], domain: Optional[str] = None) -> Dict[str, float]:
        return await self._connector.get_last_traded_prices(trading_pairs=trading_pairs)

    async def listen_for_order_book_diffs(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        """
        Crypto.com book channel behaves as repeated full book payloads.
        Process them through snapshot pipeline instead of diff pipeline.
        """
        pass

    async def listen_for_order_book_snapshots(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        message_queue = self._message_queue[self._diff_messages_queue_key]
        while True:
            try:
                try:
                    snapshot_event = await asyncio.wait_for(message_queue.get(), timeout=30.0)
                    await self._parse_order_book_snapshot_message(raw_message=snapshot_event, message_queue=output)
                except asyncio.TimeoutError:
                    # Fallback to REST snapshot if stream stalls.
                    await self._request_order_book_snapshots(output=output)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("Unexpected error when processing Crypto.com order book snapshots from exchange")
                await self._sleep(1.0)

    async def _request_order_book_snapshot(self, trading_pair: str) -> Dict[str, Any]:
        params = {
            "instrument_name": await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair),
            "depth": 150,
        }

        rest_assistant = await self._api_factory.get_rest_assistant()
        data = await rest_assistant.execute_request(
            url=web_utils.public_rest_url(path_url=CONSTANTS.SNAPSHOT_PATH_URL, domain=self._domain),
            params=params,
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.SNAPSHOT_PATH_URL,
        )
        if data.get("code", 0) != 0:
            raise IOError(f"Error requesting Crypto.com order book snapshot: {data}")

        return data

    async def _subscribe_channels(self, ws: WSAssistant):
        try:
            channels = []
            for trading_pair in self._trading_pairs:
                symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
                channels.append(f"trade.{symbol}")
                channels.extend(self._book_channels_for_symbol(symbol))

            payload = {
                "id": int(time.time() * 1e3),
                "method": "subscribe",
                "params": {
                    "channels": channels,
                    "book_subscription_type": "SNAPSHOT_AND_UPDATE",
                    "book_update_frequency": 100,
                },
            }
            await ws.send(WSJSONRequest(payload=payload))

            self.logger().info("Subscribed to Crypto.com public order book and trade channels...")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().error(
                "Unexpected error occurred subscribing to order book and trade streams...",
                exc_info=True,
            )
            raise

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws = await self._api_factory.get_ws_assistant()
        await ws.connect(ws_url=CONSTANTS.WSS_PUBLIC_URL, ping_timeout=CONSTANTS.WS_HEARTBEAT_TIME_INTERVAL)
        return ws

    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:
        snapshot_response = await self._request_order_book_snapshot(trading_pair)
        data = snapshot_response.get("result", {}).get("data", [])
        snapshot_data = data[0] if len(data) > 0 else {}

        snapshot_msg = self.snapshot_message_from_exchange(
            snapshot_data,
            metadata={"trading_pair": trading_pair},
        )
        self._last_book_update_id[trading_pair] = int(snapshot_msg.update_id)
        return snapshot_msg

    def snapshot_message_from_exchange(
        self,
        msg: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OrderBookMessage:
        payload = dict(msg)
        if metadata:
            payload.update(metadata)

        update_id = self._extract_book_update_id(payload)
        bids = self._normalize_book_entries(payload.get("bids") or payload.get("b") or [])
        asks = self._normalize_book_entries(payload.get("asks") or payload.get("a") or [])

        return OrderBookMessage(
            OrderBookMessageType.SNAPSHOT,
            {
                "trading_pair": payload["trading_pair"],
                "update_id": update_id,
                "bids": bids,
                "asks": asks,
            },
            timestamp=update_id * 1e-3,
        )

    def diff_message_from_exchange(
        self,
        msg: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OrderBookMessage:
        payload = dict(msg)
        if metadata:
            payload.update(metadata)

        trading_pair = payload["trading_pair"]
        update_id = self._next_monotonic_book_update_id(trading_pair=trading_pair, payload=payload)
        bids = self._normalize_book_entries(payload.get("bids") or payload.get("b") or [])
        asks = self._normalize_book_entries(payload.get("asks") or payload.get("a") or [])

        return OrderBookMessage(
            OrderBookMessageType.DIFF,
            {
                "trading_pair": trading_pair,
                "update_id": update_id,
                "bids": bids,
                "asks": asks,
            },
            timestamp=update_id * 1e-3,
        )

    def _extract_book_update_id(self, payload: Dict[str, Any]) -> int:
        update_id = int(
            payload.get("u")
            or payload.get("update_id")
            or payload.get("tt")
            or payload.get("t")
            or int(time.time() * 1e3)
        )
        # Some payload variants expose second-based timestamps; normalize to milliseconds.
        if update_id < 10**12:
            update_id *= 1000
        return update_id

    def _next_monotonic_book_update_id(self, trading_pair: str, payload: Dict[str, Any]) -> int:
        candidate = self._extract_book_update_id(payload)
        last = self._last_book_update_id.get(trading_pair, 0)
        # Keep diff ids ahead of snapshot/update history so tracker does not reject them.
        candidate = max(candidate, int(time.time() * 1e3), last + 1)
        if candidate <= last:
            # Ensure strictly increasing IDs so tracker accepts incremental updates.
            candidate = max(last + 1, int(time.time() * 1e3))
        self._last_book_update_id[trading_pair] = candidate
        return candidate

    def _normalize_book_entries(self, entries: Any) -> List[List[str]]:
        normalized: List[List[str]] = []
        if not isinstance(entries, list):
            return normalized

        for entry in entries:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                normalized.append([str(entry[0]), str(entry[1])])
            elif isinstance(entry, dict):
                price = entry.get("p") or entry.get("price")
                amount = entry.get("q") or entry.get("qty") or entry.get("quantity") or entry.get("amount")
                if price is not None and amount is not None:
                    normalized.append([str(price), str(amount)])
        return normalized

    def _book_channels_for_symbol(self, symbol: str) -> List[str]:
        # Crypto.com has used multiple book channel variants over time/deployments.
        # Subscribe to all common variants and process whichever arrives.
        candidates = [
            f"book.{symbol}",
            f"book.{symbol}.50",
            f"book.{symbol}.150",
            f"book.{symbol}.{self.WS_BOOK_DEPTH}",
        ]
        # Preserve order while removing duplicates.
        return list(dict.fromkeys(candidates))

    def _extract_instrument_name(self, result: Dict[str, Any]) -> str:
        """
        Docs-compliant extractor:
        - `channel` may be `book` / `book.update` (no symbol),
        - symbol may be in `subscription` or `instrument_name`.
        """
        channel = str(result.get("channel", ""))
        subscription = str(result.get("subscription", ""))
        instrument_name = str(result.get("instrument_name", ""))

        # Legacy style: book.BTC_USD.50 / trade.BTC_USD
        if "." in channel:
            parts = channel.split(".")
            if len(parts) >= 2 and parts[0] in {"book", "trade"} and parts[1] not in {"", "update"}:
                return parts[1]

        # Current docs style: channel without symbol + subscription with symbol.
        if subscription.startswith("book.") or subscription.startswith("trade."):
            parts = subscription.split(".")
            if len(parts) >= 2:
                return parts[1]

        if instrument_name:
            return instrument_name

        return ""

    def trade_message_from_exchange(self, msg: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> OrderBookMessage:
        payload = dict(msg)
        if metadata:
            payload.update(metadata)

        side = str(payload.get("s", "")).upper()
        trade_type = float(TradeType.BUY.value) if side == "BUY" else float(TradeType.SELL.value)
        trade_id = payload.get("d") or payload.get("id") or payload.get("t")
        now_ts = time.time()
        timestamp_raw = int(payload.get("t", int(now_ts)))
        timestamp = self._normalize_timestamp(timestamp_raw)
        # Contract expected by strategy-side indicators:
        # epoch seconds, close to local current time.
        if abs(now_ts - timestamp) > 120:
            timestamp = now_ts
        timestamp_ms = int(timestamp * 1e3)

        return OrderBookMessage(
            OrderBookMessageType.TRADE,
            {
                "trading_pair": payload["trading_pair"],
                "trade_type": trade_type,
                "trade_id": trade_id,
                "update_id": timestamp_ms,
                "price": payload.get("p"),
                "amount": payload.get("q"),
            },
            timestamp=timestamp,
        )

    def _normalize_timestamp(self, value: int) -> float:
        """
        Normalize exchange timestamps that may be in seconds, milliseconds,
        microseconds, or nanoseconds.
        """
        if value > 10**17:   # ns
            return value * 1e-9
        if value > 10**14:   # us
            return value * 1e-6
        if value > 10**11:   # ms
            return value * 1e-3
        return float(value)  # s

    def get_recent_parsed_trades(self, trading_pair: str, limit: int = 50) -> List[Dict[str, Any]]:
        trades = list(self._recent_parsed_trades.get(trading_pair, deque()))
        if limit <= 0:
            return trades
        return trades[-limit:]

    async def _parse_trade_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        result = raw_message.get("result", {})
        symbol = self._extract_instrument_name(result)
        if not symbol:
            return
        trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=symbol)

        for trade in result.get("data", []):
            trade_message = self.trade_message_from_exchange(trade, {"trading_pair": trading_pair})
            message_queue.put_nowait(trade_message)
            trade_type = "buy" if trade_message.content["trade_type"] == float(TradeType.BUY.value) else "sell"
            self._recent_parsed_trades[trading_pair].append(
                {
                    "trade_id": str(trade_message.content["trade_id"]),
                    "price": trade_message.content["price"],
                    "amount": trade_message.content["amount"],
                    "trade_type": trade_type,
                    "timestamp": float(trade_message.timestamp),
                }
            )

    async def _parse_order_book_diff_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        # Not used: Crypto.com book stream is handled as snapshots.
        return

    async def _parse_order_book_snapshot_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        result = raw_message.get("result", {})
        symbol = self._extract_instrument_name(result)
        if not symbol:
            return
        trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=symbol)

        data = result.get("data", [])
        if len(data) == 0:
            return

        for item in data:
            # Update-mode entries are nested under `update`; snapshot-mode entries are top-level.
            payload = dict(item.get("update", item))
            if "tt" not in payload and "tt" in item:
                payload["tt"] = item["tt"]
            if "t" not in payload and "t" in item:
                payload["t"] = item["t"]

            has_bids = ("bids" in payload or "b" in payload)
            has_asks = ("asks" in payload or "a" in payload)
            # Partial book updates must be applied as DIFF, otherwise snapshot restore can wipe one side.
            if item.get("update") is not None or not (has_bids and has_asks):
                book_message = self.diff_message_from_exchange(payload, {"trading_pair": trading_pair})
            else:
                book_message = self.snapshot_message_from_exchange(payload, {"trading_pair": trading_pair})

            self._last_book_update_id[trading_pair] = int(book_message.update_id)
            message_queue.put_nowait(book_message)

    def _channel_originating_message(self, event_message: Dict[str, Any]) -> str:
        result = event_message.get("result", {})
        channel = str(result.get("channel", ""))
        subscription = str(result.get("subscription", ""))
        if channel in {"book", "book.update"} or channel.startswith("book.") or subscription.startswith("book."):
            return self._diff_messages_queue_key
        if channel in {"trade", "trade.update"} or channel.startswith("trade.") or subscription.startswith("trade."):
            return self._trade_messages_queue_key
        return ""

    async def _process_message_for_unknown_channel(self, event_message: Dict[str, Any], websocket_assistant: WSAssistant):
        if event_message.get("method") == "public/heartbeat":
            await websocket_assistant.send(WSJSONRequest(payload={"id": event_message.get("id"), "method": "public/respond-heartbeat"}))
            return

        if "code" in event_message and int(event_message.get("code", 0)) != 0:
            self.logger().warning(f"Crypto.com WS non-zero code message: {event_message}")
            return

        if event_message.get("method") in {"subscribe", "unsubscribe"}:
            self.logger().debug(f"Crypto.com WS subscription message: {event_message}")

    async def subscribe_to_trading_pair(self, trading_pair: str) -> bool:
        if self._ws_assistant is None:
            self.logger().warning(f"Cannot subscribe to {trading_pair}: WebSocket not connected")
            return False

        try:
            symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
            payload = {
                "id": int(time.time() * 1e3),
                "method": "subscribe",
                "params": {
                    "channels": [f"trade.{symbol}", *self._book_channels_for_symbol(symbol)]
                },
            }
            await self._ws_assistant.send(WSJSONRequest(payload=payload))
            self.add_trading_pair(trading_pair)
            self.logger().info(f"Subscribed to {trading_pair} order book and trade channels")
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().exception(f"Unexpected error subscribing to {trading_pair} channels")
            return False

    async def unsubscribe_from_trading_pair(self, trading_pair: str) -> bool:
        if self._ws_assistant is None:
            self.logger().warning(f"Cannot unsubscribe from {trading_pair}: WebSocket not connected")
            return False

        try:
            symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
            payload = {
                "id": int(time.time() * 1e3),
                "method": "unsubscribe",
                "params": {
                    "channels": [f"trade.{symbol}", *self._book_channels_for_symbol(symbol)]
                },
            }
            await self._ws_assistant.send(WSJSONRequest(payload=payload))
            self.remove_trading_pair(trading_pair)
            self.logger().info(f"Unsubscribed from {trading_pair} order book and trade channels")
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().exception(f"Unexpected error unsubscribing from {trading_pair} channels")
            return False
