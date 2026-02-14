import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.cryptocom import cryptocom_constants as CONSTANTS
from hummingbot.connector.exchange.cryptocom.cryptocom_auth import CryptocomAuth
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.cryptocom.cryptocom_exchange import CryptocomExchange


class CryptocomAPIUserStreamDataSource(UserStreamTrackerDataSource):
    _logger: Optional[HummingbotLogger] = None

    def __init__(
        self,
        auth: CryptocomAuth,
        trading_pairs: List[str],
        connector: "CryptocomExchange",
        api_factory: WebAssistantsFactory,
        domain: str = CONSTANTS.DEFAULT_DOMAIN,
    ):
        super().__init__()
        self._auth = auth
        self._domain = domain
        self._api_factory = api_factory
        self._connector = connector
        self._trading_pairs = trading_pairs

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws = await self._api_factory.get_ws_assistant()
        await ws.connect(ws_url=CONSTANTS.WSS_PRIVATE_URL, ping_timeout=CONSTANTS.WS_HEARTBEAT_TIME_INTERVAL)
        return ws

    async def _authenticate_client(self, websocket_assistant: WSAssistant):
        auth_payload = self._auth.get_ws_auth_payload()
        await websocket_assistant.send(WSJSONRequest(payload=auth_payload))
        response = await websocket_assistant.receive()
        data = response.data
        if not self._is_success_response(data):
            raise IOError(f"Private websocket authentication failed ({data})")

    async def _subscribe_channels(self, websocket_assistant: WSAssistant):
        try:
            await self._authenticate_client(websocket_assistant=websocket_assistant)

            payload = {
                "id": int(time.time() * 1e3),
                "method": "subscribe",
                "params": {
                    "channels": [
                        "user.order",
                        "user.trade",
                        "user.balance",
                    ]
                },
            }
            await websocket_assistant.send(WSJSONRequest(payload=payload))
            response = await websocket_assistant.receive()
            data = response.data
            if not self._is_success_response(data):
                raise IOError(f"Private websocket channel subscription failed ({data})")
            self.logger().info("Subscribed to Crypto.com private user channels...")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().error("Unexpected error occurred subscribing to user streams...", exc_info=True)
            raise

    async def _process_websocket_messages(self, websocket_assistant: WSAssistant, queue: asyncio.Queue):
        async for ws_response in websocket_assistant.iter_messages():
            data = ws_response.data
            if not isinstance(data, dict):
                continue
            # Fail fast if exchange reports auth/subscribe issues after connection is established.
            if str(data.get("method", "")).endswith(("auth", "subscribe")) and not self._is_success_response(data):
                raise IOError(f"Private websocket error response ({data})")
            if data.get("method") == "public/heartbeat":
                pong_request = WSJSONRequest(payload={"id": data.get("id"), "method": "public/respond-heartbeat"})
                await websocket_assistant.send(request=pong_request)
            else:
                queue.put_nowait(data)

    def _is_success_response(self, data: Dict[str, Any]) -> bool:
        # Crypto.com responses include `code == 0` on success.
        # Some responses may use a success status inside result payload.
        if not isinstance(data, dict):
            return False
        code = data.get("code")
        if code is not None:
            try:
                return int(code) == 0
            except Exception:
                return False
        status = str((data.get("result") or {}).get("status", "")).lower()
        return status in {"ok", "success"}
