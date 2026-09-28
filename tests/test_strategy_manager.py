import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.memory_store import InMemoryStore
from app.redis_store import RedisStore, k_alert_cfg, k_alert_cfg_legacy


class StrategyManagerApiTests(unittest.TestCase):
    def setUp(self):
        from app import main
        self.main = main
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(main, '_is_test_mode', return_value=True))
        self.stack.enter_context(patch.object(main, '_admin_auth_enabled', return_value=False))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.payload = {'alert_name': 'Manager Test', 'qty_mode': 'QTY', 'qty': 1,
                        'target_pct': 2, 'stop_loss_pct': 1, 'config_action': 'create'}

    def post(self, **changes):
        return self.client.post('/api/alert-config', json={**self.payload, **changes}).json()

    def test_create_and_update_preserve_identity_and_other_strategies(self):
        first = self.post()
        self.assertEqual(first['status'], 'saved')
        self.assertNotIn('config_action', first['config'])
        self.assertNotIn('original_alert_name', first['config'])
        self.post(alert_name='Another Strategy')
        updated = self.post(config_action='update', original_alert_name='manager test',
                            qty=3, paper_trading=True, enabled=False)
        self.assertEqual(updated['status'], 'saved')
        configs = self.client.get('/api/alert-config').json()['configs']
        self.assertEqual(configs['manager test']['qty'], 3)
        self.assertFalse(configs['manager test']['enabled'])
        self.assertTrue(configs['manager test']['paper_trading'])
        self.assertEqual(configs['another strategy']['qty'], 1)

    def test_create_existing_cannot_overwrite(self):
        self.post()
        duplicate = self.post(qty=9)
        self.assertEqual(duplicate['error'], 'STRATEGY_ALREADY_EXISTS')
        self.assertEqual(self.client.get('/api/alert-config').json()['configs']['manager test']['qty'], 1)

    def test_deleted_strategy_cannot_be_recreated_by_edit(self):
        self.post()
        self.client.request('DELETE', '/api/alert-config', json={'alert_name': 'manager test'})
        self.assertEqual(self.post(config_action='update', original_alert_name='manager test')['error'], 'STRATEGY_NOT_FOUND')

    def test_edit_cannot_rename_or_change_identity(self):
        self.post()
        self.assertEqual(self.post(config_action='update', original_alert_name='different')['error'], 'STRATEGY_IDENTITY_MISMATCH')
        self.assertEqual(self.post(config_action='update')['error'], 'STRATEGY_IDENTITY_MISMATCH')

    def test_invalid_action_and_storage_failure_do_not_write(self):
        self.assertEqual(self.post(config_action='replace')['error'], 'CONFIG_ACTION_INVALID')
        with patch.object(self.main.store, 'get_alert_config', new=AsyncMock(side_effect=RuntimeError('offline'))), \
             patch.object(self.main.store, 'save_alert_config', new=AsyncMock()) as save:
            self.assertEqual(self.post()['error'], 'CONFIG_READ_FAILED')
            save.assert_not_awaited()

    def test_legacy_api_still_supports_existing_clients(self):
        payload = dict(self.payload)
        payload.pop('config_action')
        self.assertEqual(self.client.post('/api/alert-config', json=payload).json()['status'], 'saved')
        payload['qty'] = 2
        self.assertEqual(self.client.post('/api/alert-config', json=payload).json()['config']['qty'], 2)

    def test_validation_error_leaves_original_config_unchanged(self):
        self.post()
        failed = self.post(config_action='update', original_alert_name='manager test', target_pct=-1)
        self.assertIn('error', failed)
        self.assertEqual(self.client.get('/api/alert-config').json()['configs']['manager test']['target_pct'], 2)


class GuardedConfigStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import fakeredis.aioredis
        self.store = RedisStore('redis://localhost/15')
        await self.store.redis.aclose()
        self.store.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(self.store.close)

    async def test_concurrent_creates_only_one_wins(self):
        outcomes = await asyncio.gather(*(self.store.save_alert_config(
            1, {'alert_name': 'Concurrent', 'qty': quantity}, action='create')
            for quantity in range(1, 11)), return_exceptions=True)
        self.assertEqual(sum(result is None for result in outcomes), 1)
        self.assertEqual(sum(isinstance(result, ValueError) for result in outcomes), 9)

    async def test_guard_includes_legacy_hash_and_user_isolation(self):
        await self.store.set_alert_config(1, 'Legacy', {'qty': 1})
        await self.store.redis.delete(k_alert_cfg(1))
        self.assertTrue(await self.store.redis.hexists(k_alert_cfg_legacy(1), 'legacy'))
        with self.assertRaisesRegex(ValueError, 'ALREADY_EXISTS'):
            await self.store.save_alert_config(1, {'alert_name': 'Legacy'}, action='create')
        await self.store.save_alert_config(1, {'alert_name': 'Legacy', 'qty': 2}, action='update')
        await self.store.save_alert_config(2, {'alert_name': 'Legacy', 'qty': 3}, action='create')
        self.assertEqual((await self.store.get_alert_config(1, 'Legacy'))['qty'], 2)
        self.assertEqual((await self.store.get_alert_config(2, 'Legacy'))['qty'], 3)

    async def test_missing_update_and_invalid_action_fail_in_both_stores(self):
        for store in (self.store, InMemoryStore()):
            with self.assertRaisesRegex(ValueError, 'NOT_FOUND'):
                await store.save_alert_config(1, {'alert_name': 'Missing'}, action='update')
            with self.assertRaisesRegex(ValueError, 'ACTION_INVALID'):
                await store.save_alert_config(1, {'alert_name': 'Missing'}, action='unexpected')
