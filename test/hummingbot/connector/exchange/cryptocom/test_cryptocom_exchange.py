from unittest.mock import AsyncMock

from hummingbot.connector.exchange.cryptocom.cryptocom_exchange import CryptocomExchange
from hummingbot.core.data_type.in_flight_order import OrderState
from test.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase


class CryptocomExchangeTests(IsolatedAsyncioWrapperTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.trading_pair = "BTC-USD"
        self.exchange_symbol = "BTC_USD"
        self.exchange = CryptocomExchange(
            cryptocom_api_key="",
            cryptocom_api_secret="",
            trading_pairs=[self.trading_pair],
            trading_required=False,
        )
        self.exchange.exchange_symbol_associated_to_pair = AsyncMock(return_value=self.exchange_symbol)

    async def test_place_cancel_accepts_ack_without_status(self):
        tracked_order = AsyncMock()
        tracked_order.trading_pair = self.trading_pair
        tracked_order.get_exchange_order_id = AsyncMock(return_value="10001")
        self.exchange._api_private_post = AsyncMock(return_value={"order_id": "10001"})

        canceled = await self.exchange._place_cancel(order_id="cid-1", tracked_order=tracked_order)

        self.assertTrue(canceled)

    async def test_place_cancel_accepts_empty_success_response(self):
        tracked_order = AsyncMock()
        tracked_order.trading_pair = self.trading_pair
        tracked_order.get_exchange_order_id = AsyncMock(return_value="10002")
        self.exchange._api_private_post = AsyncMock(return_value={})

        canceled = await self.exchange._place_cancel(order_id="cid-2", tracked_order=tracked_order)

        self.assertTrue(canceled)

    async def test_request_order_status_parses_order_info_payload(self):
        tracked_order = AsyncMock()
        tracked_order.trading_pair = self.trading_pair
        tracked_order.client_order_id = "cid-3"
        tracked_order.exchange_order_id = "10003"
        tracked_order.current_state = OrderState.OPEN
        tracked_order.get_exchange_order_id = AsyncMock(return_value="10003")
        self.exchange._api_private_post = AsyncMock(
            return_value={
                "order_info": {
                    "order_id": "10003",
                    "status": "CANCELED",
                    "update_time": 1700000000000,
                }
            }
        )

        order_update = await self.exchange._request_order_status(tracked_order=tracked_order)

        self.assertEqual("cid-3", order_update.client_order_id)
        self.assertEqual("10003", order_update.exchange_order_id)
        self.assertEqual(OrderState.CANCELED, order_update.new_state)
        self.assertEqual(1700000000.0, order_update.update_timestamp)

    async def test_request_order_status_parses_data_list_payload(self):
        tracked_order = AsyncMock()
        tracked_order.trading_pair = self.trading_pair
        tracked_order.client_order_id = "cid-4"
        tracked_order.exchange_order_id = "10004"
        tracked_order.current_state = OrderState.OPEN
        tracked_order.get_exchange_order_id = AsyncMock(return_value="10004")
        self.exchange._api_private_post = AsyncMock(
            return_value={
                "data": [
                    {
                        "order_id": "10004",
                        "order_status": "PENDING_CANCEL",
                        "update_time": 1700000000000,
                    }
                ]
            }
        )

        order_update = await self.exchange._request_order_status(tracked_order=tracked_order)

        self.assertEqual("cid-4", order_update.client_order_id)
        self.assertEqual("10004", order_update.exchange_order_id)
        self.assertEqual(OrderState.PENDING_CANCEL, order_update.new_state)
        self.assertEqual(1700000000.0, order_update.update_timestamp)

    async def test_request_order_status_keeps_current_state_for_unknown_status(self):
        tracked_order = AsyncMock()
        tracked_order.trading_pair = self.trading_pair
        tracked_order.client_order_id = "cid-5"
        tracked_order.exchange_order_id = "10005"
        tracked_order.current_state = OrderState.OPEN
        tracked_order.get_exchange_order_id = AsyncMock(return_value="10005")
        self.exchange._api_private_post = AsyncMock(
            return_value={
                "data": [
                    {
                        "order_id": "10005",
                        "status": "UNKNOWN_CUSTOM_STATUS",
                        "update_time": 1700000000000,
                    }
                ]
            }
        )

        order_update = await self.exchange._request_order_status(tracked_order=tracked_order)

        self.assertEqual(OrderState.OPEN, order_update.new_state)

