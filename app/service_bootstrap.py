from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Dict, List

from .redis_store import RedisStore
from .trade_engine import TradeEngine
from .log_safety import install_access_log_filter

try:
    from .crypto import init_encryption
except Exception:  # pragma: no cover - optional dependency/runtime setup
    init_encryption = None  # type: ignore[assignment]


log = logging.getLogger("service_bootstrap")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")


def configure_logging(service_name: str) -> None:
    install_access_log_filter()
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
    testing = (os.getenv("APP_TESTING") or "").strip().lower() in {"1", "true", "yes", "on"}
    if testing or (os.getenv("APP_ENV") or "").strip().lower() == "test":
        from .memory_store import InMemoryStore

        return InMemoryStore()  # type: ignore[return-value]

    encryption_manager = None
    if init_encryption is not None:
        try:
            encryption_manager = init_encryption()
        except Exception as exc:
            log.warning("Encryption initialization failed: %s", exc)
    store = RedisStore(REDIS_URL, encryption_manager)
    await store.require_connection()
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
        self._init_locks: Dict[int, asyncio.Lock] = {}

    async def get(self, user_id: int) -> TradeEngine:
        uid = int(user_id)
        async with self._init_locks.setdefault(uid, asyncio.Lock()):
            engine = self.engines.get(uid)
            if engine is not None:
                await engine.configure_broker()
                return engine
            engine = TradeEngine(uid, self.store)
            try:
                await engine.configure_broker()
                cache = await self.store.load_sector_cache(uid)
                if cache:
                    engine.load_sector_cache(cache)
                await engine.rehydrate_open_positions()
            except BaseException:
                await engine.close()
                raise
            self.engines[uid] = engine
            return engine

    async def close(self) -> None:
        for engine in list(self.engines.values()):
            try:
                await engine.close()
            except Exception:
                pass
        self.engines.clear()
        self._init_locks.clear()


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
