import asyncio
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.trade_engine import OrderWorker, TradeEngine, Position
from app.order_locks import OrderLockLost
from app.memory_store import InMemoryStore
from app.services.job_queue import AsyncJobQueue
from app import reconciliation_service as recon
from app.dhan_broker import DhanFeedService, _QueuedDhanMarketFeed


class QueueAndFeedSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_queued_broker_order_is_never_sent(self):
        worker = OrderWorker()
        broker = Mock(return_value="unexpected")
        submit = asyncio.create_task(worker.submit(broker))
        await asyncio.sleep(0)
        submit.cancel()
        await asyncio.gather(submit, return_exceptions=True)
        await worker.start()
        try:
            await asyncio.wait_for(worker.q.join(), 1)
            broker.assert_not_called()
            self.assertEqual(await worker.submit(lambda: "next"), "next")
        finally:
            await worker.stop()

    async def test_dispatch_time_guard_prevents_broker_call(self):
        worker = OrderWorker()
        broker = Mock()
        guard = AsyncMock(side_effect=OrderLockLost("lost"))
        await worker.start()
        try:
            with self.assertRaises(OrderLockLost):
                await worker.submit(broker, _before_send=guard)
            broker.assert_not_called()
            guard.assert_awaited_once()
            await asyncio.wait_for(worker.q.join(), 1)
        finally:
            await worker.stop()

    async def test_worker_shutdown_resolves_all_waiters(self):
        worker = OrderWorker()
        entered = asyncio.Event()
        async def guard():
            entered.set()
            await asyncio.Event().wait()
        await worker.start()
        one = asyncio.create_task(worker.submit(Mock(), _before_send=guard))
        await entered.wait()
        two = asyncio.create_task(worker.submit(Mock()))
        await asyncio.sleep(0)
        await worker.stop()
        result = await asyncio.wait_for(asyncio.gather(one, two, return_exceptions=True), 1)
        self.assertTrue(all(isinstance(r, asyncio.CancelledError) for r in result))
        await asyncio.wait_for(worker.q.join(), 1)

    async def test_generic_queue_survives_cancelled_handler(self):
        async def handler(value):
            if value == "cancel":
                raise asyncio.CancelledError()
            return value
        queue = AsyncJobQueue("test", handler, workers=1)
        try:
            with self.assertRaisesRegex(RuntimeError, "JOB_CANCELLED"):
                await queue.submit("cancel")
            self.assertEqual(await asyncio.wait_for(queue.submit("next"), 1), "next")
        finally:
            await queue.stop()

    async def test_rest_reply_cannot_replace_ws_tick_arriving_during_request(self):
        recon._REST_FALLBACK_LAST.clear()
        store = InMemoryStore()
        async def quote(*args, **kwargs):
            await store.save_latest_tick(1, "SBIN", {"ltp": 101, "source": "DHAN_WS"})
            return 110
        engine = SimpleNamespace(_fetch_ltp=quote, on_tick=AsyncMock())
        registry = SimpleNamespace(get=AsyncMock(return_value=engine))
        await recon._rest_exit_fallback_position(store, registry, 1, {"symbol": "SBIN", "qty": 1, "status": "OPEN"})
        self.assertEqual((await store.load_latest_tick(1, "SBIN"))["source"], "DHAN_WS")
        self.assertEqual((await store.load_latest_tick(1, "SBIN"))["ltp"], 101)
        engine.on_tick.assert_not_awaited()
        recon._REST_FALLBACK_LAST.clear()

    async def test_nonfinite_rest_quote_is_not_used_for_exit(self):
        for price in (float("nan"), float("inf")):
            recon._REST_FALLBACK_LAST.clear()
            store = InMemoryStore()
            engine = SimpleNamespace(_fetch_ltp=AsyncMock(return_value=price), on_tick=AsyncMock())
            await recon._rest_exit_fallback_position(store, SimpleNamespace(get=AsyncMock(return_value=engine)),
                                                     1, {"symbol": "SBIN", "qty": 1, "status": "OPEN"})
            engine.on_tick.assert_not_awaited()
            self.assertEqual(await store.load_latest_tick(1, "SBIN"), {})
        recon._REST_FALLBACK_LAST.clear()

    async def test_slow_tick_delivery_does_not_discard_received_packet(self):
        service = DhanFeedService(1, "client", "test-token", AsyncMock(), AsyncMock())
        feed = _QueuedDhanMarketFeed(service.context, [], "v2")
        feed._running = True
        feed.delivery_loop = asyncio.get_running_loop()
        received = []
        async def deliver(packet):
            await asyncio.sleep(0.04)
            received.append(packet)
        feed.deliver = deliver
        feed.connect = AsyncMock()
        feed.ws = SimpleNamespace(recv=AsyncMock(side_effect=[
            struct.pack("<BHBIfI", 2, 16, 1, 3045, 110.0, 0), asyncio.CancelledError(),
        ]))
        original_wait = asyncio.wait_for
        async def short_receive(awaitable, timeout):
            return await original_wait(awaitable, 0.01 if timeout == 60 else timeout)
        try:
            with patch("app.dhan_broker.asyncio.wait_for", side_effect=short_receive):
                with self.assertRaises(asyncio.CancelledError):
                    await feed._run_async()
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["LTP"], "110.00")
        finally:
            feed.loop.close()

    async def test_closed_position_cannot_be_resurrected_or_exited_twice(self):
        store = InMemoryStore()
        pos = Position(trade_id="T1", user_id=1, symbol="SBIN", alert_name="classic", side="BUY", product="MIS",
                       qty=2, initial_qty=2, entry_price=100, target_price=110, sl_price=95, status="OPEN")
        closed = dict(pos.to_public(), qty=0, status="CLOSED")
        await store.upsert_position(1, "SBIN", closed)
        with self.assertRaisesRegex(RuntimeError, "STALE_POSITION_REOPEN"):
            await store.upsert_position(1, "SBIN", pos.to_public())
        engine = TradeEngine(1, store)
        engine.positions["SBIN"] = pos
        engine._place_order_with_execution = AsyncMock()
        await engine._exit_position("SBIN", "TARGET")
        engine._place_order_with_execution.assert_not_awaited()
        self.assertEqual((await store.get_position(1, "SBIN"))["status"], "CLOSED")
        await store.close()

    async def test_subscribe_api_uses_dedicated_feed_when_api_is_not_owner(self):
        from app import main
        with patch.object(main, "API_OWNS_MARKET_FEED", False), \
             patch.object(main, "_poke_market_feed_service", AsyncMock()) as queued, \
             patch.object(main, "subscribe_symbols_for_user", AsyncMock()) as local:
            result = await main.api_subscribe_symbols({"user_id": 1, "symbols": ["SBIN"]})
        queued.assert_awaited_once_with(1, ["SBIN"], "api_signal_intake")
        local.assert_not_awaited()
        self.assertEqual(result["requested"], ["SBIN"])
