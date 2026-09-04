import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from app.dhan_broker import (
    DhanInstrumentRegistry,
    DhanFeedService,
    MarketFeed,
    broker_error_message,
    ensure_no_broker_error,
    normalize_dhan_candles,
    normalize_dhan_holdings,
    normalize_dhan_positions,
    order_id_from_response,
    resample_intraday_candles,
)
from app.memory_store import InMemoryStore
from app.trade_engine import OrderExecution, Position, TradeEngine, _round_dhan_price


class DhanBrokerTests(unittest.TestCase):
    def test_order_id_is_normalized(self) -> None:
        self.assertEqual(
            order_id_from_response({"status": "success", "data": {"orderId": "123"}}),
            "123",
        )

    def test_nested_broker_error_message_is_extracted(self) -> None:
        response = {
            "data": {
                "orderStatus": "REJECTED",
                "details": {"omsErrorDescription": "Rate Not Within Ckt Limit 605.35 To 669.05"},
            }
        }

        self.assertIn("Rate Not Within Ckt Limit", broker_error_message(response))
        with self.assertRaisesRegex(RuntimeError, "DHAN_PLACE_ORDER_REJECTED"):
            ensure_no_broker_error(response, "DHAN_PLACE_ORDER_REJECTED")

    def test_positions_are_converted_to_engine_shape(self) -> None:
        result = normalize_dhan_positions(
            {
                "data": [
                    {
                        "tradingSymbol": "SBIN",
                        "securityId": "3045",
                        "netQty": 10,
                        "costPrice": 812.5,
                        "productType": "INTRADAY",
                        "realizedProfit": 20,
                        "unrealizedProfit": 30,
                    }
                ]
            }
        )
        self.assertEqual(result["net"][0]["tradingsymbol"], "SBIN")
        self.assertEqual(result["net"][0]["quantity"], 10)
        self.assertEqual(result["net"][0]["product"], "MIS")
        self.assertEqual(result["net"][0]["pnl"], 50)

    def test_holdings_are_converted_to_cnc_shape(self) -> None:
        result = normalize_dhan_holdings(
            {
                "data": [
                    {
                        "tradingSymbol": "SBIN-EQ",
                        "securityId": "3045",
                        "availableQty": 3,
                        "avgCostPrice": 812.5,
                    }
                ]
            }
        )
        self.assertEqual(result[0]["tradingsymbol"], "SBIN")
        self.assertEqual(result[0]["quantity"], 3)
        self.assertEqual(result[0]["product"], "CNC")
        self.assertEqual(result[0]["average_price"], 812.5)

    def test_open_candle_is_excluded(self) -> None:
        result = normalize_dhan_candles(
            {
                "data": {
                    "open": [100],
                    "high": [102],
                    "low": [99],
                    "close": [101],
                    "volume": [1000],
                    "timestamp": [1],
                }
            },
            5,
        )
        self.assertEqual(len(result), 1)

    def test_resamples_one_minute_candles_to_three_minute(self) -> None:
        from datetime import datetime, timedelta
        import pytz

        ist = pytz.timezone("Asia/Kolkata")
        start = ist.localize(datetime(2026, 6, 19, 9, 15))
        candles = [
            {
                "date": start + timedelta(minutes=i),
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100.5 + i,
                "volume": 10 + i,
            }
            for i in range(6)
        ]

        result = resample_intraday_candles(candles, 3)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"], start)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["high"], 103)
        self.assertEqual(result[0]["low"], 99)
        self.assertEqual(result[0]["close"], 102.5)
        self.assertEqual(result[0]["volume"], 33)

    def test_dhan_registry_selects_nearest_expiry_atm_index_option(self) -> None:
        registry = DhanInstrumentRegistry()
        registry._master_frame = pd.DataFrame(
            [
                {
                    "SEM_TRADING_SYMBOL": "BANKNIFTY26JUN54000CE",
                    "SEM_SEGMENT": "OPTIDX",
                    "SEM_EXPIRY_DATE": "2026-06-26",
                    "SEM_OPTION_TYPE": "CE",
                    "SEM_STRIKE_PRICE": 54000,
                    "SEM_SMST_SECURITY_ID": "9001",
                },
                {
                    "SEM_TRADING_SYMBOL": "BANKNIFTY26JUN54100CE",
                    "SEM_SEGMENT": "OPTIDX",
                    "SEM_EXPIRY_DATE": "2026-06-26",
                    "SEM_OPTION_TYPE": "CE",
                    "SEM_STRIKE_PRICE": 54100,
                    "SEM_SMST_SECURITY_ID": "9002",
                },
                {
                    "SEM_TRADING_SYMBOL": "BANKNIFTY26JUN54000PE",
                    "SEM_SEGMENT": "OPTIDX",
                    "SEM_EXPIRY_DATE": "2026-06-26",
                    "SEM_OPTION_TYPE": "PE",
                    "SEM_STRIKE_PRICE": 54000,
                    "SEM_SMST_SECURITY_ID": "9003",
                },
            ]
        )

        async def run():
            return await registry.atm_index_option("BANKNIFTY", "BUY", 54024, today=pd.Timestamp("2026-06-19").date())

        result = __import__("asyncio").run(run())

        self.assertEqual(result["security_id"], "9001")
        self.assertEqual(result["option_type"], "CE")
        self.assertEqual(registry.symbol("9001"), "BANKNIFTY26JUN54000CE")
        self.assertEqual(registry.feed_segment("9001"), MarketFeed.NSE_FNO)

    def test_dhan_registry_reads_tick_size_from_master(self) -> None:
        registry = DhanInstrumentRegistry()
        registry._master_frame = pd.DataFrame(
            [
                {
                    "SEM_EXM_EXCH_ID": "NSE",
                    "SEM_SEGMENT": "E",
                    "SEM_SERIES": "EQ",
                    "SEM_INSTRUMENT_NAME": "EQUITY",
                    "SEM_TRADING_SYMBOL": "ABREL",
                    "SEM_SMST_SECURITY_ID": "625",
                    "SEM_TICK_SIZE": 10,
                },
                {
                    "SEM_EXM_EXCH_ID": "NSE",
                    "SEM_SEGMENT": "E",
                    "SEM_SERIES": "EQ",
                    "SEM_INSTRUMENT_NAME": "EQUITY",
                    "SEM_TRADING_SYMBOL": "GODREJCP",
                    "SEM_SMST_SECURITY_ID": "10099",
                    "SEM_TICK_SIZE": 5,
                },
            ]
        )

        __import__("asyncio").run(registry.ensure_loaded())

        self.assertEqual(__import__("asyncio").run(registry.security_id("ABREL")), "625")
        self.assertAlmostEqual(registry.tick_size("625"), 0.10)
        self.assertAlmostEqual(registry.tick_size("10099"), 0.05)

    def test_dhan_order_price_rounds_to_tick_size(self) -> None:
        self.assertEqual(_round_dhan_price(351.57, "SELL", 0.10), 351.50)
        self.assertEqual(_round_dhan_price(351.57, "BUY", 0.10), 351.60)
        self.assertEqual(_round_dhan_price(351.57, "SELL", 0.05), 351.55)
        self.assertEqual(_round_dhan_price(351.57, "BUY", 0.05), 351.60)


class DhanTradeEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_dhan_feed_stop_closes_sdk_event_loop(self) -> None:
        class FakeFeed:
            def __init__(self) -> None:
                self.loop = asyncio.new_event_loop()
                self.closed = False

            def close_connection(self) -> None:
                self.closed = True

        class FakeThread:
            def is_alive(self) -> bool:
                return False

        states = []
        feed = FakeFeed()
        service = DhanFeedService.__new__(DhanFeedService)
        service.feed = feed
        service.feed_thread = FakeThread()
        service.order_task = None
        service.order_update = None
        service.on_state = states.append

        await service.stop()

        self.assertTrue(feed.closed)
        self.assertTrue(feed.loop.is_closed())
        self.assertIsNone(service.feed)
        self.assertIsNone(service.feed_thread)
        self.assertEqual(states, [False])

    async def test_dhan_market_order_fallback_is_opt_in(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(
            NSE="NSE_EQ",
            BUY="BUY",
            SELL="SELL",
            MARKET="MARKET",
            INTRA="INTRADAY",
            CNC="CNC",
        )
        engine.order_worker.submit = AsyncMock(
            return_value={"status": "success", "data": {"orderId": "DHAN-1"}}
        )
        engine._fetch_ltp = AsyncMock(return_value=0.0)
        engine.market_data_worker.submit = AsyncMock(return_value={})

        with patch.dict("os.environ", {"APP_TESTING": "", "APP_ENV": "production"}):
            with patch("app.trade_engine.DHAN_INSTRUMENTS.security_id", AsyncMock(return_value="3045")):
                with self.assertRaisesRegex(RuntimeError, "DHAN_EXECUTABLE_PRICE_UNAVAILABLE"):
                    await engine._place_order("SBIN", "BUY", 10, "MIS")
        engine.order_worker.submit.assert_not_awaited()

        with patch("app.trade_engine.DHAN_INSTRUMENTS.security_id", AsyncMock(return_value="3045")):
            order_id = await engine._place_order("SBIN", "BUY", 10, "MIS")
        self.assertEqual(order_id, "DHAN-1")

        engine.order_worker.submit.reset_mock()
        with patch.dict("os.environ", {"APP_TESTING": "", "APP_ENV": "production", "ALLOW_DHAN_MARKET_FALLBACK": "1"}):
            with patch("app.trade_engine.DHAN_INSTRUMENTS.security_id", AsyncMock(return_value="3045")):
                engine._fetch_ltp.return_value = 100.0
                engine.market_data_worker.submit = AsyncMock(return_value={})
                engine.order_worker.submit.return_value = {"status": "success", "data": {"orderId": "DHAN-2"}}
                order_id = await engine._place_order("SBIN", "BUY", 10, "MIS")

        self.assertEqual(order_id, "DHAN-2")
        kwargs = engine.order_worker.submit.await_args.kwargs
        self.assertEqual(kwargs["security_id"], "3045")
        self.assertEqual(kwargs["exchange_segment"], "NSE_EQ")
        self.assertEqual(kwargs["product_type"], "INTRADAY")
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_buy_uses_sell_depth_for_aggressive_limit(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(
            NSE="NSE_EQ",
            BUY="BUY",
            SELL="SELL",
            MARKET="MARKET",
            LIMIT="LIMIT",
            INTRA="INTRADAY",
            CNC="CNC",
        )
        engine.dhan.place_order = MagicMock()
        engine.dhan.quote_data = MagicMock()

        placed_kwargs = {}

        async def fake_order_submit(fn, *args, **kwargs):
            self.assertIs(fn, engine.dhan.place_order)
            placed_kwargs.update(kwargs)
            return {"status": "success", "data": {"orderId": "DHAN-LIMIT"}}

        async def fake_market_submit(fn, *args, **kwargs):
            self.assertIs(fn, engine.dhan.quote_data)
            return {
                "data": {
                    "NSE_EQ": {
                        "3045": {
                            "last_price": 100.0,
                            "depth": {
                                "sell": [
                                    {"price": 100.5, "quantity": 5, "orders": 1},
                                    {"price": 101.0, "quantity": 10, "orders": 2},
                                ],
                                "buy": [
                                    {"price": 99.9, "quantity": 20, "orders": 3},
                                ],
                            },
                        }
                    }
                }
            }

        engine.order_worker.submit = fake_order_submit
        engine.market_data_worker.submit = fake_market_submit

        with patch("app.trade_engine.DHAN_INSTRUMENTS.security_id", AsyncMock(return_value="3045")):
            order_id = await engine._place_order(
                "SBIN",
                "BUY",
                8,
                "MIS",
                {"order_limit_buffer_pct": 0.10, "order_limit_extra_ticks": 1},
            )

        self.assertEqual(order_id, "DHAN-LIMIT")
        self.assertEqual(placed_kwargs["order_type"], "LIMIT")
        self.assertEqual(placed_kwargs["price"], 101.2)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_sell_order_uses_instrument_tick_size(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(
            NSE="NSE_EQ",
            BUY="BUY",
            SELL="SELL",
            MARKET="MARKET",
            LIMIT="LIMIT",
            INTRA="INTRADAY",
            CNC="CNC",
        )
        engine.dhan.place_order = MagicMock()
        engine.dhan.quote_data = MagicMock()

        placed_kwargs = {}

        async def fake_order_submit(fn, *args, **kwargs):
            self.assertIs(fn, engine.dhan.place_order)
            placed_kwargs.update(kwargs)
            return {"status": "success", "data": {"orderId": "DHAN-TICK"}}

        async def fake_market_submit(fn, *args, **kwargs):
            self.assertIs(fn, engine.dhan.quote_data)
            return {
                "data": {
                    "NSE_EQ": {
                        "625": {
                            "last_price": 351.57,
                            "depth": {
                                "buy": [{"price": 351.57, "quantity": 10, "orders": 1}],
                                "sell": [{"price": 351.65, "quantity": 10, "orders": 1}],
                            },
                        }
                    }
                }
            }

        engine.order_worker.submit = fake_order_submit
        engine.market_data_worker.submit = fake_market_submit

        with patch("app.trade_engine.DHAN_INSTRUMENTS.security_id", AsyncMock(return_value="625")), patch(
            "app.trade_engine.DHAN_INSTRUMENTS.tick_size",
            return_value=0.10,
        ):
            order_id = await engine._place_order(
                "ABREL",
                "SELL",
                1,
                "MIS",
                {"order_limit_buffer_pct": 0, "order_limit_extra_ticks": 0},
            )

        self.assertEqual(order_id, "DHAN-TICK")
        self.assertEqual(placed_kwargs["order_type"], "LIMIT")
        self.assertEqual(placed_kwargs["price"], 351.50)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_pending_order_is_cancelled_and_retried_until_executed(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(
            NSE="NSE_EQ",
            BUY="BUY",
            SELL="SELL",
            MARKET="MARKET",
            INTRA="INTRADAY",
            CNC="CNC",
        )
        engine.dhan.place_order = MagicMock()
        engine.dhan.cancel_order = MagicMock()
        engine.dhan.get_order_by_id = MagicMock()

        placed: list[str] = []
        cancelled: list[str] = []

        async def fake_order_submit(fn, *args, **kwargs):
            if fn is engine.dhan.place_order:
                order_id = "DHAN-PENDING" if not placed else "DHAN-FILLED"
                placed.append(order_id)
                return {"status": "success", "data": {"orderId": order_id}}
            if fn is engine.dhan.cancel_order:
                cancelled.append(str(args[0]))
                return {"status": "success"}
            raise AssertionError(f"unexpected order worker fn {fn}")

        async def fake_market_submit(fn, *args, **kwargs):
            self.assertIs(fn, engine.dhan.get_order_by_id)
            order_id = str(args[0])
            if order_id == "DHAN-PENDING":
                return {
                    "data": {
                        "orderId": order_id,
                        "orderStatus": "PENDING",
                        "quantity": 10,
                        "remainingQuantity": 10,
                    }
                }
            return {
                "data": {
                    "orderId": order_id,
                    "orderStatus": "TRADED",
                    "quantity": 10,
                    "filledQuantity": 10,
                    "remainingQuantity": 0,
                    "averagePrice": 101.25,
                    "tradingSymbol": "SBIN",
                }
            }

        engine.order_worker.submit = fake_order_submit
        engine.market_data_worker.submit = fake_market_submit

        with patch(
            "app.trade_engine.DHAN_INSTRUMENTS.security_id",
            AsyncMock(return_value="3045"),
        ):
            execution = await engine._place_order_with_execution(
                "SBIN",
                "BUY",
                10,
                "MIS",
                {"order_confirm_timeout_sec": 0.2, "order_pending_retry_count": 1},
            )

        self.assertEqual(execution.order_id, "DHAN-FILLED")
        self.assertEqual(execution.status, "COMPLETE")
        self.assertEqual(execution.avg_price, 101.25)
        self.assertEqual(execution.attempts, 2)
        self.assertEqual(cancelled, ["DHAN-PENDING"])
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_partial_fill_retries_only_remaining_quantity(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(
            NSE="NSE_EQ",
            BUY="BUY",
            SELL="SELL",
            MARKET="MARKET",
            INTRA="INTRADAY",
            CNC="CNC",
        )
        engine.dhan.place_order = MagicMock()
        engine.dhan.cancel_order = MagicMock()
        engine.dhan.get_order_by_id = MagicMock()
        engine.dhan.get_order_list = MagicMock()
        engine.dhan.get_trade_book = MagicMock()
        engine.dhan.get_positions = MagicMock()

        placed_quantities: list[int] = []
        cancelled: list[str] = []

        async def fake_order_submit(fn, *args, **kwargs):
            if fn is engine.dhan.place_order:
                placed_quantities.append(int(kwargs["quantity"]))
                order_id = "DHAN-PARTIAL" if len(placed_quantities) == 1 else "DHAN-FILLED"
                return {"status": "success", "data": {"orderId": order_id}}
            if fn is engine.dhan.cancel_order:
                cancelled.append(str(args[0]))
                return {"status": "success"}
            raise AssertionError(f"unexpected order worker fn {fn}")

        async def fake_market_submit(fn, *args, **kwargs):
            if fn is engine.dhan.get_positions:
                return []
            if fn is engine.dhan.get_order_list:
                return {"data": []}
            if fn is engine.dhan.get_trade_book:
                return {"data": []}
            if fn is engine.dhan.get_order_by_id:
                order_id = str(args[0])
                if order_id == "DHAN-PARTIAL":
                    return {
                        "data": {
                            "orderId": order_id,
                            "orderStatus": "PARTIALLY_TRADED",
                            "quantity": 10,
                            "filledQuantity": 4,
                            "remainingQuantity": 6,
                            "averagePrice": 100.0,
                            "tradingSymbol": "SBIN",
                        }
                    }
                return {
                    "data": {
                        "orderId": order_id,
                        "orderStatus": "TRADED",
                        "quantity": 6,
                        "filledQuantity": 6,
                        "remainingQuantity": 0,
                        "averagePrice": 102.0,
                        "tradingSymbol": "SBIN",
                    }
                }
            raise AssertionError(f"unexpected market worker fn {fn}")

        engine.order_worker.submit = fake_order_submit
        engine.market_data_worker.submit = fake_market_submit

        with patch(
            "app.trade_engine.DHAN_INSTRUMENTS.security_id",
            AsyncMock(return_value="3045"),
        ):
            execution = await engine._place_order_with_execution(
                "SBIN",
                "BUY",
                10,
                "MIS",
                {"order_confirm_timeout_sec": 0.2, "order_pending_retry_count": 1},
            )

        self.assertEqual(placed_quantities, [10, 6])
        self.assertEqual(cancelled, ["DHAN-PARTIAL"])
        self.assertEqual(execution.status, "COMPLETE")
        self.assertEqual(execution.filled_qty, 10)
        self.assertEqual(execution.remaining_qty, 0)
        self.assertAlmostEqual(execution.avg_price, 101.2)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_price_rejection_retries_with_fresh_depth(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(
            NSE="NSE_EQ",
            BUY="BUY",
            SELL="SELL",
            MARKET="MARKET",
            LIMIT="LIMIT",
            INTRA="INTRADAY",
            CNC="CNC",
        )
        engine.dhan.place_order = MagicMock()
        engine.dhan.cancel_order = MagicMock()
        engine.dhan.get_order_by_id = MagicMock()
        engine.dhan.get_order_list = MagicMock()
        engine.dhan.get_trade_book = MagicMock()
        engine.dhan.get_positions = MagicMock()
        engine.dhan.quote_data = MagicMock()

        placed_prices: list[float] = []
        quote_refs = [100.0, 101.0]

        async def fake_order_submit(fn, *args, **kwargs):
            if fn is engine.dhan.place_order:
                placed_prices.append(float(kwargs["price"]))
                order_id = "DHAN-REJECTED" if len(placed_prices) == 1 else "DHAN-FILLED"
                return {"status": "success", "data": {"orderId": order_id}}
            if fn is engine.dhan.cancel_order:
                return {"status": "success"}
            raise AssertionError(f"unexpected order worker fn {fn}")

        async def fake_market_submit(fn, *args, **kwargs):
            if fn is engine.dhan.quote_data:
                ref = quote_refs[min(len(placed_prices), len(quote_refs) - 1)]
                return {
                    "data": {
                        "NSE_EQ": {
                            "3045": {
                                "last_price": ref,
                                "depth": {
                                    "sell": [{"price": ref, "quantity": 100, "orders": 3}],
                                    "buy": [{"price": ref - 0.1, "quantity": 100, "orders": 4}],
                                },
                            }
                        }
                    }
                }
            if fn is engine.dhan.get_positions:
                return []
            if fn is engine.dhan.get_order_list:
                return {"data": []}
            if fn is engine.dhan.get_trade_book:
                return {"data": []}
            if fn is engine.dhan.get_order_by_id:
                order_id = str(args[0])
                if order_id == "DHAN-REJECTED":
                    return {
                        "data": {
                            "orderId": order_id,
                            "orderStatus": "REJECTED",
                            "quantity": 10,
                            "remainingQuantity": 10,
                            "omsErrorDescription": "price protection range breached",
                        }
                    }
                return {
                    "data": {
                        "orderId": order_id,
                        "orderStatus": "TRADED",
                        "quantity": 10,
                        "filledQuantity": 10,
                        "remainingQuantity": 0,
                        "averagePrice": 101.25,
                        "tradingSymbol": "SBIN",
                    }
                }
            raise AssertionError(f"unexpected market worker fn {fn}")

        engine.order_worker.submit = fake_order_submit
        engine.market_data_worker.submit = fake_market_submit

        with patch("app.trade_engine.DHAN_INSTRUMENTS.security_id", AsyncMock(return_value="3045")):
            execution = await engine._place_order_with_execution(
                "SBIN",
                "BUY",
                10,
                "MIS",
                {
                    "order_confirm_timeout_sec": 0.2,
                    "order_pending_retry_count": 1,
                    "order_limit_buffer_pct": 0.10,
                    "order_limit_extra_ticks": 1,
                },
            )

        self.assertEqual(execution.status, "COMPLETE")
        self.assertEqual(execution.attempts, 2)
        self.assertEqual(len(placed_prices), 2)
        self.assertGreater(placed_prices[1], placed_prices[0])
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_trade_book_reconciles_pending_order_fill(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(
            NSE="NSE_EQ",
            BUY="BUY",
            SELL="SELL",
            MARKET="MARKET",
            INTRA="INTRADAY",
            CNC="CNC",
        )
        engine.dhan.place_order = MagicMock()
        engine.dhan.cancel_order = MagicMock()
        engine.dhan.get_order_by_id = MagicMock()
        engine.dhan.get_order_list = MagicMock()
        engine.dhan.get_trade_book = MagicMock()
        engine.dhan.get_positions = MagicMock()

        cancelled: list[str] = []

        async def fake_order_submit(fn, *args, **kwargs):
            if fn is engine.dhan.place_order:
                return {"status": "success", "data": {"orderId": "DHAN-PENDING"}}
            if fn is engine.dhan.cancel_order:
                cancelled.append(str(args[0]))
                return {"status": "success"}
            raise AssertionError(f"unexpected order worker fn {fn}")

        async def fake_market_submit(fn, *args, **kwargs):
            if fn is engine.dhan.get_positions:
                return []
            if fn is engine.dhan.get_order_list:
                return {"data": []}
            if fn is engine.dhan.get_order_by_id:
                return {
                    "data": {
                        "orderId": "DHAN-PENDING",
                        "orderStatus": "PENDING",
                        "quantity": 10,
                        "remainingQuantity": 10,
                        "tradingSymbol": "SBIN",
                    }
                }
            if fn is engine.dhan.get_trade_book:
                return {
                    "data": [
                        {
                            "orderId": "DHAN-PENDING",
                            "tradingSymbol": "SBIN",
                            "tradedQuantity": 7,
                            "tradedPrice": 99.5,
                        }
                    ]
                }
            raise AssertionError(f"unexpected market worker fn {fn}")

        engine.order_worker.submit = fake_order_submit
        engine.market_data_worker.submit = fake_market_submit

        with patch(
            "app.trade_engine.DHAN_INSTRUMENTS.security_id",
            AsyncMock(return_value="3045"),
        ):
            execution = await engine._place_order_with_execution(
                "SBIN",
                "BUY",
                10,
                "MIS",
                {"order_confirm_timeout_sec": 0.2, "order_pending_retry_count": 0},
            )

        self.assertEqual(execution.status, "PARTIAL")
        self.assertEqual(execution.filled_qty, 7)
        self.assertEqual(execution.remaining_qty, 3)
        self.assertEqual(execution.avg_price, 99.5)
        self.assertEqual(cancelled, ["DHAN-PENDING"])
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_partial_exit_keeps_remaining_position_open(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        pos = Position(
            trade_id="T1",
            user_id=1,
            symbol="SBIN",
            alert_name="TEST",
            side="BUY",
            product="MIS",
            qty=10,
            entry_price=100.0,
            status="OPEN",
        )
        engine.positions["SBIN"] = pos
        await store.upsert_position(1, "SBIN", pos.to_public())
        await store.mark_open(1, "SBIN", "T1")
        engine._place_order_with_execution = AsyncMock(
            return_value=OrderExecution(
                order_id="EXIT-1",
                symbol="SBIN",
                side="SELL",
                qty=4,
                status="PARTIAL",
                avg_price=104.0,
                filled_qty=4,
                remaining_qty=6,
            )
        )

        await engine._exit_position("SBIN", "TARGET_HIT")

        updated = engine.positions["SBIN"]
        stored_positions = await store.list_positions(1)
        stored = next(row for row in stored_positions if row["symbol"] == "SBIN")
        self.assertEqual(updated.status, "OPEN")
        self.assertEqual(updated.qty, 6)
        self.assertEqual(updated.exit_filled_qty, 4)
        self.assertEqual(updated.exit_remaining_qty, 6)
        self.assertEqual(updated.pending_reason, "PARTIAL_EXIT_FILLED:4/10")
        self.assertEqual(stored["qty"], 6)
        self.assertEqual(await store.get_open(1, "SBIN"), "T1")
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_full_exit_keeps_closed_snapshot_for_dashboard_history(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        pos = Position(
            trade_id="T1",
            user_id=1,
            symbol="ABREL",
            alert_name="TEST",
            side="BUY",
            product="MIS",
            qty=1,
            initial_qty=1,
            entry_price=1402.0,
            target_price=1423.03,
            sl_price=1387.98,
            ltp=1436.0,
            pnl=34.0,
            status="OPEN",
        )
        engine.positions["ABREL"] = pos
        await store.upsert_position(1, "ABREL", pos.to_public())
        await store.mark_open(1, "ABREL", "T1")
        engine._place_order_with_execution = AsyncMock(
            return_value=OrderExecution(
                order_id="EXIT-1",
                symbol="ABREL",
                side="SELL",
                qty=1,
                status="COMPLETE",
                avg_price=1436.0,
                filled_qty=1,
                remaining_qty=0,
            )
        )

        await engine._exit_position("ABREL", "TARGET")

        stored_positions = await store.list_positions(1)
        stored = next(row for row in stored_positions if row["symbol"] == "ABREL")
        self.assertEqual(stored["status"], "CLOSED")
        self.assertEqual(stored["qty"], 0)
        self.assertEqual(stored["exit_reason"], "TARGET")
        self.assertEqual(stored["exit_order_id"], "EXIT-1")
        self.assertNotIn("ABREL", engine.positions)
        self.assertEqual(await store.get_open(1, "ABREL"), "")
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_candles_request_uses_intraday_time_boundaries(self) -> None:
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ")
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "data": {
                    "open": [],
                    "high": [],
                    "low": [],
                    "close": [],
                    "volume": [],
                    "timestamp": [],
                }
            }
        )

        with patch(
            "app.trade_engine.DHAN_INSTRUMENTS.security_id",
            AsyncMock(return_value="3045"),
        ):
            await engine._fetch_historical_candles("SBIN", "5minute", 5)

        kwargs = engine.market_data_worker.submit.await_args.kwargs
        self.assertTrue(kwargs["from_date"].endswith("09:15:00"))
        self.assertRegex(kwargs["to_date"], r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
        self.assertEqual(kwargs["interval"], 5)
        self.assertEqual(kwargs["instrument_type"], "EQUITY")
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_three_minute_fetch_uses_one_minute_and_resamples(self) -> None:
        from datetime import datetime, timedelta
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ")
        ist = pytz.timezone("Asia/Kolkata")
        start = ist.localize(datetime(2026, 6, 19, 9, 15))
        stamps = [int((start + timedelta(minutes=i)).timestamp()) for i in range(6)]
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "data": {
                    "open": [100, 101, 102, 103, 104, 105],
                    "high": [101, 102, 103, 104, 105, 106],
                    "low": [99, 100, 101, 102, 103, 104],
                    "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
                    "volume": [10, 11, 12, 13, 14, 15],
                    "timestamp": stamps,
                }
            }
        )

        result = await engine._fetch_dhan_intraday_candles(
            "3045",
            3,
            start,
            start + timedelta(minutes=5),
        )

        self.assertEqual(engine.market_data_worker.submit.await_args.kwargs["interval"], 1)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["close"], 102.5)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_intraday_discards_candles_outside_requested_window(self) -> None:
        from datetime import datetime, timedelta
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ")
        ist = pytz.timezone("Asia/Kolkata")
        requested_start = ist.localize(datetime(2026, 6, 12, 9, 15))
        stale_start = ist.localize(datetime(2026, 5, 29, 9, 15))
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "data": {
                    "open": [100],
                    "high": [101],
                    "low": [99],
                    "close": [100.5],
                    "volume": [10],
                    "timestamp": [int(stale_start.timestamp())],
                }
            }
        )

        result = await engine._fetch_dhan_intraday_candles(
            "3045",
            5,
            requested_start,
            requested_start + timedelta(minutes=5),
        )

        self.assertEqual(result, [])
        self.assertEqual(engine.market_data_worker.submit.await_args.kwargs["from_date"], "2026-06-12 09:15:00")
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_backtest_uses_index_segment_for_nifty(self) -> None:
        from datetime import datetime
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ", INDEX="IDX_I")
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "data": {
                    "open": [],
                    "high": [],
                    "low": [],
                    "close": [],
                    "volume": [],
                    "timestamp": [],
                }
            }
        )
        ist = pytz.timezone("Asia/Kolkata")

        await engine._fetch_backtest_candles(
            "NIFTY",
            "15minute",
            ist.localize(datetime(2026, 6, 19, 9, 15)),
            ist.localize(datetime(2026, 6, 19, 15, 30)),
            warmup_days=1,
        )

        kwargs = engine.market_data_worker.submit.await_args.kwargs
        self.assertEqual(kwargs["security_id"], "13")
        self.assertEqual(kwargs["exchange_segment"], "IDX_I")
        self.assertEqual(kwargs["instrument_type"], "INDEX")
        self.assertEqual(kwargs["interval"], 5)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_fifteen_minute_fetch_uses_five_minute_and_resamples(self) -> None:
        from datetime import datetime, timedelta
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ")
        ist = pytz.timezone("Asia/Kolkata")
        start = ist.localize(datetime(2026, 6, 19, 9, 15))
        stamps = [int((start + timedelta(minutes=5 * i)).timestamp()) for i in range(6)]
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "data": {
                    "open": [100, 101, 102, 103, 104, 105],
                    "high": [101, 102, 103, 104, 105, 106],
                    "low": [99, 100, 101, 102, 103, 104],
                    "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
                    "volume": [10, 11, 12, 13, 14, 15],
                    "timestamp": stamps,
                }
            }
        )

        result = await engine._fetch_dhan_intraday_candles(
            "3045",
            15,
            start,
            start + timedelta(minutes=25),
        )

        self.assertEqual(engine.market_data_worker.submit.await_args.kwargs["interval"], 5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"].strftime("%H:%M"), "09:15")
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["high"], 103)
        self.assertEqual(result[0]["low"], 99)
        self.assertEqual(result[0]["close"], 102.5)
        self.assertEqual(result[0]["volume"], 33)
        self.assertEqual(result[1]["date"].strftime("%H:%M"), "09:30")
        self.assertEqual(result[1]["close"], 105.5)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_backtest_fetches_selected_range_before_warmup(self) -> None:
        from datetime import datetime, timedelta
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ", INDEX="IDX_I")
        ist = pytz.timezone("Asia/Kolkata")
        selected_start = ist.localize(datetime(2026, 6, 19, 9, 15))
        warmup_start = ist.localize(datetime(2026, 6, 17, 9, 15))
        calls = []

        async def submit_side_effect(_fn, *args, **kwargs):
            calls.append(dict(kwargs))
            from_date = str(kwargs.get("from_date") or "")
            stamp = selected_start if from_date.startswith("2026-06-19") else warmup_start
            return {
                "data": {
                    "open": [100],
                    "high": [101],
                    "low": [99],
                    "close": [100.5],
                    "volume": [10],
                    "timestamp": [int(stamp.timestamp())],
                }
            }

        engine.market_data_worker.submit = AsyncMock(side_effect=submit_side_effect)

        result = await engine._fetch_backtest_candles(
            "NIFTY",
            "5minute",
            selected_start,
            selected_start.replace(hour=15, minute=30),
            warmup_days=2,
        )

        self.assertTrue(calls[0]["from_date"].startswith("2026-06-19"))
        self.assertTrue(any(candle["date"].date().isoformat() == "2026-06-19" for candle in result))
        self.assertTrue(any(candle["date"].date().isoformat() == "2026-06-17" for candle in result))
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()

    async def test_dhan_backtest_returns_empty_when_selected_range_has_no_candles(self) -> None:
        from datetime import datetime
        import pytz

        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "client", "token")
        engine = TradeEngine(1, store)
        await engine.configure_broker()
        engine.dhan = MagicMock(NSE="NSE_EQ", INDEX="IDX_I")
        engine.market_data_worker.submit = AsyncMock(
            return_value={"data": {"open": [], "high": [], "low": [], "close": [], "volume": [], "timestamp": []}}
        )
        ist = pytz.timezone("Asia/Kolkata")

        result = await engine._fetch_backtest_candles(
            "NIFTY",
            "5minute",
            ist.localize(datetime(2026, 6, 19, 9, 15)),
            ist.localize(datetime(2026, 6, 19, 15, 30)),
            warmup_days=2,
        )

        self.assertEqual(result, [])
        self.assertEqual(engine.market_data_worker.submit.await_count, 1)
        engine.order_worker.task.cancel()
        if engine._pnl_exit_task:
            engine._pnl_exit_task.cancel()


if __name__ == "__main__":
    unittest.main()
