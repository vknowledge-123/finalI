"""Dedicated, read-only Dhan NSE turnover worker. Run one per deployment."""

import asyncio
import csv
import io
import json
import logging
import time
import uuid
from datetime import datetime

import httpx

from .alert_features import IST, enabled
from .dhan_broker import DHAN_SCRIP_MASTER_URL, DhanFeedService, MarketFeed
from .dhan_feed_policy import equity_session_open
from .service_bootstrap import configure_logging, init_store, load_user_ids
from .market_quote_budget import reserve_quote_slot
from .turnover import TurnoverBook, equity_universe

log = logging.getLogger("turnover_service")
LEASE_KEY = "turnover:worker:lease"
RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], 30)
  return 1
end
return 0
"""
RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
PUBLISH = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'EX', 15)
return 1
"""


def rest_trade_epoch(value):
    # Dhan documents a day/month/year string; keep an ISO variant for SDK wrappers.
    for fmt in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=IST).timestamp()
        except ValueError:
            pass
    return None


class TurnoverSession:
    def __init__(self, uid, credentials, universe, client, store=None):
        self.uid = uid
        self.credentials = dict(credentials)
        self.book = TurnoverBook(universe)
        self.client = client
        self.store = store
        self.feed = None
        self.seed_task = None
        self.generation = 0
        self.last_status = None
        self.last_status_at = 0

    async def start(self):
        loop = asyncio.get_running_loop()
        self.feed = DhanFeedService(
            self.uid, self.credentials["client_id"], self.credentials["access_token"],
            self.on_tick, lambda _: None,
            on_state=lambda connected: loop.call_soon_threadsafe(self.on_state, connected),
            data_mode=MarketFeed.Quote, include_order_updates=False,
        )
        await self.feed.start([(MarketFeed.NSE, sid) for sid in self.book.universe.values()])
        self.seed_task = asyncio.create_task(self.refresh_quotes(), name=f"turnover_quotes_{self.uid}")
        log.info("TURNOVER_SUBSCRIBED | user=%s instruments=%s mode=QUOTE", self.uid, len(self.book.universe))

    def on_state(self, connected):
        self.book.connected = bool(connected)
        self.generation += 1
        self.book.quotes.clear()
        log.info("TURNOVER_CONNECTION | user=%s connected=%s", self.uid, connected)

    def on_tick(self, packet):
        if packet.get("exchange_segment") != MarketFeed.NSE or "volume" not in packet:
            return
        self.book.update(packet.get("security_id"), packet.get("LTP"), packet.get("volume"),
                         packet.get("received_at"), trade_at=packet.get("exchange_ts"))

    async def refresh_quotes(self):
        # Warm/revalidate quiet symbols; ticks remain the primary source. Quote
        # snapshots are batched, never one HTTP request per stock or per tick.
        while True:
            try:
                if self.book.connected and equity_session_open():
                    ids = list(self.book.universe.values())
                    for offset in range(0, len(ids), 1000):
                        # Risk/order quote requests take precedence over ranking.
                        if not await reserve_quote_slot(self.store, self.uid, background=True):
                            await asyncio.sleep(2)
                            continue
                        started, generation = time.time(), self.generation
                        response = await self.client.post("https://api.dhan.co/v2/marketfeed/quote",
                            headers={"client-id": self.credentials["client_id"],
                                     "access-token": self.credentials["access_token"]},
                            json={"NSE_EQ": [int(sid) for sid in ids[offset:offset + 1000]]})
                        if response.status_code == 429:
                            log.warning("TURNOVER_QUOTE_THROTTLED | user=%s", self.uid)
                            await asyncio.sleep(60)
                            break
                        response.raise_for_status()
                        payload = response.json()
                        if payload.get("status") != "success":
                            raise ValueError("TURNOVER_QUOTE_FAILED")
                        rows = (payload.get("data") or {}).get("NSE_EQ")
                        if not isinstance(rows, dict):
                            raise ValueError("TURNOVER_QUOTE_SCHEMA_INVALID")
                        if generation == self.generation and self.book.connected:
                            for sid, quote in rows.items():
                                if isinstance(quote, dict):
                                    self.book.update(sid, quote.get("last_price"), quote.get("volume"), started,
                                                     trade_at=rest_trade_epoch(quote.get("last_trade_time")))
                        await asyncio.sleep(1.25)
                await asyncio.sleep(45)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("TURNOVER_QUOTE_FAILED | user=%s type=%s", self.uid, type(exc).__name__)
                await asyncio.sleep(30)

    async def stop(self):
        if self.seed_task:
            self.seed_task.cancel()
            await asyncio.gather(self.seed_task, return_exceptions=True)
        if self.feed:
            await self.feed.stop()


async def run_worker(store, client):
    owner = uuid.uuid4().hex
    if not await store.redis.set(LEASE_KEY, owner, nx=True, ex=30):
        raise RuntimeError("TURNOVER_WORKER_ALREADY_RUNNING")
    sessions = {}
    universe, master_day = None, ""
    last_refresh = 0
    try:
        while True:
            if not await store.redis.eval(RENEW, 1, LEASE_KEY, owner):
                raise RuntimeError("TURNOVER_WORKER_LEASE_LOST")
            now = datetime.now(IST)
            day = now.strftime("%Y%m%d")
            if time.monotonic() - last_refresh >= 10:
                last_refresh = time.monotonic()
                desired = {}
                for uid in await load_user_ids(store):
                    configs = await store.list_alert_configs(uid)
                    wanted = any(enabled(c.get("enabled", True)) and enabled(c.get("turnover_filter_on"))
                                 for c in configs.values())
                    if wanted and str(await store.load_broker(uid)).upper() == "DHAN" and equity_session_open(now, True):
                        creds = await store.load_dhan_credentials(uid)
                        if creds.get("client_id") and creds.get("access_token"):
                            desired[uid] = {"client_id": creds["client_id"], "access_token": creds["access_token"]}
                for uid in list(sessions):
                    session = sessions[uid]
                    if desired.get(uid) != session.credentials or session.book.day != day:
                        await session.stop()
                        del sessions[uid]
                        await store.redis.eval(PUBLISH, 2, LEASE_KEY, f"turnover:snapshot:{uid}", owner,
                            json.dumps({"ready": False, "ts": time.time(), "trading_day": day,
                                        "reason": "TURNOVER_DISABLED_OR_RECONNECTING"}))
                if desired and (universe is None or day != master_day):
                    try:
                        response = await client.get(DHAN_SCRIP_MASTER_URL)
                        response.raise_for_status()
                        universe = await asyncio.to_thread(lambda: equity_universe(csv.DictReader(io.StringIO(response.text))))
                        master_day = day
                        log.info("TURNOVER_UNIVERSE_READY | eligible=%s day=%s", len(universe), day)
                    except Exception as exc:
                        log.warning("TURNOVER_MASTER_FAILED | type=%s", type(exc).__name__)
                        # Never carry yesterday's membership into today's filter.
                        universe = None
                if universe is not None:
                    for uid, creds in desired.items():
                        if uid not in sessions:
                            if not await store.redis.eval(RENEW, 1, LEASE_KEY, owner):
                                raise RuntimeError("TURNOVER_WORKER_LEASE_LOST")
                            session = TurnoverSession(uid, creds, universe, client, store)
                            sessions[uid] = session
                            await session.start()
            for uid, session in sessions.items():
                snapshot = session.book.snapshot()
                saved = await store.redis.eval(PUBLISH, 2, LEASE_KEY, f"turnover:snapshot:{uid}", owner,
                                               json.dumps(snapshot, allow_nan=False))
                if not saved:
                    raise RuntimeError("TURNOVER_WORKER_LEASE_LOST")
                status = (snapshot["ready"], snapshot["reason"])
                if status != session.last_status or time.monotonic() - session.last_status_at >= 60:
                    log.info("TURNOVER_RANK_STATUS | user=%s ready=%s covered=%s total=%s reason=%s",
                             uid, snapshot["ready"], snapshot["covered"], snapshot["total"], snapshot["reason"])
                    session.last_status, session.last_status_at = status, time.monotonic()
            await asyncio.sleep(2)
    finally:
        await asyncio.gather(*(session.stop() for session in sessions.values()), return_exceptions=True)
        for uid in sessions:
            await store.redis.eval(PUBLISH, 2, LEASE_KEY, f"turnover:snapshot:{uid}", owner,
                json.dumps({"ready": False, "reason": "TURNOVER_STOPPED", "ts": time.time(),
                            "trading_day": datetime.now(IST).strftime("%Y%m%d")}))
        await store.redis.eval(RELEASE, 1, LEASE_KEY, owner)


async def main():
    configure_logging("turnover_service")
    store = await init_store()
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            await run_worker(store, client)
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
