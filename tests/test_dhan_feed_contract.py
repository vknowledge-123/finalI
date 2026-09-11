import asyncio
import json
import struct
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
from websockets.protocol import State

from app.dhan_broker import DhanFeedService, DhanInstrumentRegistry, MarketFeed, _QueuedDhanMarketFeed
from app.memory_store import InMemoryStore
from app.redis_store import RedisStore


class DhanFeedContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_releases_sdk_receiver_thread_loop_and_consumers(self):
        started = asyncio.Event()
        states = []

        async def receiver(feed):
            feed.delivery_loop.call_soon_threadsafe(started.set)
            await asyncio.Event().wait()

        async def order_receiver():
            await asyncio.Event().wait()

        service = DhanFeedService(1, "client", "token", lambda p: None, lambda p: None, states.append)
        with patch.object(_QueuedDhanMarketFeed, "_run_async", receiver), \
             patch("app.dhan_broker._SafeDhanOrderUpdate", return_value=SimpleNamespace(connect_order_update=order_receiver)):
            await service.start(["3045"])
            feed = service.feed
            thread = service.feed_thread
            consumers = list(service._tick_tasks)
            try:
                await asyncio.wait_for(started.wait(), 2)
            finally:
                await asyncio.wait_for(service.stop(), 12)
            self.assertFalse(thread.is_alive())
            self.assertTrue(feed.loop.is_closed())
            self.assertTrue(all(task.done() for task in consumers))
            self.assertFalse(states[-1])

    async def test_sector_registration_preserves_index_segment_zero(self):
        registry = DhanInstrumentRegistry()
        registry.register_instrument("NIFTY AUTO", "14", MarketFeed.IDX)
        registry.register_instrument("SBIN", "3045")
        self.assertEqual(registry.feed_segment("14"), 0)
        self.assertEqual(registry.feed_segment("3045"), 1)

    async def test_sector_only_registry_still_loads_equity_master(self):
        registry = DhanInstrumentRegistry()
        registry.register_instrument("NIFTY AUTO", "14", MarketFeed.IDX)
        registry._master_frame = pd.DataFrame([{
            "SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "E", "SEM_SERIES": "EQ",
            "SEM_INSTRUMENT_NAME": "EQUITY", "SEM_TRADING_SYMBOL": "SBIN",
            "SEM_SMST_SECURITY_ID": "3045", "SEM_TICK_SIZE": 5,
        }])
        self.assertTrue(await registry.ensure_loaded())
        self.assertEqual(await registry.security_id("SBIN"), "3045")

    async def test_same_security_id_keeps_stock_and_index_independent(self):
        registry = DhanInstrumentRegistry()
        registry.register_instrument("ADANIENT", "25", MarketFeed.NSE, 5)
        registry.register_instrument("NIFTY BANK", "25", MarketFeed.IDX, 1)
        self.assertEqual(registry.instrument_key("ADANIENT"), (1, "25"))
        self.assertEqual(registry.instrument_key("NIFTY BANK"), (0, "25"))
        self.assertEqual(registry.symbol("25", 1), "ADANIENT")
        self.assertEqual(registry.symbol("25", 0), "NIFTY BANK")
        self.assertEqual(registry.symbol("25"), "")
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS_SECURITY_ID"):
            registry.feed_segment("25")
        self.assertEqual(registry.tick_size("25", segment=1), 0.05)
        self.assertEqual(registry.tick_size("25", segment=0), 0.01)
        service = DhanFeedService(1, "client", "token", lambda p: None, lambda p: None)
        await service.subscribe([registry.instrument_key("ADANIENT"), registry.instrument_key("NIFTY BANK")])
        self.assertEqual(service.security_ids, {(1, "25"), (0, "25")})

    async def test_cold_master_parse_runs_off_event_loop(self):
        registry = DhanInstrumentRegistry()
        registry._master_frame = pd.DataFrame()
        started, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        parse_threads = []

        def parse(frame):
            parse_threads.append(threading.get_ident())
            started.set()
            release.wait(3)
            return [("ADANIENT", "25", "5")]

        with patch.object(registry, "_parse_equity_master", side_effect=parse):
            task = asyncio.create_task(registry.ensure_loaded())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                self.assertFalse(task.done())
                self.assertNotEqual(parse_threads[0], loop_thread)
            finally:
                release.set()
                self.assertTrue(await asyncio.wait_for(task, 2))
        self.assertEqual(registry.instrument_key("ADANIENT"), (1, "25"))

    async def test_master_refresh_removes_obsolete_equity_ids_but_keeps_sectors(self):
        registry = DhanInstrumentRegistry()
        registry.register_instrument("OLD", "99", MarketFeed.NSE)
        registry.register_instrument("NIFTY AUTO", "14", MarketFeed.IDX)
        with patch.object(registry, "_load_master_frame", AsyncMock(return_value=pd.DataFrame())), \
             patch.object(registry, "_parse_equity_master", return_value=[("NEW", "100", "5")]):
            self.assertTrue(await registry.ensure_loaded(force=True))
        self.assertIsNone(registry.instrument_key("OLD"))
        self.assertEqual(registry.symbol("99", 1), "")
        self.assertEqual(registry.instrument_key("NEW"), (1, "100"))
        self.assertEqual(registry.instrument_key("NIFTY AUTO"), (0, "14"))

    async def test_failed_send_is_retried_and_sdk_batches_correct_segments(self):
        service = DhanFeedService(1, "test-client", "test-token", lambda p: None, lambda p: None)
        feed = _QueuedDhanMarketFeed(service.context, [], "v2")
        feed.ws = SimpleNamespace(state=State.OPEN, send=AsyncMock(side_effect=OSError("send failed")))
        service.feed = feed
        thread = threading.Thread(target=feed.loop.run_forever, daemon=True)
        thread.start()
        try:
            registry = DhanInstrumentRegistry()
            registry.register_instrument("NIFTY AUTO", "14", MarketFeed.IDX)
            ids = ["14"] + [str(i) for i in range(10000, 10101)]
            with patch("app.dhan_broker.DHAN_INSTRUMENTS", registry):
                with self.assertRaisesRegex(OSError, "send failed"):
                    await service.subscribe(ids)
                self.assertFalse(service.sent_security_ids)
                feed.ws.send = AsyncMock()
                self.assertTrue(await service.subscribe(ids))
            messages = [json.loads(call.args[0]) for call in feed.ws.send.await_args_list]
            self.assertEqual([m["InstrumentCount"] for m in messages], [100, 2])
            instruments = [i for m in messages for i in m["InstrumentList"]]
            self.assertIn({"ExchangeSegment": "IDX_I", "SecurityId": "14"}, instruments)
            self.assertEqual(service.sent_security_ids, {(0, "14")} | {(1, sid) for sid in ids[1:]})
            self.assertTrue(all(m["RequestCode"] == 21 for m in messages))
            await service.subscribe(service.security_ids)
            self.assertEqual(feed.ws.send.await_count, 2)
        finally:
            feed.loop.call_soon_threadsafe(feed.loop.stop)
            await asyncio.to_thread(thread.join, 2)
            feed.loop.close()

    async def test_disconnected_subscription_keeps_desired_list_without_claiming_sent(self):
        service = DhanFeedService(1, "client", "token", lambda p: None, lambda p: None)
        self.assertFalse(await service.subscribe(["3045"]))
        self.assertEqual(service.security_ids, {(MarketFeed.NSE, "3045")})
        self.assertFalse(service.sent_security_ids)
        with self.assertRaisesRegex(ValueError, "SUBSCRIPTION_LIMIT"):
            await service.subscribe([str(i) for i in range(6000)])

    async def test_sdk_binary_packets_reach_bounded_consumer_in_order(self):
        received = []
        service = DhanFeedService(1, "client", "token", received.append, lambda p: None)
        service._tick_queues = [asyncio.Queue(maxsize=2)]
        consumer = asyncio.create_task(service._consume_ticks(service._tick_queues[0]))
        feed = _QueuedDhanMarketFeed(service.context, [], "v2")
        feed.delivery_loop = asyncio.get_running_loop()
        feed.deliver = service._deliver_tick
        feed.ws = SimpleNamespace(recv=AsyncMock(side_effect=[
            struct.pack("<BHBIfI", 6, 16, 0, 14, 100.0, 0),
            struct.pack("<BHBIfI", 2, 16, 0, 14, 102.0, 123),
        ]))
        thread = threading.Thread(target=feed.loop.run_forever, daemon=True)
        thread.start()
        try:
            for _ in range(2):
                await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(feed.get_instrument_data(), feed.loop))
            await asyncio.wait_for(service._tick_queues[0].join(), 2)
            self.assertEqual([p["type"] for p in received], ["Previous Close", "Ticker Data"])
            self.assertEqual(received[1]["LTP"], "102.00")
            self.assertGreater(received[0]["received_at"], 0)
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            feed.loop.call_soon_threadsafe(feed.loop.stop)
            await asyncio.to_thread(thread.join, 2)
            feed.loop.close()

    async def test_backpressure_bounds_queue_and_preserves_price_crossings(self):
        service = DhanFeedService(1, "client", "token", lambda p: None, lambda p: None)
        queue = asyncio.Queue(maxsize=2)
        service._tick_queues = [queue]
        await service._deliver_tick({"security_id": 14, "LTP": 100})
        await service._deliver_tick({"security_id": 14, "LTP": 110})
        pending = asyncio.create_task(service._deliver_tick({"security_id": 14, "LTP": 101}))
        try:
            await asyncio.sleep(0)
            self.assertFalse(pending.done())
            self.assertEqual(queue.qsize(), 2)
            self.assertEqual((await queue.get())["LTP"], 100)
            await asyncio.wait_for(pending, 1)
            self.assertEqual((await queue.get())["LTP"], 110)
            self.assertEqual((await queue.get())["LTP"], 101)
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def test_buffered_ticks_keep_receive_age_in_both_stores(self):
        old = time.time() - 20
        memory = InMemoryStore()
        await memory.save_latest_tick(1, "SBIN", {"ltp": 100, "received_at": old, "source": "DHAN_WS"})
        self.assertGreaterEqual((await memory.load_latest_tick(1, "SBIN"))["age_sec"], 20)
        redis = RedisStore.__new__(RedisStore)
        redis.redis = SimpleNamespace(setex=AsyncMock())
        await redis.save_latest_tick(1, "SBIN", {"ltp": 100, "received_at": old, "source": "DHAN_WS"})
        data = json.loads(redis.redis.setex.await_args.args[2])
        self.assertEqual(data["ts"], old)

    async def test_previous_close_and_ticks_flow_to_engine_and_dashboard(self):
        from app import main as main_app

        memory = InMemoryStore()
        await memory.save_dhan_credentials(1, "client", "token")
        captured = {}

        def fake_service(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(start=AsyncMock())

        engine = SimpleNamespace(on_tick=AsyncMock(return_value=None), on_order_update=AsyncMock())
        registry = DhanInstrumentRegistry()
        registry.register_instrument("NIFTY AUTO", "14", MarketFeed.IDX)
        registry.register_instrument("ADANIENT", "25", MarketFeed.NSE)
        registry.register_instrument("NIFTY BANK", "25", MarketFeed.IDX)
        with patch.object(main_app, "store", memory), patch.object(main_app, "_is_test_mode", return_value=False), \
             patch.object(main_app, "_stop_kite_ticker", AsyncMock()), \
             patch.object(main_app, "_stop_dhan_feed", AsyncMock()), \
             patch.object(main_app, "DHAN_FEED", None), patch.object(main_app, "DHAN_USER_ID", None), \
             patch.object(main_app, "DHAN_ACCESS_TOKEN", ""), patch.object(main_app, "SUB_TOKENS", {14}), \
             patch.object(main_app, "DHAN_INSTRUMENTS", registry), \
             patch.object(main_app, "DHAN_SUBSCRIPTIONS", {(0, "14")}), \
             patch.object(main_app, "ensure_engine", AsyncMock(return_value=engine)), \
             patch.object(main_app, "DhanFeedService", side_effect=fake_service), \
             patch.object(main_app.ws_mgr, "broadcast_nowait") as broadcast:
            await main_app.start_dhan_feed(1)
            handler = captured["on_tick"]
            await handler({"type": "Previous Close", "exchange_segment": 0, "security_id": 14, "prev_close": "100.0"})
            engine.on_tick.assert_not_awaited()
            self.assertEqual(await memory.load_latest_tick(1, "NIFTY AUTO"), {})
            await handler({"type": "Full Data", "exchange_segment": 0, "security_id": 14, "LTP": "102", "close": "0", "depth": [{"bid_price": "101"}]})
            tick = await memory.load_latest_tick(1, "NIFTY AUTO")
            self.assertEqual(tick["close"], 100)
            self.assertEqual(tick["ltp"], 102)
            self.assertEqual(tick["source"], "DHAN_WS")
            self.assertEqual(tick["depth"], [{"bid_price": "101"}])
            self.assertEqual(engine.on_tick.await_args.args[:3], ("NIFTY AUTO", 102, 100))
            self.assertEqual(broadcast.call_args.args[1]["close"], 100)
            for segment, close, price in [(1, 3000, 3010), (0, 50000, 50100)]:
                await handler({"type": "Previous Close", "exchange_segment": segment, "security_id": 25, "prev_close": close})
                await handler({"type": "Ticker Data", "exchange_segment": segment, "security_id": 25, "LTP": price})
            stock = await memory.load_latest_tick(1, "ADANIENT")
            index = await memory.load_latest_tick(1, "NIFTY BANK")
            self.assertEqual((stock["ltp"], stock["close"]), (3010, 3000))
            self.assertEqual((index["ltp"], index["close"]), (50100, 50000))
            await captured["on_order_update"]({"Type": "order_alert", "Data": {
                "OrderNo": "O1", "Status": "TRADED", "Symbol": "SBIN", "Quantity": 2,
                "TradedQty": 2, "RemainingQuantity": 0, "AvgTradedPrice": 100,
            }})
            order = engine.on_order_update.await_args.args[0]
            self.assertEqual(order["order_id"], "O1")
            self.assertEqual(order["status"], "TRADED")
            self.assertEqual(order["filledQuantity"], 2)
            self.assertEqual(order["remainingQuantity"], 0)
