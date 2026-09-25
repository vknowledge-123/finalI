"""Out-of-band Telegram delivery; no broker order operations."""

import asyncio
import hashlib
import logging
import math
import re
import time
from datetime import datetime

import httpx

from ..alert_features import IST, enabled

log = logging.getLogger(__name__)


class TelegramURLFilter(logging.Filter):
    def filter(self, record):
        # Bot credentials are embedded in Telegram's URL, including httpx logs.
        if "api.telegram.org/bot" in record.getMessage() or re.search(r"bot[0-9]+:[A-Za-z0-9_-]+", record.getMessage()):
            record.msg, record.args = "Telegram HTTP request [URL redacted]", ()
        return True


class TelegramService:
    def __init__(self, store_provider, ensure_engine):
        self.store_provider = store_provider
        self.ensure_engine = ensure_engine
        self.client = None
        self._next_send = {}
        for name in ("httpx", "httpcore.http11", "httpcore.http2"):
            logger = logging.getLogger(name)
            if not any(isinstance(f, TelegramURLFilter) for f in logger.filters):
                logger.addFilter(TelegramURLFilter())

    async def run(self):
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            self.client = client
            try:
                while True:
                    try:
                        for uid in await self.store_provider().list_all_user_ids():
                            try:
                                await self.poll_user(uid, datetime.now(IST))
                            except Exception as exc:
                                log.warning("TELEGRAM_PASS_FAILED | user=%s type=%s", uid, type(exc).__name__)
                    except Exception as exc:
                        log.warning("TELEGRAM_WORKER_FAILED | type=%s", type(exc).__name__)
                    await asyncio.sleep(5)
            finally:
                self.client = None

    def destination(self, config):
        if not enabled(config.get("telegram_enabled")) or str(config.get("strategy_mode", "CLASSIC")).upper() != "CLASSIC":
            return None
        cipher = getattr(getattr(self.store_provider(), "encryption", None), "cipher", None)
        if cipher is None:
            return None
        token = cipher.decrypt(config["telegram_bot_token_encrypted"].encode()).decode()
        chat = config["telegram_chat_id"]
        identity = hashlib.sha256(f"{token}:{chat}".encode()).hexdigest()
        return token, chat, identity

    async def send(self, token, chat, text):
        destination = hashlib.sha256(f"{token}:{chat}".encode()).hexdigest()
        delay = self._next_send.get(destination, 0) - time.monotonic()
        if delay > 1.1:
            return False
        if delay > 0:
            await asyncio.sleep(delay)
        self._next_send[destination] = time.monotonic() + 1.05
        try:
            response = await self.client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                "chat_id": chat, "text": text[:4096],
            })
            data = response.json()
            if response.status_code == 200 and data.get("ok") is True:
                return True
            if response.status_code == 429:
                retry = min(3600, max(1, int((data.get("parameters") or {}).get("retry_after", 60))))
                self._next_send[destination] = time.monotonic() + retry
            log.warning("TELEGRAM_SEND_FAILED | status=%s code=%s", response.status_code, data.get("error_code"))
        except Exception as exc:
            log.warning("TELEGRAM_SEND_FAILED | type=%s", type(exc).__name__)
        return False

    async def deliver(self, uid, destination, identity, text):
        store = self.store_provider()
        key = f"{destination[2]}:{identity}"
        if not await store.claim_telegram_message(uid, key):
            return False
        if not await self.send(destination[0], destination[1], text):
            return False
        await store.mark_telegram_sent(uid, key)
        return True

    async def poll_user(self, uid, now):
        now = now.astimezone(IST)
        store = self.store_provider()
        configs = await store.list_alert_configs(uid)
        destinations = {}
        for key, config in configs.items():
            try:
                target = self.destination(config)
                if target:
                    destinations[key] = target
            except Exception as exc:
                log.warning("TELEGRAM_CONFIG_INVALID | user=%s type=%s", uid, type(exc).__name__)

        for event in (await store.list_telegram_entries(uid))[:100]:
            target = destinations.get(event["alert_name"])
            age = now.timestamp() - float(event["created_at"])
            if target is None or not 0 <= age <= 600:
                await store.delete_telegram_entry(uid, event["trade_id"])
                continue
            text = (("PAPER TRADE\n" if event.get("paper_trading") else "LIVE TRADE\n")
                    + f"{event['symbol']} | {event['side']} | {event['product']}\n"
                    f"Entry: INR {event['entry']:.2f}\nTarget: INR {event['target']:.2f}\n"
                    f"Stop loss: INR {event['stop_loss']:.2f}\nFilled quantity: {event['qty']}\n"
                    f"Strategy: {event['alert_name']}")
            if await self.deliver(uid, target, f"entry:{event['trade_id']}", text):
                await store.delete_telegram_entry(uid, event["trade_id"])

        if not destinations:
            return
        unique = {target[2]: target for target in destinations.values()}
        minute = now.hour * 60 + now.minute
        day = now.strftime("%Y%m%d")
        if 545 <= minute < 550:
            for target in unique.values():
                await self.deliver(uid, target, f"welcome:{day}", "Welcome dear traders!\n" + now.strftime("%d %b %Y | 09:05 IST"))
        if now.weekday() >= 5 or not 555 <= minute <= 930:
            return
        # Claim the five-minute slots before making a broker call. All groups
        # share this one MTM snapshot; multiple API workers do not spam a group.
        slot = f"pnl:{day}:{minute // 5}"
        claimed = []
        for target in unique.values():
            if await store.claim_telegram_message(uid, f"{target[2]}:{slot}"):
                claimed.append(target)
        if not claimed:
            return
        engine = await self.ensure_engine(uid)
        if engine.broker != "DHAN":
            return
        data = await asyncio.wait_for(engine._broker_positions(), timeout=10)
        rows = data.get("net")
        if not isinstance(rows, list):
            raise ValueError("TELEGRAM_MTM_INVALID")
        values = [float(row["pnl"]) for row in rows]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("TELEGRAM_MTM_INVALID")
        mtm = sum(values)
        text = f"My current P&L is INR {mtm:+.2f}\nDhan account positions (realized + unrealized)\n{now:%d %b %Y %H:%M} IST"
        for target in claimed:
            if await self.send(target[0], target[1], text):
                await store.mark_telegram_sent(uid, f"{target[2]}:{slot}")
