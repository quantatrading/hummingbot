import asyncio
from unittest.mock import AsyncMock, patch

from bidict import bidict

from hummingbot.connector.exchange.cryptocom.cryptocom_api_order_book_data_source import CryptocomAPIOrderBookDataSource
from hummingbot.connector.exchange.cryptocom.cryptocom_exchange import CryptocomExchange
from test.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase


class CryptocomAPIOrderBookDataSourceTests(IsolatedAsyncioWrapperTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.trading_pair = "BTC-USD"
        self.exchange_symbol = "BTC_USD"
        self.connector = CryptocomExchange(
            cryptocom_api_key="",
            cryptocom_api_secret="",
            trading_pairs=[self.trading_pair],
            trading_required=False,
        )
        self.connector._set_trading_pair_symbol_map(bidict({self.exchange_symbol: self.trading_pair}))
        self.data_source = CryptocomAPIOrderBookDataSource(
            trading_pairs=[self.trading_pair],
            connector=self.connector,
            api_factory=self.connector._web_assistants_factory,
        )

    @patch("hummingbot.connector.exchange.cryptocom.cryptocom_api_order_book_data_source.time.time", return_value=1700000000.0)
    async def test_normalized_trade_payload_clamps_stale_timestamps_to_now(self, _: AsyncMock):
        normalized = self.data_source._normalized_trade_payload(
            {"d": "123", "p": "70000.1", "q": "0.01", "s": "BUY", "t": 1}
        )

        self.assertEqual("123", normalized["trade_id"])
        self.assertEqual("70000.1", normalized["price"])
        self.assertEqual("0.01", normalized["amount"])
        self.assertEqual("buy", normalized["trade_type"])
        self.assertEqual(1700000000.0, normalized["timestamp"])

    async def test_parse_trade_message_filters_invalid_payloads(self):
        self.connector.trading_pair_associated_to_exchange_symbol = AsyncMock(return_value=self.trading_pair)
        output_queue = asyncio.Queue()
        raw_message = {
            "result": {
                "channel": f"trade.{self.exchange_symbol}",
                "data": [
                    {"d": "bad-1", "p": "NaN", "q": "0.01", "s": "BUY", "t": 1700000000000},
                    {"d": "good-1", "p": "70123.45", "q": "0.02", "s": "SELL", "t": 1700000000000},
                ],
            }
        }

        await self.data_source._parse_trade_message(raw_message=raw_message, message_queue=output_queue)

        self.assertEqual(1, output_queue.qsize())
        self.assertEqual(1, self.data_source.invalid_trade_payloads_count())
        recent = self.data_source.get_recent_parsed_trades(self.trading_pair, limit=10)
        self.assertEqual(1, len(recent))
        self.assertEqual("good-1", recent[0]["trade_id"])
        self.assertEqual("70123.45", recent[0]["price"])
        self.assertEqual("0.02", recent[0]["amount"])
        self.assertEqual("sell", recent[0]["trade_type"])

