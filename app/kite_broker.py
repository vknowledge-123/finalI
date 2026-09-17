"""Compatibility with the pinned Kite SDK, without dropping order protection."""

import inspect
import asyncio
import logging
import queue
import threading
import time

log = logging.getLogger(__name__)


class KiteCallbackQueue:
    """Bounded FIFO from the SDK reactor thread to the application's event loop."""

    def __init__(self, on_overflow, capacity=64):
        self.loop = asyncio.get_running_loop()
        self.queue = queue.Queue(maxsize=capacity)
        self.ready = asyncio.Event()
        self.closed = False
        self.lock = threading.Lock()
        self.wake_pending = False
        self.overflow_reported = False
        self.on_overflow = on_overflow
        self.task = asyncio.create_task(self._run(), name="kite_tick_delivery")

    def submit(self, factory):
        with self.lock:
            if self.closed or self.loop.is_closed():
                return False
            accepted = True
            try:
                self.queue.put_nowait(factory)
            except queue.Full:
                self.overflow_reported = True
                accepted = False
            # Coalesce wakeups too; a bounded queue alone does not bound the
            # event loop's thread-safe callback backlog.
            if not self.wake_pending:
                self.wake_pending = True
                self.loop.call_soon_threadsafe(self.ready.set)
            return accepted

    async def _run(self):
        overflow_handled = False
        retry_overflow_at = 0.0
        while True:
            await self.ready.wait()
            with self.lock:
                self.ready.clear()
                self.wake_pending = False
            if self.overflow_reported and not overflow_handled and time.monotonic() >= retry_overflow_at:
                try:
                    await self.on_overflow()
                    overflow_handled = True
                except Exception:
                    retry_overflow_at = time.monotonic() + 1.0
                    log.exception("ZERODHA_TICK_OVERFLOW_HEALTH_WRITE_FAILED")
            try:
                factory = self.queue.get_nowait()
            except queue.Empty:
                continue
            try:
                await factory()
            except Exception:
                log.exception("ZERODHA_TICK_DELIVERY_FAILED")
            finally:
                self.queue.task_done()
            if not self.queue.empty():
                self.ready.set()

    async def close(self):
        with self.lock:
            self.closed = True
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()


def place_protected_order(client, **params):
    parameters = inspect.signature(client.place_order).parameters
    if "market_protection" in parameters or any(p.kind == p.VAR_KEYWORD for p in parameters.values()):
        return client.place_order(**params)
    # SDK 5.0.1 predates this REST field. Reuse its authenticated transport;
    # never catch TypeError and resubmit an order that may already be accepted.
    variety = params["variety"]
    response = client._post("order.place", url_args={"variety": variety}, params=params)
    return response["order_id"]


def normalize_holdings(rows):
    if not isinstance(rows, list):
        raise ValueError("ZERODHA_HOLDINGS_INVALID_RESPONSE")
    result = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("tradingsymbol") or "quantity" not in row:
            raise ValueError("ZERODHA_HOLDINGS_INVALID_ROW")
        quantity = max(0, int(row["quantity"]) + int(row.get("t1_quantity") or 0)
                       - int(row.get("used_quantity") or 0))
        result.append(dict(row, quantity=quantity, product="CNC"))
    return result
