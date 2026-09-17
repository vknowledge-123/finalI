import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from dhanhq import DhanContext, dhanhq
from kiteconnect import KiteConnect

from app.auth import AuthService
from app.dhan_broker import normalize_dhan_holdings
from app.kite_broker import KiteCallbackQueue, place_protected_order, normalize_holdings
from app.memory_store import InMemoryStore
from app.models import OTP
from app.service_bootstrap import EngineRegistry
from app.trade_engine import TradeEngine, MarketDataWorker, Position, OrderExecution, _normalize_order_snapshot


class SDKContractTests(unittest.TestCase):
    def test_pinned_kite_sdk_sends_protection_through_authenticated_transport_once(self):
        client = KiteConnect(api_key="FAKE")
        client._post = Mock(return_value={"order_id": "SIMULATED"})
        result = place_protected_order(client, variety="regular", exchange="NSE", tradingsymbol="SBIN",
                                       transaction_type="BUY", quantity=1, product="MIS", order_type="MARKET",
                                       market_protection=-1)
        self.assertEqual(result, "SIMULATED")
        client._post.assert_called_once()
        self.assertEqual(client._post.call_args.kwargs["params"]["market_protection"], -1)
        self.assertEqual(client._post.call_args.kwargs["url_args"], {"variety": "regular"})

    def test_new_sdk_signature_uses_public_method_without_exception_retry(self):
        class Client:
            def place_order(self, variety, market_protection):
                raise TypeError("simulated transport error after request")
        client = Client()
        client._post = Mock()
        with self.assertRaises(TypeError):
            place_protected_order(client, variety="regular", market_protection=-1)
        client._post.assert_not_called()

    def test_real_dhan_22_order_and_candle_payloads(self):
        client = dhanhq(DhanContext("FAKE", "FAKE"))
        client.dhan_http.post = Mock(return_value={"status": "success", "data": {"orderId": "SIMULATED"}})
        client.place_order(security_id="1333", exchange_segment="NSE_EQ", transaction_type="BUY", quantity=2,
                           order_type="LIMIT", product_type="INTRADAY", price=100.05)
        endpoint, data = client.dhan_http.post.call_args.args
        self.assertEqual(endpoint, "/orders")
        self.assertEqual((data["securityId"], data["quantity"], data["price"]), ("1333", 2, 100.05))
        client.intraday_minute_data("1333", "NSE_EQ", "EQUITY", "2026-09-16", "2026-09-17", interval=5)
        endpoint, data = client.dhan_http.post.call_args.args
        self.assertEqual(endpoint, "/charts/intraday")
        self.assertEqual(data["interval"], 5)
        self.assertEqual(data["instrument"], "EQUITY")

    def test_explicit_zero_fill_is_not_manufactured_from_cancel_or_complete(self):
        for status in ("CANCELLED", "EXPIRED", "COMPLETE", "REJECTED"):
            result = _normalize_order_snapshot({"order_id": "O1", "status": status, "quantity": 10,
                                                "filled_quantity": 0, "pending_quantity": 0, "average_price": 100})
            self.assertEqual(result["filled_quantity"], 0)
        self.assertEqual(_normalize_order_snapshot({"orderStatus": "EXPIRED"})["status"], "CANCELLED")

    def test_holdings_fail_closed_and_zero_availability_is_preserved(self):
        with self.assertRaises(ValueError):
            normalize_dhan_holdings({"data": {"unexpected": []}})
        rows = normalize_dhan_holdings({"data": [{"tradingSymbol": "SBIN", "availableQty": 0, "totalQty": 50}]})
        self.assertEqual(rows[0]["quantity"], 0)
        rows = normalize_holdings([{"tradingsymbol": "SBIN", "quantity": 4, "t1_quantity": 3, "used_quantity": 2}])
        self.assertEqual(rows[0]["quantity"], 5)
        with self.assertRaises(ValueError):
            normalize_holdings({"error": "token expired"})


class BrokerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemoryStore()
        self.engine = TradeEngine(1, self.store)
        self.engine.api_key = "FAKE"
        self.engine.access_token = "FAKE"
        self.engine.kite = KiteConnect(api_key="FAKE")
        await self.engine.order_worker.start()

    async def asyncTearDown(self):
        await self.engine.close()

    async def test_zerodha_accepted_order_requires_real_fill(self):
        self.engine.kite._post = Mock(return_value={"order_id": "O1"})
        self.engine.kite._get = Mock(return_value=[{"order_id": "O1", "status": "COMPLETE", "quantity": 2,
                                                   "filled_quantity": 2, "average_price": 101.25, "pending_quantity": 0}])
        execution = await self.engine._place_order_with_execution("SBIN", "BUY", 2, "MIS", {"confirm_order_execution": False})
        self.assertEqual(execution.filled_qty, 2)
        self.assertEqual(execution.avg_price, 101.25)
        self.engine.kite._get.assert_called_once_with("order.info", url_args={"order_id": "O1"})

    async def test_zerodha_rejected_order_does_not_become_live(self):
        self.engine._place_order = AsyncMock(return_value="O1")
        self.engine._wait_for_order_execution = AsyncMock(return_value={"order_id": "O1", "status": "REJECTED", "filled_quantity": 0})
        self.engine._fetch_dhan_order_list_snapshot = AsyncMock(return_value={})
        self.engine._fetch_dhan_trade_snapshot = AsyncMock(return_value={})
        with self.assertRaisesRegex(RuntimeError, "ORDER_REJECTED"):
            await self.engine._place_order_with_execution("SBIN", "BUY", 2, "MIS")
        self.engine._place_order.assert_awaited_once()
        self.assertEqual(self.engine.positions, {})

    async def test_zerodha_cancel_race_retries_only_unfilled_quantity(self):
        self.engine._place_order = AsyncMock(side_effect=["O1", "O2"])
        self.engine._wait_for_order_execution = AsyncMock(side_effect=[
            {"status": "PENDING", "filled_quantity": 0},
            {"status": "CANCELLED", "filled_quantity": 2, "average_price": 100},
            {"status": "COMPLETE", "filled_quantity": 3, "average_price": 101},
        ])
        self.engine._cancel_order_if_pending = AsyncMock(return_value=True)
        self.engine._fetch_dhan_order_list_snapshot = AsyncMock(return_value={})
        self.engine._fetch_dhan_trade_snapshot = AsyncMock(return_value={})
        result = await self.engine._place_order_with_execution("SBIN", "BUY", 5, "MIS")
        self.assertEqual([c.args[2] for c in self.engine._place_order.await_args_list], [5, 3])
        self.assertEqual(result.filled_qty, 5)
        self.assertAlmostEqual(result.avg_price, 100.6)

    async def test_missing_order_id_blocks_future_entries(self):
        self.engine._place_order = AsyncMock(return_value="")
        with self.assertRaisesRegex(RuntimeError, "ORDER_ID_MISSING"):
            await self.engine._place_order_with_execution("SBIN", "BUY", 2, "MIS")
        self.assertTrue(await self.store.is_kill(1))

    async def test_failed_retry_keeps_confirmed_partial_fill_and_blocks_entries(self):
        self.engine._place_order = AsyncMock(side_effect=["O1", RuntimeError("submission timeout")])
        self.engine._wait_for_order_execution = AsyncMock(side_effect=[
            {"status": "PENDING", "filled_quantity": 2, "average_price": 100},
            {"status": "CANCELLED", "filled_quantity": 2, "average_price": 100},
        ])
        self.engine._cancel_order_if_pending = AsyncMock(return_value=True)
        self.engine._fetch_dhan_order_list_snapshot = AsyncMock(return_value={})
        self.engine._fetch_dhan_trade_snapshot = AsyncMock(return_value={})
        execution = await self.engine._place_order_with_execution("SBIN", "BUY", 5, "MIS")
        self.assertEqual((execution.filled_qty, execution.remaining_qty, execution.avg_price), (2, 3, 100))
        self.assertEqual(execution.order_id, "O1")
        self.assertTrue(await self.store.is_kill(1))

    async def test_complete_status_with_zero_fill_requires_reconciliation(self):
        self.engine._place_order = AsyncMock(return_value="O1")
        self.engine._wait_for_order_execution = AsyncMock(return_value={"status": "COMPLETE", "filled_quantity": 0})
        self.engine._fetch_dhan_order_list_snapshot = AsyncMock(return_value={})
        self.engine._fetch_dhan_trade_snapshot = AsyncMock(return_value={})
        with self.assertRaisesRegex(RuntimeError, "ORDER_COMPLETE_WITHOUT_FILL"):
            await self.engine._place_order_with_execution("SBIN", "BUY", 5, "MIS")
        self.assertTrue(await self.store.is_kill(1))

    async def test_queue_time_deadline_check_prevents_late_submission(self):
        self.engine.kite._post = Mock(return_value={"order_id": "O1"})
        with self.assertRaisesRegex(RuntimeError, "BREAKOUT_TTL_EXPIRED"):
            await self.engine._place_order("SBIN", "BUY", 1, "MIS", {"_breakout_deadline": time.time() - 1})
        self.engine.kite._post.assert_not_called()

    async def test_manual_exit_respects_net_view_and_reports_actual_fill(self):
        self.engine._broker_positions = AsyncMock(return_value={"net": [], "day": [
            {"tradingsymbol": "SBIN", "quantity": 10, "product": "MIS"}]})
        self.engine._place_order_with_execution = AsyncMock(return_value=OrderExecution(
            order_id="O1", symbol="SBIN", side="SELL", qty=10, filled_qty=3, status="PARTIAL"))
        result = await self.engine.manual_squareoff_zerodha("SBIN")
        self.assertEqual(result["status"], "NOT_FOUND")
        self.engine._place_order_with_execution.assert_not_awaited()
        self.engine._broker_positions.return_value["net"] = self.engine._broker_positions.return_value["day"]
        result = await self.engine.manual_squareoff_zerodha("SBIN")
        self.assertEqual((result["status"], result["qty"], result["remaining_qty"]), ("EXIT_PARTIAL", 3, 7))

    async def test_manual_exit_cannot_overlap_another_process_exit(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def positions():
            entered.set()
            await release.wait()
            return {"net": []}
        self.engine._broker_positions = positions
        task = asyncio.create_task(self.engine.manual_squareoff_zerodha("SBIN"))
        await entered.wait()
        try:
            result = await self.engine.manual_squareoff_zerodha("SBIN")
            self.assertEqual(result["status"], "BUSY")
        finally:
            release.set()
            await task

    async def test_unrelated_account_quantity_does_not_prove_order_fill(self):
        self.engine.broker = "DHAN"
        self.engine._fetch_broker_symbol_qty = AsyncMock(return_value=100)
        self.engine._fetch_dhan_order_list_snapshot = AsyncMock(return_value={})
        self.engine._fetch_dhan_trade_snapshot = AsyncMock(return_value={})
        result, _ = await self.engine._reconcile_dhan_execution_snapshot("O1", "SBIN", "BUY", 10, 0, {"status": "PENDING"})
        self.assertFalse(result.get("filled_quantity"))
        self.engine._fetch_broker_symbol_qty.assert_not_awaited()

    async def test_zerodha_trade_book_and_order_book_use_actual_sdk_methods(self):
        def get(route, **kwargs):
            if route == "orders":
                return [{"order_id": "O1", "status": "OPEN", "quantity": 5, "filled_quantity": 2}]
            if route == "order.trades":
                return [{"order_id": "O1", "quantity": 1, "average_price": 100},
                        {"order_id": "O1", "quantity": 1, "average_price": 102}]
            raise AssertionError(route)
        self.engine.kite._get = Mock(side_effect=get)
        self.assertEqual((await self.engine._fetch_dhan_order_list_snapshot("O1"))["status"], "PENDING")
        trade = await self.engine._fetch_dhan_trade_snapshot("O1")
        self.assertEqual(trade["filled_quantity"], 2)
        self.assertEqual(trade["average_price"], 101)

    async def test_zerodha_carry_fetches_holdings_without_adopting_manual_shares(self):
        pos = Position(trade_id="T1", user_id=1, symbol="SBIN", alert_name="test", side="BUY", product="CNC",
                       qty=2, initial_qty=2, entry_price=100, target_price=110, sl_price=95, status="OPEN")
        await self.store.save_cnc_carry_position(1, "SBIN", pos.to_public())
        self.engine.kite._get = Mock(return_value=[{"tradingsymbol": "SBIN", "quantity": 20,
                                                   "t1_quantity": 0, "average_price": 80}])
        self.assertEqual(await self.engine.rehydrate_cnc_carry_positions(), ["SBIN"])
        self.assertEqual(self.engine.positions["SBIN"].qty, 2)
        self.assertEqual(self.engine.positions["SBIN"].entry_price, 100)
        self.engine.kite._get.assert_called_once_with("portfolio.holdings")

    async def test_failed_holdings_do_not_delete_carry(self):
        await self.store.save_cnc_carry_position(1, "SBIN", {"symbol": "SBIN", "qty": 2})
        self.engine.kite._get = Mock(side_effect=RuntimeError("unavailable"))
        self.assertEqual(await self.engine.rehydrate_cnc_carry_positions(), [])
        self.assertEqual(len(await self.store.list_cnc_carry_positions(1)), 1)

    async def test_today_cnc_position_is_not_deleted_before_settlement(self):
        pos = Position(trade_id="T1", user_id=1, symbol="SBIN", alert_name="test", side="BUY", product="CNC",
                       qty=2, initial_qty=2, entry_price=100, target_price=110, sl_price=95, status="OPEN")
        await self.store.save_cnc_carry_position(1, "SBIN", pos.to_public())
        self.engine._broker_holdings = AsyncMock(return_value=[])
        self.engine._broker_positions = AsyncMock(return_value={"net": [
            {"tradingsymbol": "SBIN", "product": "CNC", "quantity": 2, "average_price": 100}]})
        self.assertEqual(await self.engine.rehydrate_cnc_carry_positions(), ["SBIN"])
        self.assertEqual(await self.store.get_open(1, "SBIN"), "T1")
        self.assertEqual(len(await self.store.list_cnc_carry_positions(1)), 1)

    async def test_absent_cnc_holding_does_not_prove_confirmed_exit(self):
        await self.store.save_cnc_carry_position(1, "SBIN", {"symbol": "SBIN", "qty": 2})
        await self.store.mark_open(1, "SBIN", "T1")
        self.engine._broker_holdings = AsyncMock(return_value=[])
        self.engine._broker_positions = AsyncMock(return_value={"net": []})
        self.assertEqual(await self.engine.rehydrate_cnc_carry_positions(), [])
        self.assertEqual(len(await self.store.list_cnc_carry_positions(1)), 1)
        self.assertEqual(await self.store.get_open(1, "SBIN"), "T1")


class ConcurrencyAndAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_creation_cannot_overwrite_an_existing_admin(self):
        import fakeredis.aioredis
        from app.redis_store import RedisStore
        redis_store = RedisStore("redis://127.0.0.1:6379/0")
        redis_store.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        try:
            for store in (InMemoryStore(), redis_store):
                results = await asyncio.gather(store.save_admin_auth("first@example.com", "hash1"),
                                               store.save_admin_auth("second@example.com", "hash2"))
                self.assertEqual(sum(bool(result) for result in results), 1)
                saved = await store.load_admin_auth()
                winner = next(result for result in results if result)
                self.assertEqual(saved, winner)
        finally:
            await redis_store.redis.aclose()

    async def test_kite_queue_is_fifo_bounded_and_closes_cleanly(self):
        overflow = AsyncMock()
        delivery = KiteCallbackQueue(overflow, capacity=2)
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        async def first():
            entered.set()
            await release.wait()
            seen.append(1)
        async def second():
            seen.append(2)
        async def third():
            seen.append(3)
        try:
            self.assertTrue(await asyncio.to_thread(delivery.submit, first))
            await asyncio.wait_for(entered.wait(), 1)
            self.assertTrue(await asyncio.to_thread(delivery.submit, second))
            self.assertTrue(await asyncio.to_thread(delivery.submit, third))
            self.assertFalse(await asyncio.to_thread(delivery.submit, third))
            self.assertEqual(delivery.queue.qsize(), 2)
            release.set()
            await asyncio.wait_for(asyncio.to_thread(delivery.queue.join), 2)
            self.assertEqual(seen, [1, 2, 3])
            overflow.assert_awaited_once()
        finally:
            release.set()
            await delivery.close()
        self.assertFalse(delivery.submit(third))
        self.assertTrue(delivery.task.done())

    async def test_kite_callback_and_health_failures_do_not_kill_consumer(self):
        delivery = KiteCallbackQueue(AsyncMock(side_effect=RuntimeError("Redis unavailable")), capacity=2)
        done = asyncio.Event()
        async def bad():
            raise RuntimeError("invalid tick")
        async def good():
            done.set()
        try:
            delivery.submit(bad)
            delivery.submit(good)
            self.assertFalse(delivery.submit(good))
            with self.assertLogs("app.kite_broker", level="ERROR") as logs:
                await asyncio.wait_for(done.wait(), 2)
            self.assertTrue(any("ZERODHA_TICK_DELIVERY_FAILED" in row for row in logs.output))
            self.assertTrue(any("ZERODHA_TICK_OVERFLOW_HEALTH_WRITE_FAILED" in row for row in logs.output))
            self.assertFalse(delivery.task.done())
        finally:
            await delivery.close()

    async def test_registry_refreshes_replaced_token_and_reuses_unchanged_client(self):
        store = InMemoryStore()
        await store.save_broker(1, "DHAN")
        await store.save_dhan_credentials(1, "FAKE", "OLD")
        registry = EngineRegistry(store)
        try:
            engine = await registry.get(1)
            sentinel = object()
            engine.dhan = sentinel
            self.assertIs((await registry.get(1)).dhan, sentinel)
            await store.save_dhan_credentials(1, "FAKE", "NEW")
            self.assertIs(await registry.get(1), engine)
            self.assertEqual(engine.dhan_access_token, "NEW")
            self.assertIsNone(engine.dhan)
        finally:
            await registry.close()

    async def test_backtest_admission_stays_bounded_after_request_cancellation(self):
        from app.services.backtest_service import BacktestService
        service = BacktestService()
        service._job = asyncio.get_running_loop().create_future()
        with self.assertRaisesRegex(RuntimeError, "BACKTEST_BUSY"):
            await service.run_custom_strategy([])
        service._job.cancel()
        await service.close()

    async def test_registry_never_exposes_partially_initialized_engine(self):
        store = InMemoryStore()
        registry = EngineRegistry(store)
        entered, release = asyncio.Event(), asyncio.Event()
        async def configure():
            entered.set()
            await release.wait()
        fake = SimpleNamespace(configure_broker=configure, rehydrate_open_positions=AsyncMock(), close=AsyncMock())
        with patch("app.service_bootstrap.TradeEngine", return_value=fake) as factory:
            first = asyncio.create_task(registry.get(1))
            await entered.wait()
            second = asyncio.create_task(registry.get(1))
            await asyncio.sleep(.01)
            self.assertFalse(second.done())
            self.assertEqual(registry.engines, {})
            release.set()
            self.assertEqual(await asyncio.gather(first, second), [fake, fake])
            factory.assert_called_once()
        await registry.close()

    async def test_registry_failure_can_be_retried_without_leaked_worker(self):
        registry = EngineRegistry(InMemoryStore())
        fake = SimpleNamespace(configure_broker=AsyncMock(side_effect=RuntimeError("failed")), close=AsyncMock())
        with patch("app.service_bootstrap.TradeEngine", return_value=fake):
            with self.assertRaises(RuntimeError):
                await registry.get(1)
        self.assertEqual(registry.engines, {})
        fake.close.assert_awaited_once()

    async def test_slow_market_calls_do_not_block_event_loop(self):
        worker = MarketDataWorker(4)
        task = asyncio.create_task(worker.submit(time.sleep, .15))
        started = time.perf_counter()
        await asyncio.sleep(.02)
        self.assertLess(time.perf_counter() - started, .12)
        self.assertFalse(task.done())
        await task

    async def test_email_failure_never_discloses_otp(self):
        store = InMemoryStore()
        with patch("app.auth.email_service.send_otp", return_value=False):
            result = await AuthService(store).register_or_login("test@example.com")
        self.assertEqual(result["status"], "error")
        self.assertNotIn("otp_code", result)
        self.assertIsNone(await store.get_otp("test@example.com"))

    def test_otp_allows_third_attempt_but_not_fourth(self):
        otp = OTP.create("test@example.com")
        self.assertFalse(otp.verify("wrong"))
        self.assertFalse(otp.verify("wrong"))
        self.assertTrue(otp.verify(otp.code))
        self.assertFalse(otp.verify(otp.code))
