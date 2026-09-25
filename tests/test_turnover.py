import asyncio
import struct
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.alert_features import IST, prepare_features
from app.dhan_broker import DhanFeedService, MarketFeed, _QueuedDhanMarketFeed
from app.memory_store import InMemoryStore
from app.turnover import TurnoverBook, equity_universe, exclusions, turnover_decision
from app.turnover_service import TurnoverSession, rest_trade_epoch
from app.turnover_service import LEASE_KEY, PUBLISH, RELEASE, RENEW, run_worker
from app.market_quote_budget import reserve_quote_slot
from app.redis_store import RedisStore

NOW = datetime(2026, 9, 24, 9, 20, tzinfo=IST).timestamp()


class TurnoverTests(unittest.TestCase):
    def setUp(self):
        self.book = TurnoverBook({"AAA": "101", "BBB": "102", "CCC": "103"}, NOW)
        self.book.connected = True

    def seed(self):
        self.book.update("101", 100, 1000, NOW, trade_at=NOW, now=NOW)
        self.book.update("102", 20, 10000, NOW, trade_at=NOW, now=NOW)
        self.book.update("103", 500, 100, NOW, trade_at=NOW, now=NOW)
        return self.book.snapshot(NOW)

    def test_uses_day_volume_not_price_gains_or_last_quantity(self):
        snapshot = self.seed()
        self.assertTrue(snapshot["ready"])
        self.assertEqual([r["symbol"] for r in snapshot["rows"]], ["BBB", "AAA", "CCC"])
        self.assertEqual(snapshot["rows"][0]["turnover"], 200000)
        self.assertTrue(turnover_decision(snapshot, "BBB", 1, NOW)[0])
        self.assertEqual(turnover_decision(snapshot, "AAA", 1, NOW)[1], "TURNOVER_FILTER")

    def test_cumulative_volume_replaces_not_sums(self):
        self.seed()
        self.book.update("102", 25, 11000, NOW + 1, trade_at=NOW + 1, now=NOW + 1)
        self.assertEqual(self.book.quotes["BBB"]["turnover"], 275000)

    def test_price_change_revalues_entire_day_volume(self):
        self.seed()
        self.book.update("102", 30, 10000, NOW + 1, trade_at=NOW, now=NOW + 1)
        self.assertEqual(self.book.quotes["BBB"]["turnover"], 300000)

    def test_incomplete_universe_blocks_ranking(self):
        self.book.update("101", 100, 1000, NOW, trade_at=NOW, now=NOW)
        self.assertFalse(self.book.snapshot(NOW)["ready"])
        self.assertEqual(turnover_decision(self.book.snapshot(NOW), "AAA", 10, NOW)[1], "TURNOVER_RANK_NOT_READY")

    def test_stale_global_snapshot_blocks(self):
        self.assertEqual(turnover_decision(self.seed(), "BBB", 1, NOW + 11)[1], "TURNOVER_RANK_STALE")

    def test_stale_individual_quote_blocks_whole_ranking(self):
        self.seed()
        snapshot = self.book.snapshot(NOW + 121)
        self.assertFalse(snapshot["ready"])
        self.assertEqual(snapshot["covered"], 0)

    def test_no_last_day_volume_leaks_into_new_day(self):
        self.seed()
        self.assertFalse(self.book.update("101", 100, 1, NOW + 86400, trade_at=NOW + 86400, now=NOW + 86400))
        self.assertEqual(self.book.quotes, {})
        self.assertFalse(self.book.snapshot(NOW + 86400)["ready"])

    def test_previous_day_nonzero_volume_not_accepted(self):
        self.assertFalse(self.book.update("101", 100, 1000, NOW, trade_at=NOW - 86400, now=NOW))

    def test_zero_day_volume_can_have_old_last_trade(self):
        self.assertTrue(self.book.update("101", 100, 0, NOW, trade_at=NOW - 86400, now=NOW))
        self.assertEqual(self.book.quotes["AAA"]["turnover"], 0)

    def test_invalid_numbers_unknown_ids_and_volume_regression_rejected(self):
        self.seed()
        for price, volume in [(float("nan"), 10), (100, -1), (100, 1.2), (float("inf"), 2), (0, 10)]:
            self.assertFalse(self.book.update("101", price, volume, NOW, trade_at=NOW, now=NOW))
        self.assertFalse(self.book.update("999", 1, 1, NOW, trade_at=NOW, now=NOW))
        self.assertFalse(self.book.update("101", 100, 999, NOW + 1, trade_at=NOW, now=NOW + 1))
        self.assertFalse(self.book.update("101", 100, 9999, NOW - 1, trade_at=NOW, now=NOW))

    def test_ties_deterministic_and_fno_excluded(self):
        self.seed()
        self.book.update("101", 200, 1000, NOW + 1, trade_at=NOW, now=NOW + 1)
        snapshot = self.book.snapshot(NOW + 1)
        self.assertEqual(snapshot["rows"][0]["symbol"], "AAA")
        self.assertEqual(turnover_decision(snapshot, "SBIN", 10, NOW + 1)[1], "TURNOVER_FNO_EXCLUDED")
        self.assertEqual(turnover_decision(snapshot, "UNKNOWN", 10, NOW + 1)[1], "TURNOVER_SYMBOL_NOT_IN_UNIVERSE")

    def test_disconnected_and_closed_session_not_ready(self):
        self.seed()
        self.book.connected = False
        self.assertEqual(self.book.snapshot(NOW)["reason"], "TURNOVER_FEED_DISCONNECTED")
        self.assertEqual(self.book.snapshot(NOW + 86400 * 2)["reason"], "TURNOVER_SESSION_CLOSED")

    def test_master_filters_exchange_segment_instrument_and_exclusions(self):
        row = {"EXCH_ID": "NSE", "SEGMENT": "E", "INSTRUMENT": "EQUITY", "SYMBOL_NAME": "AAA", "SECURITY_ID": "101",
               "SERIES": "EQ", "INSTRUMENT_TYPE": "ES"}
        rows = [row, dict(row, EXCH_ID="BSE", SECURITY_ID="201", SYMBOL_NAME="BSESTOCK"),
                dict(row, SEGMENT="D", INSTRUMENT="FUTSTK", SYMBOL_NAME="FUTURE"),
                dict(row, INSTRUMENT="ETF", SYMBOL_NAME="ETF"),
                dict(row, INSTRUMENT_TYPE="ETF", SYMBOL_NAME="ETFEQ"),
                dict(row, INSTRUMENT_TYPE="DEB", SERIES="N1", SYMBOL_NAME="BOND"),
                dict(row, SYMBOL_NAME="SBIN", SECURITY_ID="3045"),
                dict(row, SERIES="SM", SYMBOL_NAME="SMESTOCK", SECURITY_ID="102")]
        self.assertEqual(equity_universe(rows), {"AAA": "101", "SMESTOCK": "102"})

    def test_compact_master_and_ambiguous_or_empty_master(self):
        row = {"SEM_EXM_EXCH_ID": "NSE", "SEM_SEGMENT": "E", "SEM_INSTRUMENT_NAME": "EQUITY",
               "SEM_TRADING_SYMBOL": "AAA", "SEM_SMST_SECURITY_ID": "101", "SEM_SERIES": "EQ", "SEM_EXCH_INSTRUMENT_TYPE": "ES"}
        self.assertEqual(equity_universe([row]), {"AAA": "101"})
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS"):
            equity_universe([row, dict(row, SEM_SMST_SECURITY_ID="102")])
        with self.assertRaisesRegex(ValueError, "INVALID"):
            equity_universe([])

    def test_supplied_exclusion_list_special_symbols(self):
        for symbol in ("M&M", "GVT&D", "360ONE", "SBIN", "TMPV", "ZYDUSLIFE"):
            self.assertIn(symbol, exclusions())

    def test_settings_default_live_and_turnover_off(self):
        value = prepare_features({}, {}, InMemoryStore())
        self.assertFalse(value["paper_trading"])
        self.assertFalse(value["turnover_filter_on"])
        self.assertEqual(value["turnover_top_n"], 10)
        for n in (0, -1, 0.1, 5001, "nan", None):
            with self.assertRaisesRegex(ValueError, "TURNOVER_TOP_N_INVALID"):
                prepare_features({"turnover_top_n": n}, {}, InMemoryStore())
        with self.assertRaisesRegex(ValueError, "TRADING_MODE_SETTINGS_INVALID"):
            prepare_features({"paper_trading": "tru"}, {}, InMemoryStore())

    def test_rest_date_parser(self):
        self.assertEqual(rest_trade_epoch("24/09/2026 09:20:00"), NOW)
        self.assertIsNone(rest_trade_epoch("bad"))

    def test_sdk_quote_preserves_epoch_for_day_validation(self):
        feed = object.__new__(_QueuedDhanMarketFeed)
        packet = struct.pack("<BHBIfHIfIIIffff", 4, 50, 1, 101, 100, 1, int(NOW), 100, 1000, 20, 30,
                             99, 98, 101, 97)
        decoded = feed.process_quote(packet)
        self.assertEqual(decoded["volume"], 1000)
        self.assertEqual(decoded["exchange_ts"], NOW)

    def test_sdk_full_packet_still_preserves_depth_and_volume(self):
        feed = object.__new__(_QueuedDhanMarketFeed)
        packet = struct.pack("<BHBIfHIfIIIIIIffff100s", 8, 162, 1, 101, 100, 1, int(NOW), 100,
                             1000, 20, 30, 0, 0, 0, 99, 98, 101, 97, bytes(100))
        decoded = feed.process_full(packet)
        self.assertEqual(decoded["volume"], 1000)
        self.assertEqual(decoded["exchange_ts"], NOW)
        self.assertEqual(len(decoded["depth"]), 5)


class TurnoverFeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_quote_feed_has_no_order_websocket(self):
        started = asyncio.Event()
        async def receiver(feed):
            feed.delivery_loop.call_soon_threadsafe(started.set)
            await asyncio.Event().wait()
        service = DhanFeedService(1, "client", "token", lambda _: None, lambda _: None,
                                  data_mode=MarketFeed.Quote, include_order_updates=False)
        with patch.object(_QueuedDhanMarketFeed, "_run_async", receiver), \
             patch("app.dhan_broker._SafeDhanOrderUpdate") as orders:
            try:
                await service.start([(1, "101"), (1, "102")])
                await asyncio.wait_for(started.wait(), 2)
                self.assertEqual(service.feed.instruments, [(1, "101", 17), (1, "102", 17)])
                self.assertIsNone(service.order_task)
                orders.assert_not_called()
            finally:
                await service.stop()

    async def test_session_updates_volume_not_ltq_and_resets_on_disconnect(self):
        session = TurnoverSession(1, {}, {"AAA": "101"}, None)
        session.book = TurnoverBook({"AAA": "101"}, NOW)
        session.on_state(True)
        with patch("app.turnover.time.time", return_value=NOW):
            session.on_tick({"exchange_segment": 1, "security_id": 101, "LTP": 100,
                             "volume": 1000, "LTQ": 1, "received_at": NOW, "exchange_ts": NOW})
        self.assertEqual(session.book.quotes["AAA"]["turnover"], 100000)
        session.on_state(False)
        self.assertEqual(session.book.quotes, {})

    async def test_rest_bootstrap_is_batched_and_accepts_explicit_zero_day_volume(self):
        calls = []
        received = asyncio.Event()
        def handler(request):
            import json
            ids = json.loads(request.content)["NSE_EQ"]
            calls.append(ids)
            rows = {str(sid): {"last_price": 100, "volume": 0,
                              "last_trade_time": "01/01/1980 00:00:00"} for sid in ids}
            if len(calls) == 2:
                received.set()
            return httpx.Response(200, json={"status": "success", "data": {"NSE_EQ": rows}})
        universe = {f"TEST{i}": str(i) for i in range(1, 1002)}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            session = TurnoverSession(1, {"client_id": "id", "access_token": "test"}, universe, client)
            session.book = TurnoverBook(universe, NOW)
            session.book.connected = True
            with patch("app.turnover.time.time", return_value=NOW), \
                 patch("app.turnover_service.equity_session_open", return_value=True):
                task = asyncio.create_task(session.refresh_quotes())
                try:
                    await asyncio.wait_for(received.wait(), 5)
                    await asyncio.sleep(0)
                    self.assertEqual([len(ids) for ids in calls], [1000, 1])
                    self.assertTrue(session.book.snapshot(NOW)["ready"])
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


class TurnoverRedisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import fakeredis.aioredis
        self.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.store = RedisStore("redis://localhost/15")
        await self.store.redis.aclose()
        self.store.redis = self.redis

    async def asyncTearDown(self):
        await self.store.close()

    async def test_snapshot_user_isolation_and_expiry(self):
        await self.store.save_turnover_snapshot(1, {"ready": True, "ts": NOW})
        self.assertTrue((await self.store.load_turnover_snapshot(1))["ready"])
        self.assertEqual(await self.store.load_turnover_snapshot(2), {})
        self.assertGreater(await self.redis.ttl("turnover:snapshot:1"), 0)
        self.assertLessEqual(await self.redis.ttl("turnover:snapshot:1"), 15)
        await self.redis.delete("turnover:snapshot:1")
        self.assertEqual(await self.store.load_turnover_snapshot(1), {})

    async def test_worker_lease_blocks_stale_owner_publish_and_release(self):
        await self.redis.set(LEASE_KEY, "owner", ex=30)
        self.assertEqual(await self.redis.eval(RENEW, 1, LEASE_KEY, "wrong"), 0)
        self.assertEqual(await self.redis.eval(PUBLISH, 2, LEASE_KEY, "turnover:snapshot:1", "wrong", "{}"), 0)
        self.assertEqual(await self.redis.eval(RELEASE, 1, LEASE_KEY, "wrong"), 0)
        self.assertEqual(await self.redis.eval(RENEW, 1, LEASE_KEY, "owner"), 1)
        self.assertEqual(await self.redis.eval(PUBLISH, 2, LEASE_KEY, "turnover:snapshot:1", "owner", "{}"), 1)
        self.assertEqual(await self.redis.eval(RELEASE, 1, LEASE_KEY, "owner"), 1)

    async def test_duplicate_worker_never_opens_feed(self):
        await self.redis.set(LEASE_KEY, "other", ex=30)
        client = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "ALREADY_RUNNING"):
            await run_worker(self.store, client)
        client.get.assert_not_awaited()

    async def test_worker_config_master_snapshot_and_graceful_shutdown(self):
        self.store.list_alert_configs = AsyncMock(return_value={"test": {"enabled": True, "turnover_filter_on": True}})
        self.store.load_broker = AsyncMock(return_value="DHAN")
        self.store.load_dhan_credentials = AsyncMock(return_value={"client_id": "id", "access_token": "test"})
        csv = ("SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SMST_SECURITY_ID,SEM_SERIES,SEM_EXCH_INSTRUMENT_TYPE\n"
               "NSE,E,EQUITY,TEST,101,EQ,ES\n")
        response = httpx.Response(200, text=csv, request=httpx.Request("GET", "https://example.test/master"))
        client = SimpleNamespace(get=AsyncMock(return_value=response))
        stopped = []
        async def start(session):
            session.book.connected = True
        async def stop(session):
            stopped.append(session.uid)
        with patch("app.turnover_service.load_user_ids", AsyncMock(return_value=[1])), \
             patch("app.turnover_service.equity_session_open", return_value=True), \
             patch.object(TurnoverSession, "start", start), patch.object(TurnoverSession, "stop", stop):
            task = asyncio.create_task(run_worker(self.store, client))
            try:
                for _ in range(100):
                    payload = await self.store.load_turnover_snapshot(1)
                    if payload:
                        break
                    if task.done():
                        await task
                    await asyncio.sleep(0.02)
                self.assertEqual(payload["total"], 1)
                client.get.assert_awaited_once()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(stopped, [1])
        self.assertEqual((await self.store.load_turnover_snapshot(1))["reason"], "TURNOVER_STOPPED")
        self.assertIsNone(await self.redis.get(LEASE_KEY))

    async def test_quote_budget_shared_across_workers_and_gives_foreground_priority(self):
        self.assertTrue(await reserve_quote_slot(self.store, 1, background=True))
        self.assertFalse(await reserve_quote_slot(self.store, 1, background=True))
        await self.redis.delete("dhan:quote-budget:1")
        self.assertTrue(await reserve_quote_slot(self.store, 1))
        await self.redis.delete("dhan:quote-budget:1")
        self.assertFalse(await reserve_quote_slot(self.store, 1, background=True))
        self.assertTrue(await reserve_quote_slot(self.store, 2, background=True))

    async def test_shared_budget_is_atomic_under_concurrent_background_requests(self):
        results = await asyncio.gather(*(reserve_quote_slot(self.store, 1, background=True) for _ in range(10)))
        self.assertEqual(sum(results), 1)


class TurnoverApiTests(unittest.TestCase):
    def test_configuration_roundtrip_and_authenticated_ranking(self):
        from app import main
        with patch.object(main, "_is_test_mode", return_value=True), \
             patch.object(main, "_admin_auth_enabled", return_value=False), TestClient(main.app) as client:
            response = client.post("/api/alert-config", json={"alert_name": "Paper Turnover", "paper_trading": True,
                "turnover_filter_on": True, "turnover_top_n": 10, "qty_mode": "QTY", "qty": 1})
            self.assertEqual(response.json()["status"], "saved", response.text)
            cfg = client.get("/api/alert-config?user_id=1").json()["configs"]["paper turnover"]
            self.assertTrue(cfg["paper_trading"])
            self.assertTrue(cfg["turnover_filter_on"])
            self.assertEqual(cfg["turnover_top_n"], 10)
            self.assertFalse(client.get("/api/turnover/top").json()["ready"])
            self.assertEqual(client.get("/api/turnover/top?limit=5001").status_code, 422)
            with patch.object(main, "_admin_auth_enabled", return_value=True):
                self.assertEqual(client.get("/api/turnover/top").status_code, 401)
