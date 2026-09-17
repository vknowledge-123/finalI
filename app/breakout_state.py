"""Shared, fixed-level breakout watches and dashboard result projection."""

import hashlib
import json
import time


def config_signature(config):
    values = {key: value for key, value in config.items() if not key.startswith("telegram_")}
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def waiting_result(watch):
    return {"symbol": watch["symbol"], "side": watch["side"], "status": "WAITING_FOR_BREAKOUT",
            "reason": "WAITING_FOR_BREAKOUT", "breakout_watch_id": watch["id"],
            "break_level": float(watch["level"]), "breakout_expires_at": watch["expires_at"]}


def project_alerts(alerts, watches, user_id):
    by_id = {watch["id"]: watch for watch in watches if watch["user_id"] == int(user_id)}
    projected = []
    for alert in alerts:
        row = dict(alert)
        if "result" in row:
            results = []
            for result in row.get("result") or []:
                item = dict(result)
                watch = by_id.get(item.get("breakout_watch_id"))
                if watch:
                    if watch.get("phase") == "DONE":
                        item.update(watch["result"])
                    elif watch.get("phase") == "EXECUTING":
                        item.update(status="PENDING ENTRY", reason="BREAKOUT_ORDER_SUBMITTING")
                    elif time.time() >= watch["expires_at"]:
                        item.update(status="SKIPPED", reason="BREAKOUT_TTL_EXPIRED")
                results.append(item)
            row["result"] = results
        projected.append(row)
    return projected
