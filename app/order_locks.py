"""Renew broker-operation locks until the owning coroutine finishes."""

import asyncio
import logging

log = logging.getLogger(__name__)


class OrderLockLeases:
    def __init__(self):
        self.active = {}

    def track(self, key, token, ttl_ms, renew, release, on_loss):
        owner = asyncio.current_task()
        identity = (owner, key)
        lease = {}

        async def cleanup():
            if self.active.get(identity) is lease:
                self.active.pop(identity, None)
            try:
                await release(key, token)
            except Exception:
                log.exception("ORDER_LOCK_RELEASE_FAILED | key=%s", key)

        async def watch():
            try:
                while not owner.done():
                    done, _ = await asyncio.wait({owner}, timeout=max(0.01, ttl_ms / 3000))
                    if done:
                        break
                    if not await renew(key, token, ttl_ms):
                        raise RuntimeError("ORDER_LOCK_OWNERSHIP_LOST")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ORDER_LOCK_RENEWAL_FAILED | key=%s", key)
                try:
                    await on_loss()
                except Exception:
                    log.exception("ORDER_LOCK_KILL_FAILED | key=%s", key)
                finally:
                    # A broker request already sent may still complete. Prevent
                    # this coroutine from continuing to another placement/retry.
                    owner.cancel("Order lock ownership lost; reconcile broker")
            finally:
                await cleanup()

        lease["cleanup"] = cleanup
        lease["task"] = asyncio.create_task(watch(), name=f"order_lock:{key}")
        self.active[identity] = lease

    async def release(self, key):
        lease = self.active.get((asyncio.current_task(), key))
        if lease:
            task = lease["task"]
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # Cancellation before the watcher first runs does not run its finally.
            await lease["cleanup"]()

    async def close(self):
        leases = list(self.active.values())
        tasks = [lease["task"] for lease in leases]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for lease in leases:
            await lease["cleanup"]()
