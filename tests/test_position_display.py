import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.memory_store import InMemoryStore
from app.redis_store import RedisStore, k_closed_positions


class PositionArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_trades_survive_same_symbol_reentry_and_are_unique(self):
        import fakeredis.aioredis
        redis_store = RedisStore('redis://localhost/15')
        await redis_store.redis.aclose()
        redis_store.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(redis_store.close)
        for store in (InMemoryStore(), redis_store):
            with self.subTest(store=type(store).__name__):
                closed = dict(symbol='APOLLO', trade_id='first', status='CLOSED', qty=0, pnl=2.4)
                await store.upsert_position(1, 'APOLLO', closed)
                await store.upsert_position(1, 'APOLLO', closed)
                await store.upsert_position(1, 'APOLLO', dict(closed, trade_id='second', status='OPEN', qty=1, pnl=0))
                self.assertEqual(len(await store.list_positions(1)), 1)
                self.assertEqual((await store.get_position(1, 'APOLLO'))['trade_id'], 'second')
                self.assertEqual(await store.list_closed_positions(1), [closed])
                await store.upsert_position(1, 'APOLLO', dict(closed, trade_id='second', pnl=3))
                self.assertEqual(len(await store.list_closed_positions(1)), 2)
                self.assertEqual(await store.list_closed_positions(2), [])
                with patch('app.redis_store.now_ist_date', return_value='20990101'), \
                     patch('app.memory_store.now_ist_date', return_value='20990101'):
                    self.assertEqual(await store.list_closed_positions(1), [])
        self.assertGreater(await redis_store.redis.ttl(k_closed_positions(1)), 0)

    async def test_closed_archive_cannot_be_reopened_by_stale_update(self):
        store = InMemoryStore()
        closed = dict(symbol='APOLLO', trade_id='first', status='CLOSED', qty=0)
        await store.upsert_position(1, 'APOLLO', closed)
        with self.assertRaisesRegex(RuntimeError, 'STALE_POSITION_REOPEN_BLOCKED'):
            await store.upsert_position(1, 'APOLLO', dict(closed, status='OPEN', qty=1))
        self.assertEqual(await store.list_closed_positions(1), [closed])


class PositionApiTests(unittest.TestCase):
    def test_api_returns_current_and_closed_separately(self):
        from app import main
        with patch.object(main, '_is_test_mode', return_value=True), \
             patch.object(main, '_admin_auth_enabled', return_value=False), TestClient(main.app) as client:
            async def seed():
                await main.store.upsert_position(1, 'APOLLO', dict(symbol='APOLLO', trade_id='first', status='CLOSED'))
                await main.store.upsert_position(1, 'APOLLO', dict(symbol='APOLLO', trade_id='second', status='OPEN'))
            client.portal.call(seed)
            data = client.get('/api/positions').json()
            self.assertEqual(data['positions'][0]['trade_id'], 'second')
            self.assertEqual(data['closed_positions'][0]['trade_id'], 'first')


class AutoSquareOffTests(unittest.IsolatedAsyncioTestCase):
    async def test_cutoff_blocks_mis_entries_and_pyramid_but_not_cnc(self):
        from app.auto_squareoff import squareoff_cutoff_reached
        from app.trade_engine import TradeEngine
        self.assertFalse(squareoff_cutoff_reached(datetime(2026, 9, 28, 15, 9, 59)))
        self.assertTrue(squareoff_cutoff_reached(datetime(2026, 9, 28, 15, 10)))
        store = InMemoryStore()
        await store.set_auto_sq_off_enabled(1, True)
        await store.save_alert_config(1, dict(alert_name='cutoff', product='MIS', enabled=True))
        engine = TradeEngine(1, store)
        self.addAsyncCleanup(engine.close)
        engine._place_order_with_execution = AsyncMock()
        with patch('app.auto_squareoff.squareoff_cutoff_reached', return_value=True):
            for paper in (True, False):
                await store.save_alert_config(1, dict(alert_name='cutoff', product='MIS', enabled=True, paper_trading=paper))
                result = await engine.on_chartink_alert('cutoff', ['APOLLO'])
                self.assertEqual(result[0]['reason'], 'AUTO_SQ_OFF_CUTOFF')
            self.assertFalse(await engine._auto_squareoff_blocks_entry('CNC'))
            pos = SimpleNamespace(symbol='APOLLO', pyramid_enabled=True, pyramid_step_pct=1,
                pyramid_base_qty=1, pyramid_add_count=0, pyramid_max_adds=3, status='OPEN',
                pyramid_last_add_price=100, entry_price=100, tp3_price=0, target_price=110,
                trail_price=0, sl_price=95, side='BUY', product='MIS')
            self.assertFalse(await engine._maybe_pyramid_position(pos, 102))
            await store.set_auto_sq_off_enabled(1, False)
            self.assertFalse(await engine._auto_squareoff_blocks_entry('MIS'))
        engine._place_order_with_execution.assert_not_awaited()

    async def test_time_boundary_enabled_and_once_daily_guard(self):
        from app import main
        for hour, minute, enabled, ran, expected in (
            (15, 9, True, False, False), (15, 10, True, False, True),
            (15, 11, True, False, True), (15, 10, False, False, False),
            (15, 10, True, True, False), (14, 10, True, False, False),
        ):
            with self.subTest(hour=hour, minute=minute, enabled=enabled, ran=ran):
                store = SimpleNamespace(is_auto_sq_off_enabled=AsyncMock(return_value=enabled),
                                        has_auto_sq_off_run=AsyncMock(return_value=ran),
                                        mark_auto_sq_off_run=AsyncMock())
                engine = SimpleNamespace(exit_all_open_positions=AsyncMock(return_value=1))
                with patch.object(main, 'store', store), \
                     patch.object(main, 'ensure_engine', new=AsyncMock(return_value=engine)), \
                     patch.object(main.datetime, 'datetime') as clock, \
                     patch.object(main.asyncio, 'sleep', new=AsyncMock(side_effect=[None, asyncio.CancelledError()])), \
                     patch.object(main.ws_mgr, 'broadcast_nowait'):
                    clock.now.return_value = datetime(2026, 9, 28, hour, minute)
                    with self.assertRaises(asyncio.CancelledError):
                        await main.schedule_auto_squareoff()
                if expected:
                    engine.exit_all_open_positions.assert_awaited_once_with(reason='AUTO_SQ_OFF_310', products={'MIS'})
                    store.mark_auto_sq_off_run.assert_awaited_once_with(1)
                else:
                    engine.exit_all_open_positions.assert_not_awaited()
                    store.mark_auto_sq_off_run.assert_not_awaited()
