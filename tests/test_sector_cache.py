import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("APP_TESTING", "1")

from app.main import _sector_quote_values
from app.memory_store import InMemoryStore
from app.trade_engine import OrderExecution, TradeEngine


class SectorCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_direction_uses_custom_signal_side_for_sector_selection(self) -> None:
        for side, admitted in [("BUY", "GAINER"), ("SELL", "LOSER")]:
            with self.subTest(side=side):
                store = InMemoryStore()
                await store.save_alert_config(1, {
                    "alert_name": "BOTH_SECTOR", "enabled": True, "direction": "BOTH",
                    "strategy_mode": "PRECISION_SNIPER", "sector_filter_on": True, "top_n_sector": 1,
                    "entry_start_time": "00:00", "entry_end_time": "23:59",
                })
                engine = TradeEngine(1, store)
                engine.sym_sector = {"GAINER": "NIFTY AUTO", "LOSER": "NIFTY METAL"}
                engine.sector_sum = {"NIFTY AUTO": 4, "NIFTY METAL": -3}
                engine.sector_cnt = {"NIFTY AUTO": 1, "NIFTY METAL": 1}
                engine._fetch_historical_candles = AsyncMock(return_value=[])
                engine._wait_for_entry_feed_ready = AsyncMock(return_value=(False, "TEST_FEED_GUARD", 0))
                signal = SimpleNamespace(side=side, candle_time="2026-09-16T10:00:00")
                with patch("app.trade_engine.evaluate_precision_sniper", return_value=(signal, {})):
                    result = await engine.on_chartink_alert("BOTH_SECTOR", ["GAINER", "LOSER"])
                self.assertEqual({row["symbol"] for row in result if row["reason"] == "TEST_FEED_GUARD"}, {admitted})

    async def test_entry_sector_selection_uses_direction_and_top_n(self) -> None:
        for direction, top_n, expected in [
            ("SHORT", 1, {"LOSER"}), ("SHORT", 2, {"LOSER", "RUNNER"}),
            ("LONG", 1, {"GAINER"}), ("LONG", 2, {"GAINER", "RUNNER"}),
        ]:
            with self.subTest(direction=direction, top_n=top_n):
                store = InMemoryStore()
                await store.save_alert_config(1, {
                    "alert_name": "RANK_DIRECTION", "enabled": True,
                    "direction": direction, "sector_filter_on": True, "top_n_sector": top_n,
                    "entry_start_time": "00:00", "entry_end_time": "23:59",
                })
                engine = TradeEngine(1, store)
                engine.sym_sector = {"LOSER": "NIFTY METAL", "RUNNER": "NIFTY IT", "GAINER": "NIFTY AUTO"}
                engine.load_sector_cache({"sectors": [
                    {"name": "NIFTY AUTO", "ltp": 104, "prev_close": 100, "pct": 4},
                    {"name": "NIFTY IT", "ltp": 99, "prev_close": 100, "pct": -1},
                    {"name": "NIFTY METAL", "ltp": 97, "prev_close": 100, "pct": -3},
                ]})
                # Stop at the next guard so this tests filtering without broker orders.
                engine._wait_for_entry_feed_ready = AsyncMock(return_value=(False, "TEST_FEED_GUARD", 0))
                result = await engine.on_chartink_alert("RANK_DIRECTION", ["LOSER", "RUNNER", "GAINER"])
                admitted = {row["symbol"] for row in result if row["reason"] == "TEST_FEED_GUARD"}
                self.assertEqual(admitted, expected)
                self.assertTrue(all(row["reason"] in {"TEST_FEED_GUARD", "SECTOR_FILTER"} for row in result))
                self.assertEqual(engine.get_sector_rank()[0][0], "NIFTY AUTO")

    def test_sector_quote_values_reads_dhan_ohlc_shape(self) -> None:
        response = {
            "status": "success",
            "data": {
                "IDX_I": {
                    "14": {
                        "last_price": 123.0,
                        "ohlc": {"open": 101.0, "high": 125.0, "low": 99.0, "close": 100.0},
                    }
                }
            },
        }

        values = _sector_quote_values(response, "14")

        self.assertEqual(values["ltp"], 123.0)
        self.assertEqual(values["prev_close"], 100.0)
        self.assertAlmostEqual(values["pct"], 23.0)

    async def test_engine_ranks_cached_sector_index_percentages_first(self) -> None:
        engine = TradeEngine(user_id=1, store=InMemoryStore())
        engine.load_sector_cache(
            {
                "sectors": [
                    {"name": "NIFTY IT", "ltp": 101.0, "prev_close": 100.0, "pct": 1.0},
                    {"name": "NIFTY AUTO", "ltp": 104.0, "prev_close": 100.0, "pct": 4.0},
                ]
            }
        )

        ranks = engine.get_sector_rank()

        self.assertEqual(ranks[0][0], "NIFTY AUTO")
        self.assertAlmostEqual(ranks[0][1], 4.0)

    async def test_same_price_premarket_cache_is_not_rank_ready(self) -> None:
        engine = TradeEngine(user_id=1, store=InMemoryStore())
        engine.load_sector_cache(
            {
                "source": "DHAN_OHLC",
                "rank_ready": False,
                "rank_ready_count": 0,
                "sectors": [
                    {
                        "name": "NIFTY AUTO",
                        "ltp": 28841.5,
                        "prev_close": 28841.5,
                        "pct": 0.0,
                        "ok": True,
                        "rank_ready": False,
                    }
                ],
            }
        )

        self.assertEqual(engine.get_sector_rank(), [])
        self.assertEqual(engine.sector_index_prev_close["NIFTY AUTO"], 28841.5)
        self.assertEqual(engine.sector_index_ltp["NIFTY AUTO"], 28841.5)

    async def test_live_sector_tick_marks_cached_sector_rank_ready(self) -> None:
        engine = TradeEngine(user_id=1, store=InMemoryStore())
        engine.load_sector_cache(
            {
                "sectors": [
                    {
                        "name": "NIFTY AUTO",
                        "ltp": 28841.5,
                        "prev_close": 28841.5,
                        "pct": 0.0,
                        "ok": True,
                        "rank_ready": False,
                    }
                ],
            }
        )

        engine.update_sector_index_tick("NIFTY AUTO", 28900.0)
        ranks = engine.get_sector_rank()

        self.assertEqual(ranks[0][0], "NIFTY AUTO")
        self.assertGreater(ranks[0][1], 0.0)

    async def test_sector_filter_does_not_bypass_unknown_symbols(self) -> None:
        store = InMemoryStore()
        await store.save_alert_config(
            1,
            {
                "alert_name": "SECTOR_TEST",
                "enabled": True,
                "sector_filter_on": True,
                "top_n_sector": 2,
                "entry_start_time": "00:00",
                "entry_end_time": "23:59",
            },
        )
        engine = TradeEngine(user_id=1, store=store)

        result = await engine.on_chartink_alert("SECTOR_TEST", ["NOTINMAP"])

        self.assertEqual(result[0]["status"], "SKIPPED")
        self.assertEqual(result[0]["reason"], "SECTOR_UNKNOWN")

    async def test_sector_filter_reports_not_ready_when_no_rank_exists(self) -> None:
        store = InMemoryStore()
        await store.save_alert_config(
            1,
            {
                "alert_name": "SECTOR_TEST",
                "enabled": True,
                "sector_filter_on": True,
                "top_n_sector": 2,
                "entry_start_time": "00:00",
                "entry_end_time": "23:59",
            },
        )
        engine = TradeEngine(user_id=1, store=store)

        result = await engine.on_chartink_alert("SECTOR_TEST", ["SBIN"])

        self.assertEqual(result[0]["status"], "SKIPPED")
        self.assertEqual(result[0]["reason"], "SECTOR_RANK_NOT_READY")

    async def test_dhan_sector_filter_refreshes_rank_when_cache_missing(self) -> None:
        class FakeDhan:
            def ohlc_data(self, _payload):
                return {}

        store = InMemoryStore()
        await store.save_alert_config(
            1,
            {
                "alert_name": "SECTOR_TEST",
                "enabled": True,
                "sector_filter_on": True,
                "top_n_sector": 1,
                "entry_start_time": "00:00",
                "entry_end_time": "23:59",
                "direction": "LONG",
                "product": "MIS",
                "qty_mode": "QTY",
                "qty": 1,
            },
        )
        engine = TradeEngine(user_id=1, store=store)
        engine.broker = "DHAN"
        engine.dhan_client_id = "client"
        engine.dhan_access_token = "token"
        engine.dhan = FakeDhan()
        engine.market_data_worker.submit = AsyncMock(
            return_value={
                "status": "success",
                "data": {
                    "IDX_I": {
                        "31": {
                            "last_price": 105.0,
                            "ohlc": {"close": 100.0},
                        }
                    }
                },
            }
        )
        engine._fetch_ltp = AsyncMock(return_value=100.0)
        engine._place_order_with_execution = AsyncMock(
            return_value=OrderExecution(
                order_id="OID",
                symbol="SAIL",
                side="BUY",
                qty=1,
                status="COMPLETE",
                avg_price=100.0,
                filled_qty=1,
            )
        )

        result = await engine.on_chartink_alert("SECTOR_TEST", ["SAIL"])

        self.assertEqual(result[0]["status"], "ENTERED")
        self.assertIn(result[0]["reason"], {"ORDER_OK", "ORDER_EXECUTED"})
        engine.market_data_worker.submit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
