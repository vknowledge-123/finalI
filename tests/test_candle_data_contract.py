import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.alert_features import IST
from app.dhan_broker import normalize_dhan_candles
from app.memory_store import InMemoryStore
from app.trade_engine import TradeEngine


class CandleResponseTests(unittest.TestCase):
    def test_failure_is_not_silently_treated_as_empty_candles(self):
        with self.assertRaisesRegex(RuntimeError, "DHAN_CANDLES_FAILED"):
            normalize_dhan_candles({"status": "failure", "remarks": {"error_code": "DH-904"}, "data": {}}, 1)

    def test_malformed_and_truncated_arrays_are_rejected(self):
        for data in (None, {}, {"open": [100], "high": [101], "low": [99], "close": [], "timestamp": [1000]}):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "DHAN_CANDLES_INVALID_RESPONSE"):
                normalize_dhan_candles({"status": "success", "data": data}, 1)

    def test_epoch_response_and_legitimate_empty_arrays(self):
        stamp = datetime.now(IST).replace(second=0, microsecond=0) - timedelta(minutes=2)
        data = {"open": [100], "high": [101], "low": [99], "close": [100.5], "timestamp": [stamp.timestamp()]}
        candles = normalize_dhan_candles({"status": "success", "data": data}, 1)
        self.assertEqual(candles[0]["date"], stamp)
        self.assertEqual(candles[0]["high"], 101)
        self.assertEqual(normalize_dhan_candles({"status": "success", "data": {key: [] for key in data}}, 1), [])


class CandleFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_breakout_dhan_fetches_one_current_session_not_previous_day(self):
        engine = TradeEngine(1, InMemoryStore())
        engine.broker = "DHAN"
        engine.dhan = SimpleNamespace(NSE="NSE_EQ")
        engine._ensure_dhan_ready = AsyncMock(return_value=True)
        engine._fetch_dhan_intraday_candles = AsyncMock(return_value=[])
        try:
            with patch("app.trade_engine.DHAN_INSTRUMENTS.security_id", AsyncMock(return_value="1333")):
                await engine._fetch_historical_candles("TEST", "1minute", 0)
            args = engine._fetch_dhan_intraday_candles.await_args.args
            self.assertEqual(args[2].date(), args[3].date())
            self.assertEqual((args[2].hour, args[2].minute), (9, 15))
        finally:
            await engine.close()

    async def test_candle_pacing_does_not_hold_quote_worker(self):
        engine = TradeEngine(1, InMemoryStore())
        engine.market_data_worker.submit = AsyncMock(return_value=[])
        try:
            with patch("app.trade_engine.time.monotonic", return_value=100), patch("app.trade_engine.asyncio.sleep", AsyncMock()) as sleep:
                await engine._submit_history(lambda: [], security_id="1")
                await engine._submit_history(lambda: [], security_id="2")
            sleep.assert_awaited_once()
            self.assertAlmostEqual(sleep.await_args.args[0], .3)
            self.assertEqual(engine.market_data_worker.submit.await_count, 2)
        finally:
            await engine.close()
