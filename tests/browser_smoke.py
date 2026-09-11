"""Opt-in real-browser smoke test. Uses only an isolated in-memory test server."""

import asyncio
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
from playwright.sync_api import sync_playwright
from app.main import app


def main():
    artifacts = Path(tempfile.mkdtemp(prefix="ashuchart-browser-"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
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
                for width, height in [(1440, 1000), (390, 844)]:
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{port}/dashboard", wait_until="networkidle")
                    page.locator('button[onclick="openCfg()"]').click()
                    page.locator("#cfg_alert").fill(f"Browser Smoke {width}")
                    page.locator("#cfg_prod").select_option("CNC")
                    page.locator("#cfg_qtymode").select_option("QTY")
                    page.locator("#cfg_qty").fill("2")
                    page.locator("#cfg_tsl_on").select_option("false")
                    page.locator("#cfg_cost_sl_on").select_option("true")
                    page.locator("#cfg_cost_sl_rr").fill("2")
                    page.locator("#cfg_exit_alert_on").select_option("true")
                    page.locator("#cfg_exit_alert_name").fill(f"Browser Exit {width}")
                    page.locator("#cfgModal").evaluate("element => element.scrollTop = 0")
                    page.screenshot(path=str(artifacts / f"config-{width}.png"), full_page=True)
                    geometry = page.locator("#cfgModal .cfg-card").bounding_box()
                    assert geometry and geometry["width"] <= width, geometry
                    with page.expect_response(lambda response: "/api/alert-config" in response.url
                                              and response.request.method == "POST") as response:
                        page.get_by_role("button", name="Save Configuration", exact=True).click()
                    saved = response.value.json()
                    assert response.value.status == 200 and saved.get("ok"), saved
                    configs = context.request.get(f"http://127.0.0.1:{port}/api/alert-config?user_id=1").json()
                    serialized = json.dumps(configs)
                    assert f"Browser Smoke {width}" in serialized, configs
                    page.reload(wait_until="networkidle")
                    page.screenshot(path=str(artifacts / f"dashboard-{width}.png"), full_page=True)
                    assert not errors, errors
                    results.append({"viewport": [width, height], "saved": True, "page_errors": errors})
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
