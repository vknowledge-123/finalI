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
from .turnover import QUOTE_MAX_AGE, TurnoverBook, equity_universe

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
        self.next_refresh = {}
        self.retries = {}
        self.blocked_until = 0

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
        self.book.rejections.clear()
        self.next_refresh.clear()
        self.retries.clear()
        log.info("TURNOVER_CONNECTION | user=%s connected=%s", self.uid, connected)

    def on_tick(self, packet):
        if packet.get("exchange_segment") != MarketFeed.NSE or "volume" not in packet:
            return
        sid = str(packet.get("security_id"))
        if self.book.update(sid, packet.get("LTP"), packet.get("volume"),
                            packet.get("received_at"), trade_at=packet.get("exchange_ts"), source="DHAN_WS"):
            self.recovered(sid)

    def recovered(self, sid):
        retry = self.retries.pop(sid, None)
        if retry:
            log.info("TURNOVER_QUOTE_RECOVERED | user=%s symbol=%s security_id=%s attempts=%s source=%s",
                     self.uid, self.book.symbols[sid], sid, retry["attempts"],
                     self.book.quotes[self.book.symbols[sid]]["source"])

    def schedule_retry(self, sid, now):
        attempts = self.retries.get(sid, {}).get("attempts", 0) + 1
        delay = (5, 10, 20, 60)[min(attempts - 1, 3)]
        self.retries[sid] = {"attempts": attempts, "due": now + delay}

    async def refresh_once(self):
        now = time.time()
        if not self.book.connected or not equity_session_open() or now < self.blocked_until:
            return
        priority = set()
        redis = getattr(self.store, "redis", None)
        if redis is not None:
            key = f"turnover:refresh:{self.uid}"
            async with redis.pipeline(transaction=True) as pipe:
                pipe.zrangebyscore(key, now - 10, now)
                pipe.zremrangebyscore(key, "-inf", now)
                values, _ = await pipe.execute()
            priority = {self.book.universe[s] for s in values if s in self.book.universe}
        pending = {r["security_id"] for r in self.book.pending(now)}
        ids = [sid for sid in self.book.universe.values()
               if (self.retries.get(sid, {}).get("due", 0) <= now
                   if sid in pending or sid in self.retries else self.next_refresh.get(sid, 0) <= now)]
        ids.sort(key=lambda sid: (sid not in priority, sid not in pending))
        for offset in range(0, len(ids), 1000):
            if not self.book.connected or not equity_session_open():
                return
            # Background refreshes always yield to foreground order/exit quotes.
            if not await reserve_quote_slot(self.store, self.uid, background=True):
                return
            batch = ids[offset:offset + 1000]
            started, generation = time.time(), self.generation
            try:
                response = await self.client.post("https://api.dhan.co/v2/marketfeed/quote",
                    headers={"client-id": self.credentials["client_id"],
                             "access-token": self.credentials["access_token"]},
                    json={"NSE_EQ": [int(sid) for sid in batch]})
                if response.status_code == 429:
                    self.blocked_until = time.time() + 60
                    log.warning("TURNOVER_QUOTE_THROTTLED | user=%s", self.uid)
                    return
                response.raise_for_status()
                payload = response.json()
                if payload.get("status") != "success":
                    raise ValueError("TURNOVER_QUOTE_FAILED")
                rows = (payload.get("data") or {}).get("NSE_EQ")
                if not isinstance(rows, dict):
                    raise ValueError("TURNOVER_QUOTE_SCHEMA_INVALID")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("TURNOVER_QUOTE_FAILED | user=%s type=%s", self.uid, type(exc).__name__)
                if generation == self.generation:
                    for sid in batch:
                        self.book.reject(sid, "REST_REQUEST_FAILED")
                        self.schedule_retry(sid, time.time())
                self.blocked_until = time.time() + 5
                return
            if generation != self.generation or not self.book.connected:
                return
            failed = []
            for sid in batch:
                quote = rows.get(sid)
                if isinstance(quote, dict):
                    accepted = self.book.update(sid, quote.get("last_price"), quote.get("volume"), started,
                        trade_at=rest_trade_epoch(quote.get("last_trade_time")), source="DHAN_REST")
                else:
                    accepted = self.book.reject(sid, "REST_QUOTE_MISSING")
                self.next_refresh[sid] = time.time() + 45
                # A newer websocket quote may have overtaken this REST request.
                current = self.book.quotes.get(self.book.symbols[sid])
                fresh = current and 0 <= time.time() - current["ts"] <= QUOTE_MAX_AGE
                if accepted or fresh:
                    self.recovered(sid)
                else:
                    self.schedule_retry(sid, time.time())
                    failed.append({"symbol": self.book.symbols[sid], "security_id": sid,
                                   "reason": self.book.rejections.get(self.book.symbols[sid]),
                                   **self.retries[sid]})
            if failed:
                log.warning("TURNOVER_RETRY_PENDING | user=%s count=%s sample=%s", self.uid, len(failed), failed[:20])
            await asyncio.sleep(1.25)

    async def refresh_quotes(self):
        while True:
            try:
                await self.refresh_once()
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("TURNOVER_REFRESH_FAILED | user=%s type=%s", self.uid, type(exc).__name__)
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
                    log.info("TURNOVER_RANK_STATUS | user=%s ready=%s covered=%s total=%s reason=%s state=%s missing=%s stale=%s sample=%s",
                             uid, snapshot["ready"], snapshot["covered"], snapshot["total"], snapshot["reason"],
                             snapshot["state"], snapshot["missing_count"], snapshot["stale_count"], snapshot["missing"][:20])
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
