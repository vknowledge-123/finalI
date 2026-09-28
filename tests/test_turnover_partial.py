import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx

from app.alert_features import IST
from app.turnover import TurnoverBook, candidate_turnover_decision, turnover_decision
from app.turnover_service import TurnoverSession

NOW = datetime(2026, 9, 28, 9, 16, tzinfo=IST).timestamp()


def book_with_missing(*, allow_partial=True):
    book = TurnoverBook({f"STOCK{i}": str(i) for i in range(1, 101)}, NOW,
                        allow_partial=allow_partial, min_coverage_pct=99)
    book.connected = True
    for i in range(1, 100):
        book.update(str(i), 100, i, NOW, trade_at=NOW, now=NOW, source="DHAN_WS")
    return book


class PartialRankingTests(unittest.TestCase):
    def test_99_percent_allows_fresh_candidate_with_explicit_scope(self):
        snapshot = book_with_missing().snapshot(NOW)
        self.assertTrue(snapshot["ready"])
        self.assertEqual(snapshot["state"], "PARTIAL")
        self.assertEqual(snapshot["coverage_pct"], 99)
        self.assertEqual(snapshot["ranking_scope"], "OBSERVED_STOCKS")
        self.assertEqual(snapshot["missing"], [{"symbol": "STOCK100", "security_id": "100",
            "reason": "QUOTE_MISSING", "previously_observed": False}])
        self.assertTrue(turnover_decision(snapshot, "STOCK99", 10, NOW)[0])
        self.assertEqual(turnover_decision(snapshot, "STOCK100", 10, NOW)[1], "TURNOVER_QUOTE_MISSING")

    def test_real_morning_counts_are_usable_without_last_stock(self):
        book = TurnoverBook({f"STOCK{i}": str(i) for i in range(2961)}, NOW,
                            allow_partial=True, min_coverage_pct=99)
        book.connected = True
        for i in range(2953):
            book.update(str(i), 100, i, NOW, trade_at=NOW, now=NOW)
        self.assertEqual(book.snapshot(NOW)["state"], "PARTIAL")

    def test_strict_policy_and_insufficient_coverage_block(self):
        self.assertFalse(book_with_missing(allow_partial=False).snapshot(NOW)["ready"])
        book = book_with_missing()
        del book.quotes["STOCK1"]
        snapshot = book.snapshot(NOW)
        self.assertEqual(snapshot["state"], "WARMING_UP")
        self.assertFalse(snapshot["ready"])

    def test_missing_recovery_reorders_without_changing_existing_positions(self):
        book = book_with_missing()
        book.update("100", 100, 1000, NOW, trade_at=NOW, now=NOW, source="DHAN_REST")
        snapshot = book.snapshot(NOW)
        self.assertEqual(snapshot["state"], "COMPLETE")
        self.assertEqual(snapshot["missing_count"], 0)
        self.assertEqual(snapshot["rows"][0]["symbol"], "STOCK100")
        self.assertEqual(snapshot["rows"][0]["source"], "DHAN_REST")
        self.assertFalse(turnover_decision(snapshot, "STOCK99", 1, NOW)[0])

    def test_stale_leader_cannot_disappear_and_promote_other_stock(self):
        book = book_with_missing()
        book.update("100", 100, 1000, NOW, trade_at=NOW, now=NOW)
        for i in range(1, 100):
            book.update(str(i), 100, i, NOW + 121, trade_at=NOW, now=NOW + 121)
        snapshot = book.snapshot(NOW + 121)
        self.assertTrue(snapshot["ready"])
        self.assertEqual(snapshot["stale_count"], 1)
        self.assertEqual(snapshot["rows"][0]["symbol"], "STOCK100")
        self.assertTrue(snapshot["rows"][0]["stale"])
        self.assertEqual(turnover_decision(snapshot, "STOCK99", 1, NOW + 121)[1], "TURNOVER_FILTER")
        self.assertEqual(turnover_decision(snapshot, "STOCK99", 2, NOW + 121)[1], "TURNOVER_TOP_N_STALE")
        self.assertEqual(turnover_decision(snapshot, "STOCK100", 2, NOW + 121)[1], "TURNOVER_RANK_STALE")

    def test_stale_low_rank_does_not_block_fresh_leaders(self):
        book = book_with_missing()
        book.update("100", 100, 100, NOW, trade_at=NOW, now=NOW)
        for i in range(2, 101):
            book.update(str(i), 100, i, NOW + 121, trade_at=NOW, now=NOW + 121)
        self.assertTrue(turnover_decision(book.snapshot(NOW + 121), "STOCK100", 10, NOW + 121)[0])

    def test_rejected_quotes_record_reasons_not_zero_turnover(self):
        book = book_with_missing()
        book.update("100", 100, 5000, NOW, trade_at=NOW - 86400, now=NOW)
        snapshot = book.snapshot(NOW)
        self.assertEqual(snapshot["missing"][0]["reason"], "INVALID_TRADE_DAY")
        self.assertNotIn("STOCK100", book.quotes)

    def test_configuration_validation_and_session_guards(self):
        for pct in (0, 101, float("nan"), float("inf")):
            with self.assertRaisesRegex(ValueError, "COVERAGE"):
                TurnoverBook({"A": "1"}, NOW, min_coverage_pct=pct)
        with self.assertRaisesRegex(ValueError, "ALLOW_PARTIAL"):
            TurnoverBook({"A": "1"}, NOW, allow_partial="typo")
        book = book_with_missing()
        book.connected = False
        self.assertEqual(book.snapshot(NOW)["state"], "UNAVAILABLE")
        self.assertFalse(turnover_decision(book.snapshot(NOW), "STOCK99", 10, NOW)[0])

    def test_coverage_is_rechecked_when_quotes_expire_between_snapshots(self):
        book = book_with_missing()
        book.update("99", 100, 1000, NOW + 119, trade_at=NOW, now=NOW + 119)
        snapshot = book.snapshot(NOW + 119)
        self.assertTrue(snapshot["ready"])
        self.assertEqual(turnover_decision(snapshot, "STOCK99", 1, NOW + 121)[1], "TURNOVER_RANK_NOT_READY")


class TargetedRetryTests(unittest.IsolatedAsyncioTestCase):
    async def make_session(self, handler, universe=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        universe = universe or {"AAA": "1", "BBB": "2"}
        session = TurnoverSession(1, {"client_id": "test", "access_token": "test"}, universe, client)
        session.book = TurnoverBook(universe, NOW)
        session.book.connected = True
        return session

    async def test_retries_only_missing_ids_and_backoff_then_recovery(self):
        calls = []
        def handler(request):
            ids = json.loads(request.content)["NSE_EQ"]
            calls.append(ids)
            rows = {"1": {"last_price": 100, "volume": 1, "last_trade_time": "28/09/2026 09:16:00"}}
            if len(calls) >= 5:
                rows["2"] = dict(rows["1"])
            return httpx.Response(200, json={"status": "success", "data": {"NSE_EQ": rows}})
        session = await self.make_session(handler)
        with patch("app.turnover.time.time", return_value=NOW) as clock, \
             patch("app.turnover_service.equity_session_open", return_value=True), \
             patch("app.turnover_service.asyncio.sleep", new=AsyncMock()):
            await session.refresh_once()
            self.assertEqual(calls, [[1, 2]])
            self.assertEqual(session.retries["2"], {"attempts": 1, "due": NOW + 5})
            clock.return_value = NOW + 4
            await session.refresh_once()
            self.assertEqual(len(calls), 1)
            for stamp, attempt, delay in [(5, 2, 10), (15, 3, 20), (35, 4, 60)]:
                clock.return_value = NOW + stamp
                await session.refresh_once()
                self.assertEqual(calls[-1], [2])
                self.assertEqual(session.retries["2"], {"attempts": attempt, "due": NOW + stamp + delay})
            clock.return_value = NOW + 95
            await session.refresh_once()
            self.assertNotIn("2", session.retries)
            self.assertEqual(session.book.snapshot(NOW + 95)["state"], "COMPLETE")

    async def test_throttle_prevents_repeated_calls(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(429)
        session = await self.make_session(handler)
        with patch("app.turnover.time.time", return_value=NOW) as clock, \
             patch("app.turnover_service.equity_session_open", return_value=True):
            await session.refresh_once()
            clock.return_value = NOW + 59
            await session.refresh_once()
            self.assertEqual(len(calls), 1)
            self.assertFalse(session.book.quotes)

    async def test_slot_denial_does_not_drop_batch_or_increment_retry(self):
        session = await self.make_session(lambda _: httpx.Response(500))
        with patch("app.turnover.time.time", return_value=NOW), \
             patch("app.turnover_service.equity_session_open", return_value=True), \
             patch("app.turnover_service.reserve_quote_slot", new=AsyncMock(return_value=False)):
            await session.refresh_once()
        self.assertEqual(session.next_refresh, {})
        self.assertEqual(session.retries, {})

    async def test_disconnect_during_http_response_cannot_repopulate_book(self):
        session = None
        def handler(request):
            session.on_state(False)
            return httpx.Response(200, json={"status": "success", "data": {"NSE_EQ": {
                "1": {"last_price": 100, "volume": 1, "last_trade_time": "28/09/2026 09:16:00"}}}})
        session = await self.make_session(handler)
        with patch("app.turnover.time.time", return_value=NOW), \
             patch("app.turnover_service.equity_session_open", return_value=True):
            await session.refresh_once()
        self.assertFalse(session.book.quotes)

    async def test_websocket_recovers_pending_quote_without_rest(self):
        session = await self.make_session(lambda _: httpx.Response(500))
        session.schedule_retry("1", NOW)
        with patch("app.turnover.time.time", return_value=NOW):
            session.on_tick({"exchange_segment": 1, "security_id": "1", "LTP": 100,
                             "volume": 1, "received_at": NOW, "exchange_ts": NOW})
        self.assertNotIn("1", session.retries)
        self.assertEqual(session.book.quotes["AAA"]["source"], "DHAN_WS")

    async def test_request_failure_is_retried_without_fake_quote(self):
        session = await self.make_session(lambda _: httpx.Response(500))
        with patch("app.turnover.time.time", return_value=NOW), \
             patch("app.turnover_service.equity_session_open", return_value=True):
            await session.refresh_once()
        self.assertEqual(session.retries["1"]["due"], NOW + 5)
        self.assertEqual(session.book.pending(NOW)[0]["reason"], "REST_REQUEST_FAILED")
        self.assertFalse(session.book.quotes)


class CandidateRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import fakeredis.aioredis
        from app.redis_store import RedisStore
        self.store = RedisStore("redis://localhost/15")
        await self.store.redis.aclose()
        self.store.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(self.store.close)

    async def test_missing_candidate_requests_worker_then_reevaluates(self):
        book = book_with_missing()
        initial = book.snapshot(NOW)
        book.update("100", 100, 1000, NOW, trade_at=NOW, now=NOW)
        await self.store.save_turnover_snapshot(1, book.snapshot(NOW))
        with patch("app.turnover.time.time", return_value=NOW):
            result = await candidate_turnover_decision(self.store, 1, initial, "STOCK100", 10, wait_seconds=1)
            self.assertTrue(result[0])
            self.assertEqual(await self.store.redis.zrange("turnover:refresh:1", 0, -1), ["STOCK100"])
            self.assertGreater(await self.store.redis.ttl("turnover:refresh:1"), 0)
            self.assertEqual(await self.store.redis.zrange("turnover:refresh:2", 0, -1), [])

    async def test_candidate_timeout_never_bypasses_ranking(self):
        initial = book_with_missing().snapshot(NOW)
        with patch("app.turnover.time.time", return_value=NOW):
            result = await candidate_turnover_decision(self.store, 1, initial, "STOCK100", 10, wait_seconds=0)
        self.assertEqual(result, (False, "TURNOVER_QUOTE_MISSING"))

    async def test_unknown_stock_does_not_create_refresh_request(self):
        with patch("app.turnover.time.time", return_value=NOW):
            result = await candidate_turnover_decision(self.store, 1, book_with_missing().snapshot(NOW), "UNKNOWN", 10)
        self.assertEqual(result[1], "TURNOVER_SYMBOL_NOT_IN_UNIVERSE")
        self.assertFalse(await self.store.redis.exists("turnover:refresh:1"))

    async def test_slow_redis_does_not_extend_candidate_deadline(self):
        async def stalled_load(_):
            await asyncio.Event().wait()
        initial = book_with_missing().snapshot(NOW)
        with patch("app.turnover.time.time", return_value=NOW), \
             patch.object(self.store, "load_turnover_snapshot", side_effect=stalled_load):
            result = await asyncio.wait_for(candidate_turnover_decision(
                self.store, 1, initial, "STOCK100", 10, wait_seconds=0.6), timeout=1.5)
        self.assertFalse(result[0])

    async def test_worker_consumes_candidate_request_and_recovers_snapshot(self):
        book = book_with_missing()
        calls = []
        def handler(request):
            ids = json.loads(request.content)["NSE_EQ"]
            calls.append(ids)
            return httpx.Response(200, json={"status": "success", "data": {"NSE_EQ": {
                "100": {"last_price": 100, "volume": 1000, "last_trade_time": "28/09/2026 09:16:00"}}}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            session = TurnoverSession(1, {"client_id": "id", "access_token": "test"}, book.universe, client, self.store)
            session.book = book
            session.next_refresh = {sid: NOW + 45 for sid in book.universe.values()}
            with patch("app.turnover.time.time", return_value=NOW), \
                 patch("app.turnover_service.equity_session_open", return_value=True):
                await self.store.save_turnover_snapshot(1, book.snapshot(NOW))
                task = asyncio.create_task(candidate_turnover_decision(
                    self.store, 1, book.snapshot(NOW), "STOCK100", 10, wait_seconds=4))
                try:
                    for _ in range(20):
                        if await self.store.redis.exists("turnover:refresh:1"):
                            break
                        await asyncio.sleep(0.01)
                    await session.refresh_once()
                    await self.store.save_turnover_snapshot(1, book.snapshot(NOW))
                    self.assertTrue((await task)[0])
                    self.assertEqual(calls, [[100]])
                    self.assertEqual(await self.store.redis.zrange("turnover:refresh:1", 0, -1), [])
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


class PartialRankingApiTests(unittest.TestCase):
    def test_api_exposes_partial_diagnostics_and_stale_snapshot_is_unavailable(self):
        from fastapi.testclient import TestClient
        from app import main
        with patch.object(main, "_is_test_mode", return_value=True), \
             patch.object(main, "_admin_auth_enabled", return_value=False), TestClient(main.app) as client:
            snapshot = book_with_missing().snapshot(NOW)
            with patch.object(main.store, "load_turnover_snapshot", new=AsyncMock(return_value=snapshot)), \
                 patch("app.main.time.time", return_value=NOW):
                data = client.get("/api/turnover/top?user_id=1&limit=5").json()
                self.assertEqual(data["state"], "PARTIAL")
                self.assertEqual(data["missing_count"], 1)
                self.assertEqual(data["missing"][0]["symbol"], "STOCK100")
                self.assertEqual(len(data["rows"]), 5)
                self.assertEqual(data["rows"][0]["source"], "DHAN_WS")
            with patch.object(main.store, "load_turnover_snapshot", new=AsyncMock(return_value=snapshot)), \
                 patch("app.main.time.time", return_value=NOW + 11):
                data = client.get("/api/turnover/top").json()
                self.assertFalse(data["ready"])
                self.assertEqual(data["state"], "UNAVAILABLE")
