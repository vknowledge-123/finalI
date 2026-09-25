"""Opt-in real-browser smoke test. Uses only an isolated in-memory test server."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time

os.environ["APP_TESTING"] = "1"
os.environ["ADMIN_AUTH_ENABLED"] = "0"

import uvicorn
from playwright.sync_api import expect, sync_playwright
from app import main as application
from app.crypto import EncryptionManager
from cryptography.fernet import Fernet


def main():
    artifacts = Path(tempfile.mkdtemp(prefix="ashuchart-browser-"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(application.app, log_level="error"))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[sock])), daemon=True)
    thread.start()
    results = []
    try:
        for _ in range(300):
            if server.started:
                break
            if not thread.is_alive():
                raise RuntimeError("Test server failed to start")
            time.sleep(0.05)
        assert server.started, "Test server startup timeout"
        application.store.encryption = EncryptionManager(Fernet.generate_key().decode())
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for width, height in [(1440, 1000), (768, 1024), (390, 844)]:
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    loaded = page.goto(f"http://127.0.0.1:{port}/dashboard", wait_until="domcontentloaded")
                    assert loaded.status == 200, (loaded.status, page.url)
                    page.wait_for_function("typeof openCfg === 'function' && typeof formatPrice === 'function'")
                    assert page.evaluate("typeof formatPrice") == "function", (page.url, errors, page.title(), page.locator('body').inner_text()[:200])
                    assert page.evaluate("formatPrice(427.5 * 1.01)") == "431.78"
                    assert page.evaluate("alertPosition({alert_name:'second'}, {status:'ENTERED'}, {alert_name:'first'})") is None
                    assert page.evaluate("alertPosition({alert_name:'first'}, {status:'ENTERED',trade_id:'old'}, {alert_name:'first',trade_id:'new'})") is None
                    if width < 640:
                        page.locator("#topbarMoreBtn").click()
                    page.locator('button[onclick="openCfg()"]').click()
                    webhook_route = "**/api/webhook-url?*"
                    page.route(webhook_route, lambda route: route.fulfill(json={
                        "ok": True, "url": "http://192.0.2.10/webhook/chartink?user_id=1&secret=test-secret",
                        "base_url_source": "PUBLIC_BASE_URL", "secret_required": True,
                    }))
                    page.evaluate("openCfg()")
                    expect(page.locator("#webhook_url_status")).to_contain_text("Verify PUBLIC_BASE_URL")
                    expect(page.locator("#webhook_url_status")).to_contain_text("http://192.0.2.10")
                    assert "test-secret" not in page.locator("#webhook_url_status").inner_text()
                    page.screenshot(path=str(artifacts / f"webhook-mismatch-{width}.png"))
                    page.unroute(webhook_route)
                    page.route(webhook_route, lambda route: route.fulfill(
                        status=502, content_type="text/html", body="<h1>Bad Gateway</h1>"))
                    page.evaluate("openCfg()")
                    expect(page.locator("#webhook_copy")).to_be_disabled()
                    expect(page.locator("#webhook_url")).to_have_value("")
                    expect(page.locator("#webhook_url_status")).to_contain_text("Could not load")
                    page.unroute(webhook_route)
                    page.route(webhook_route, lambda route: route.fulfill(json={
                        "ok": True, "url": f"http://127.0.0.1:{port}/webhook/chartink?user_id=1&secret=test-secret",
                        "base_url_source": "REQUEST", "secret_required": True,
                    }))
                    page.evaluate("openCfg()")
                    expect(page.locator("#webhook_copy")).to_be_enabled()
                    expect(page.locator("#webhook_url_status")).to_be_hidden()
                    page.unroute(webhook_route)
                    page.locator("#cfg_alert").fill(f"Browser Smoke {width}")
                    page.locator("#cfg_paper_on").check()
                    expect(page.locator("#cfg_turnover_topn")).to_be_disabled()
                    page.locator("#cfg_turnover_on").check()
                    page.locator("#cfg_turnover_topn").fill("10")
                    expect(page.locator("#cfg_turnover_topn")).to_be_enabled()
                    page.route("**/api/turnover/top?*", lambda route: route.fulfill(json={
                        "ready": True, "covered": 3200, "total": 3200,
                        "rows": [{"symbol": "TESTSTOCK", "rank": 1, "turnover": 2500000}],
                    }))
                    page.get_by_role("button", name="Refresh Turnover Ranking", exact=True).click()
                    expect(page.locator("#turnover_status")).to_have_text("Ready | 3200/3200 stocks")
                    expect(page.locator("#turnover_ranks")).to_contain_text("TESTSTOCK")
                    page.screenshot(path=str(artifacts / f"paper-turnover-{width}.png"))
                    page.unroute("**/api/turnover/top?*")
                    page.locator("#cfg_prod").select_option("CNC")
                    page.locator("#cfg_qtymode").select_option("QTY")
                    page.locator("#cfg_qty").fill("2")
                    page.locator("#cfg_order_retries").fill("0")
                    page.locator("#cfg_order_buffer").fill("0")
                    expect(page.locator("#cfg_high_break_tf")).to_be_disabled()
                    expect(page.locator("#cfg_high_break_ttl")).to_be_disabled()
                    expect(page.locator("#cfg_telegram_token")).to_be_disabled()
                    page.locator("#cfg_high_break_on").check()
                    page.locator("#cfg_high_break_tf").select_option("5")
                    page.locator("#cfg_high_break_ttl").fill("2")
                    page.locator("#cfg_high_break_buffer_on").check()
                    page.locator("#cfg_high_break_buffer").fill("0.10")
                    page.locator("#cfg_telegram_on").check()
                    page.locator("#cfg_telegram_token").fill("123456789:" + "a" * 35)
                    page.locator("#cfg_telegram_chat").fill("-1001234567")
                    page.locator("#cfg_high_break_on").evaluate("element => element.scrollIntoView({block: 'start'})")
                    page.screenshot(path=str(artifacts / f"alert-features-{width}.png"))
                    page.locator("#cfg_tgt").fill("4")
                    page.locator("#cfg_sl").fill("1")
                    page.locator("#cfg_tsl_on").select_option("false")
                    page.locator("#cfg_cost_sl_on").select_option("true")
                    page.locator("#cfg_cost_sl_rr").fill("2")
                    page.locator("#cfg_exit_alert_on").select_option("true")
                    page.locator("#cfg_exit_alert_name").fill(f"Browser Exit {width}")
                    page.locator("#cfgModal .cfg-card").evaluate("element => element.scrollTop = 0")
                    page.screenshot(path=str(artifacts / f"config-{width}.png"))
                    geometry = page.locator("#cfgModal .cfg-card").bounding_box()
                    assert geometry and geometry["width"] <= width, geometry
                    with page.expect_response(lambda response: "/api/alert-config" in response.url
                                              and response.request.method == "POST") as response:
                        page.get_by_role("button", name="Save Configuration", exact=True).click()
                    saved = response.value.json()
                    assert response.value.status == 200 and saved.get("status") == "saved", saved
                    configs = context.request.get(f"http://127.0.0.1:{port}/api/alert-config?user_id=1").json()
                    serialized = json.dumps(configs)
                    assert f"Browser Smoke {width}" in serialized, configs
                    config_key = f"browser smoke {width}"
                    config = configs["configs"][config_key]
                    assert config["paper_trading"] and config["turnover_filter_on"], config
                    assert config["turnover_top_n"] == 10, config
                    assert config["qty"] == 2 and config["product"] == "CNC", config
                    assert config["cost_sl_enabled"] and config["exit_alert_enabled"], config
                    assert config["trailing_sl_enabled"] is False, config
                    assert config["order_pending_retry_count"] == 0, config
                    assert config["order_limit_buffer_pct"] == 0, config
                    assert config["high_break_enabled"] and config["high_break_buffer_enabled"], config
                    assert config["high_break_timeframe_minutes"] == 5 and config["high_break_buffer"] == 0.1, config
                    assert config["high_break_ttl_minutes"] == 2, config
                    assert config["telegram_enabled"] and config["telegram_token_set"], config
                    assert "telegram_bot_token_encrypted" not in config and "telegram_bot_token" not in config, config
                    reloaded = page.reload(wait_until="domcontentloaded")
                    assert reloaded.status == 200, (reloaded.status, page.url)
                    page.wait_for_function("typeof openCfg === 'function' && typeof formatPrice === 'function'")
                    assert page.evaluate("typeof openCfg") == "function", (page.url, errors, page.title())
                    page.evaluate("openCfg()")
                    page.evaluate("name => fillCfg(name)", config_key)
                    expect(page.locator("#cfg_paper_on")).to_be_checked()
                    expect(page.locator("#cfg_turnover_on")).to_be_checked()
                    expect(page.locator("#cfg_turnover_topn")).to_have_value("10")
                    page.locator("#cfg_turnover_on").uncheck()
                    expect(page.locator("#cfg_turnover_topn")).to_be_disabled()
                    assert page.locator("#cfg_tsl_on").input_value() == "false"
                    assert page.locator("#cfg_order_retries").input_value() == "0"
                    assert page.locator("#cfg_order_buffer").input_value() == "0"
                    expect(page.locator("#cfg_high_break_on")).to_be_checked()
                    expect(page.locator("#cfg_high_break_ttl")).to_have_value("2")
                    expect(page.locator("#cfg_telegram_on")).to_be_checked()
                    expect(page.locator("#cfg_telegram_token")).to_have_value("")
                    expect(page.locator("#cfg_telegram_token_status")).to_have_text("(saved)")
                    page.locator("#cfg_high_break_on").uncheck()
                    expect(page.locator("#cfg_high_break_ttl")).to_be_disabled()
                    expect(page.locator("#cfg_high_break_tf")).to_be_disabled()
                    expect(page.locator("#cfg_high_break_buffer")).to_be_disabled()
                    page.locator("#cfg_telegram_on").uncheck()
                    expect(page.locator("#cfg_telegram_token")).to_be_disabled()
                    assert page.locator("#cfg_cost_sl_on").input_value() == "true"
                    assert page.locator("#cfg_exit_alert_name").input_value() == f"Browser Exit {width}"
                    page.evaluate("closeCfg()")
                    watch = {"id": f"browser-watch-{width}", "user_id": 1, "symbol": "WATCHTEST",
                             "side": "BUY", "level": "100.10", "expires_at": time.time() + 120, "phase": "WAITING"}

                    async def seed_watch():
                        from app.breakout_state import waiting_result
                        await application.store.save_breakout_watch(watch)
                        await application.store.save_alert(1, {
                            "alert_name": "Browser breakout", "time": datetime.now(timezone.utc).isoformat(),
                            "result": [waiting_result(watch)],
                        })

                    asyncio.run_coroutine_threadsafe(seed_watch(), application.APP_LOOP).result(5)
                    page.evaluate("loadAlerts()")
                    expect(page.locator("#alertsBody")).to_contain_text("WAITING FOR BREAKOUT")
                    expect(page.locator("#alertsBody")).to_contain_text("Above 100.10")
                    asyncio.run_coroutine_threadsafe(application.store.transition_breakout_watch(watch["id"], "WAITING",
                        dict(watch, phase="DONE", result={"symbol": "WATCHTEST", "status": "SKIPPED", "reason": "BREAKOUT_TTL_EXPIRED"})), application.APP_LOOP).result(5)
                    # The dashboard must refresh a completed watch without a manual click.
                    expect(page.locator("#alertsBody")).to_contain_text("BREAKOUT_TTL_EXPIRED", timeout=7000)
                    position = {
                        "trade_id": f"browser-{width}", "symbol": "SBIN", "user_id": 1,
                        "alert_name": f"Browser Smoke {width}", "status": "OPEN", "product": "CNC",
                        "side": "BUY", "qty": 2, "initial_qty": 2, "entry_price": 100,
                        "ltp": 100, "pnl": 0, "realized_pnl": 0, "target_price": 104,
                        "sl_price": 99, "trail_price": 99, "tsl_pct": 1,
                        "trailing_sl_enabled": True, "tsl_stepwise": True, "strategy_mode": "CLASSIC",
                        "paper_trading": True,
                    }

                    async def seed_position():
                        await application.store.upsert_position(1, "SBIN", position)
                        await application.store.save_alert(1, {
                            "alert_name": position["alert_name"], "time": datetime.now(timezone.utc).isoformat(),
                            "result": [
                                {"symbol": "SBIN", "status": "ENTERED", "side": "BUY", "reason": "ORDER_EXECUTED"},
                                {"symbol": "SBIN", "status": "SKIPPED", "reason": "HIGH_BREAK_CANDLE_NOT_READY"},
                                {"symbol": "SBIN", "status": "ERROR", "reason": "ORDER_REJECTED_INSUFFICIENT_FUNDS"},
                                {"symbol": "SBIN", "side": "BUY", "status": "WAITING_FOR_BREAKOUT",
                                 "reason": "WAITING_FOR_CANDLE", "break_level": None, "breakout_expires_at": time.time() + 60},
                            ],
                        })

                    asyncio.run_coroutine_threadsafe(seed_position(), application.APP_LOOP).result(5)
                    page.evaluate("refreshAll()")
                    page.wait_for_function("WS && WS.readyState === WebSocket.OPEN")
                    ltp = page.locator('[data-symbol="SBIN"][data-field="ltp"]').first
                    pnl = page.locator('[data-symbol="SBIN"][data-field="pnl"]').first
                    expect(ltp).to_have_text("100.00")
                    asyncio.run_coroutine_threadsafe(application.ws_mgr.broadcast(1, {
                        "type": "tick", "symbol": "SBIN", "ltp": 101.5, "close": 100,
                    }), application.APP_LOOP).result(5)
                    expect(ltp).to_have_text("101.50")
                    expect(pnl).to_have_text("3.00")
                    expect(page.locator("#alertsBody")).to_contain_text("PAPER OPEN")
                    expect(page.locator("#alertsBody")).to_contain_text("PAPER / CNC")
                    expect(page.locator("#totalPnlBadge")).to_contain_text("Paper:")
                    assert page.locator("#totalPnlBadge .pnl-value").first.inner_text().strip() == "\u20b9 0.00"
                    strategy_rows = page.locator('#alertsBody tr').filter(has_text=f'Browser Smoke {width}')
                    skipped = strategy_rows.filter(has_text='HIGH_BREAK_CANDLE_NOT_READY')
                    expect(skipped.locator('[data-label="Status"]')).to_contain_text('SKIPPED')
                    expect(skipped.locator('[data-label="P&L"]')).to_have_text('--')
                    expect(skipped.locator('[data-label="TSL"]')).to_have_text('--')
                    expect(skipped.locator('button')).to_have_count(0)
                    rejected = strategy_rows.filter(has_text='ORDER_REJECTED_INSUFFICIENT_FUNDS')
                    expect(rejected.locator('[data-label="Status"]')).to_contain_text('ERROR')
                    expect(rejected.locator('[data-label="Qty"]')).to_have_text('--')
                    pending_candle = strategy_rows.filter(has_text='WAITING FOR CANDLE')
                    expect(pending_candle).to_have_count(1)
                    expect(pending_candle.locator('[data-label="P&L"]')).to_have_text('--')
                    expect(page.locator(".atsl-SBIN").first).to_have_text("99.00")
                    assert page.evaluate("POS_SNAPSHOT.SBIN.ltp") == 101.5
                    # Clearing alert history must not remove access to an open position.
                    page.evaluate("window.savedSmokeAlerts = [...ALERTS]; ALERTS.length = 0; renderAlerts()")
                    expect(page.locator('#alertsBody')).to_contain_text('OPEN_POSITION')
                    expect(page.locator('#alertsBody button[data-squareoff="SBIN"]')).to_have_count(1)
                    page.evaluate("ALERTS.push(...window.savedSmokeAlerts); renderAlerts()")
                    page.evaluate("window.scrollTo(0, 0)")
                    layout = page.evaluate("""() => {
                        const nav = document.querySelector('nav').getBoundingClientRect();
                        const brand = document.querySelector('.brand-title').getBoundingClientRect();
                        const main = document.querySelector('main').getBoundingClientRect();
                        return {navTop: nav.top, navBottom: nav.bottom, brandTop: brand.top,
                                brandBottom: brand.bottom, mainTop: main.top,
                                pageWidth: document.documentElement.scrollWidth};
                    }""")
                    assert layout["brandTop"] >= layout["navTop"], layout
                    assert layout["brandBottom"] <= layout["navBottom"], layout
                    assert layout["mainTop"] >= layout["navBottom"], layout
                    assert layout["pageWidth"] <= width, layout
                    page.screenshot(path=str(artifacts / f"dashboard-{width}.png"), full_page=True)

                    async def close_position():
                        closed = dict(position, status="CLOSED", qty=0, ltp=101.5, pnl=3, realized_pnl=3, exit_reason="TARGET")
                        await application.store.upsert_position(1, "SBIN", closed)
                        await application.ws_mgr.broadcast(1, {"type": "pos", "position": closed})

                    asyncio.run_coroutine_threadsafe(close_position(), application.APP_LOOP).result(5)
                    expect(page.locator("#posCount")).to_have_text("0 Active Pos")
                    expect(page.locator("#alertsBody")).to_contain_text("EXITED")
                    expect(page.locator('#alertsBody button[onclick*="squareoff"]')).to_have_count(0)
                    page.evaluate("window.scrollTo(0, 0)")
                    page.screenshot(path=str(artifacts / f"closed-{width}.png"), full_page=True)
                    assert not errors, errors
                    results.append({"viewport": [width, height], "saved": True, "tick_pnl": True,
                                    "backend_trail_preserved": True, "closed_position": True, "page_errors": errors})
                    context.close()
            finally:
                browser.close()
        print(json.dumps({"results": results, "artifacts": str(artifacts)}))
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()
        if thread.is_alive():
            raise RuntimeError("Test server did not shut down")


if __name__ == "__main__":
    main()
