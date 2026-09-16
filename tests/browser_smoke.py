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
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for width, height in [(1440, 1000), (768, 1024), (390, 844)]:
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{port}/dashboard", wait_until="networkidle")
                    if width < 640:
                        page.locator("#topbarMoreBtn").click()
                    page.locator('button[onclick="openCfg()"]').click()
                    page.locator("#cfg_alert").fill(f"Browser Smoke {width}")
                    page.locator("#cfg_prod").select_option("CNC")
                    page.locator("#cfg_qtymode").select_option("QTY")
                    page.locator("#cfg_qty").fill("2")
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
                    assert config["qty"] == 2 and config["product"] == "CNC", config
                    assert config["cost_sl_enabled"] and config["exit_alert_enabled"], config
                    assert config["trailing_sl_enabled"] is False, config
                    page.reload(wait_until="networkidle")
                    page.evaluate("name => fillCfg(name)", config_key)
                    assert page.locator("#cfg_tsl_on").input_value() == "false"
                    assert page.locator("#cfg_cost_sl_on").input_value() == "true"
                    assert page.locator("#cfg_exit_alert_name").input_value() == f"Browser Exit {width}"
                    page.evaluate("closeCfg()")
                    position = {
                        "trade_id": f"browser-{width}", "symbol": "SBIN", "user_id": 1,
                        "alert_name": f"Browser Smoke {width}", "status": "OPEN", "product": "CNC",
                        "side": "BUY", "qty": 2, "initial_qty": 2, "entry_price": 100,
                        "ltp": 100, "pnl": 0, "realized_pnl": 0, "target_price": 104,
                        "sl_price": 99, "trail_price": 99, "tsl_pct": 1,
                        "trailing_sl_enabled": True, "tsl_stepwise": True, "strategy_mode": "CLASSIC",
                    }

                    async def seed_position():
                        await application.store.upsert_position(1, "SBIN", position)
                        await application.store.save_alert(1, {
                            "alert_name": position["alert_name"], "time": datetime.now(timezone.utc).isoformat(),
                            "result": [{"symbol": "SBIN", "status": "ENTERED", "side": "BUY", "reason": "ORDER_EXECUTED"}],
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
                    expect(page.locator(".atsl-SBIN").first).to_have_text("99.00")
                    assert page.evaluate("POS_SNAPSHOT.SBIN.ltp") == 101.5
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
