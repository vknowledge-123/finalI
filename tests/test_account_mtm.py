import asyncio
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.memory_store import InMemoryStore
from app.services.account_mtm import AccountMtmService, dhan_account_totals


class AccountTotalsTests(unittest.TestCase):
    def test_open_closed_and_external_positions_are_all_included(self):
        self.assertEqual(dhan_account_totals([
            {'positionType': 'CLOSED', 'netQty': 0, 'realizedProfit': '100.10', 'unrealizedProfit': 0},
            {'positionType': 'LONG', 'netQty': 5, 'realizedProfit': -20, 'unrealizedProfit': 50.2},
            {'tradingSymbol': 'MANUAL', 'realizedProfit': 0, 'unrealizedProfit': -10.3},
        ]), {'realized': 80.1, 'unrealized': 39.9, 'total': 120.0})
        self.assertEqual(dhan_account_totals([]), {'realized': 0.0, 'unrealized': 0.0, 'total': 0.0})

    def test_malformed_response_is_not_converted_to_zero(self):
        for rows in ({'status': 'failure'}, None, [{}], [None],
                     [{'realizedProfit': 1, 'unrealizedProfit': None}],
                     [{'realizedProfit': 'NaN', 'unrealizedProfit': 0}],
                     [{'realizedProfit': 0, 'unrealizedProfit': float('inf')}],
                     [{'realizedProfit': False, 'unrealizedProfit': 0}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                dhan_account_totals(rows)


class AccountMtmServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemoryStore()
        await self.store.save_broker(1, 'DHAN')
        await self.store.save_dhan_credentials(1, 'client1', 'test-token')
        self.calls = []
        self.reply = httpx.Response(200, json=[{'realizedProfit': 100, 'unrealizedProfit': 50}])

        async def handler(request):
            self.calls.append(request)
            await asyncio.sleep(0)
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.service = AccountMtmService(lambda: self.store, client)
        self.addAsyncCleanup(self.service.close)

    async def test_concurrent_reads_are_cached_and_get_only(self):
        results = await asyncio.gather(*(self.service.snapshot(1) for _ in range(10)))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(row['total'] == 150 and row['status'] == 'FRESH' for row in results))
        self.assertEqual(self.calls[0].method, 'GET')
        self.assertEqual(str(self.calls[0].url), 'https://api.dhan.co/v2/positions')
        self.assertEqual(self.calls[0].headers['access-token'], 'test-token')
        self.assertEqual(self.calls[0].headers['client-id'], 'client1')

    async def test_failure_keeps_last_good_value_but_marks_stale_and_recovers(self):
        first = await self.service.snapshot(1)
        self.service._cache[1]['retry_at'] = 0
        self.reply = httpx.Response(500, text='Internal Server Error')
        stale = await self.service.snapshot(1)
        self.assertEqual(stale['status'], 'STALE')
        self.assertFalse(stale['ok'])
        self.assertEqual(stale['total'], 150)
        self.assertEqual(stale['as_of'], first['as_of'])
        await self.service.snapshot(1)
        self.assertEqual(len(self.calls), 2)
        self.service._cache[1]['retry_at'] = 0
        self.reply = httpx.Response(200, json=[])
        self.assertEqual((await self.service.snapshot(1))['total'], 0)

    async def test_first_failure_returns_unavailable_not_zero(self):
        for reply in (httpx.Response(401, json={'error': 'expired'}),
                      httpx.Response(200, text='not json'),
                      httpx.Response(200, json=[{}]), httpx.ReadTimeout('timeout')):
            self.service._cache.clear()
            self.reply = reply
            data = await self.service.snapshot(1)
            self.assertEqual(data['status'], 'UNAVAILABLE')
            self.assertIsNone(data['total'])
            self.assertIsNone(data['as_of'])

    async def test_broker_credentials_day_and_user_isolation(self):
        await self.service.snapshot(1)
        await self.store.save_dhan_credentials(1, 'client2', 'new-token')
        self.reply = httpx.Response(500)
        self.assertEqual((await self.service.snapshot(1))['status'], 'UNAVAILABLE')
        self.assertEqual(self.calls[-1].headers['client-id'], 'client2')
        self.assertEqual((await self.service.snapshot(2))['status'], 'NOT_APPLICABLE')
        await self.store.save_broker(1, 'ZERODHA')
        self.assertIsNone((await self.service.snapshot(1))['total'])
        await self.store.save_broker(1, 'DHAN')
        await self.store.save_dhan_credentials(1, '', '')
        self.assertEqual((await self.service.snapshot(1))['status'], 'NOT_CONNECTED')
        self.assertEqual(len(self.calls), 2)
        await self.store.save_dhan_credentials(1, 'client1', 'test-token')
        self.reply = httpx.Response(200, json=[])
        await self.service.snapshot(1)
        with patch('app.services.account_mtm.now_ist_date', return_value='20990101'):
            await self.service.snapshot(1)
        self.assertEqual(len(self.calls), 4)


class AccountMtmApiTests(unittest.TestCase):
    def test_api_is_admin_protected_and_returns_read_only_snapshot(self):
        from app import main
        with patch.object(main, '_is_test_mode', return_value=True), \
             patch.object(main, '_admin_auth_enabled', return_value=True), TestClient(main.app) as client:
            self.assertEqual(client.get('/api/account-mtm').status_code, 401)
            with patch.object(main, '_admin_auth_enabled', return_value=False):
                self.assertEqual(client.get('/api/account-mtm').json()['status'], 'NOT_APPLICABLE')
                async def seed():
                    await main.store.save_broker(1, 'DHAN')
                    await main.store.save_dhan_credentials(1, 'client1', 'test-token')
                client.portal.call(seed)
                main.ACCOUNT_MTM.client = httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=[{'realizedProfit': 100, 'unrealizedProfit': 50}])))
                self.assertEqual(client.get('/api/account-mtm').json()['total'], 150)
                self.assertEqual(client.get('/api/positions').json()['positions'], [])
