from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Dict, List

from .redis_store import RedisStore
from .trade_engine import TradeEngine

try:
    from .crypto import init_encryption
except Exception:  # pragma: no cover - optional dependency/runtime setup
    init_encryption = None  # type: ignore[assignment]


log = logging.getLogger("service_bootstrap")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")


def configure_logging(service_name: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [%(levelname)s] {service_name} | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


async def init_store() -> RedisStore:
    encryption_manager = None
    if init_encryption is not None:
        try:
            encryption_manager = init_encryption()
        except Exception as exc:
            log.warning("Encryption initialization failed: %s", exc)
    store = RedisStore(REDIS_URL, encryption_manager)
    if not await store.ping():
        raise RuntimeError(f"Redis is not reachable at {REDIS_URL}")
    await store.init_scripts()
    return store


async def load_user_ids(store: RedisStore) -> List[int]:
    try:
        return await store.list_all_user_ids()
    except Exception as exc:
        log.warning("User listing failed: %s", exc)
        return []


class EngineRegistry:
    def __init__(self, store: RedisStore) -> None:
        self.store = store
        self.engines: Dict[int, TradeEngine] = {}

    async def get(self, user_id: int) -> TradeEngine:
        uid = int(user_id)
        engine = self.engines.get(uid)
        if engine is None:
            engine = TradeEngine(uid, self.store)
            self.engines[uid] = engine
            await engine.configure_broker()
            try:
                cache = await self.store.load_sector_cache(uid)
                if cache:
                    engine.load_sector_cache(cache)
            except Exception:
                pass
            try:
                await engine.rehydrate_open_positions()
            except Exception as exc:
                log.warning("Position rehydrate failed | user=%s err=%s", uid, exc)
        return engine

    async def close(self) -> None:
        for engine in list(self.engines.values()):
            try:
                await engine.close()
            except Exception:
                pass
        self.engines.clear()


async def wait_for_shutdown() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()

