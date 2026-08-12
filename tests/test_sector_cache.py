import os
import unittest

os.environ.setdefault("APP_TESTING", "1")

from app.main import _sector_quote_values
from app.memory_store import InMemoryStore
from app.trade_engine import TradeEngine


class SectorCacheTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
