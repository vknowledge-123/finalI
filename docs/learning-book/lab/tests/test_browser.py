"""Real browser against a disposable server and a synthetic paper broker."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    temp = tmp_path_factory.mktemp("paper-browser")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "PAPERLAB_DB": str(temp / "paper.sqlite3")}
    root = Path(__file__).resolve().parents[1]
    with (temp / "server.log").open("w", encoding="utf-8") as output:
        process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "paperlab.api:default_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port),
        ], cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Paper test server exited; inspect its temporary log")
                try:
                    if httpx.get(base + "/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Paper test server did not become ready")
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 1000}, {"width": 390, "height": 844}])
def test_form_to_backend_to_closed_position(server, viewport):
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(server)
            name = "Browser " + uuid.uuid4().hex[:8]
            symbol = "DEMO" + str(viewport["width"])
            page.get_by_label("Strategy name", exact=True).fill(name)
            page.get_by_role("button", name="Save strategy", exact=True).click()
            expect(page.get_by_role("status")).to_have_text("Saved " + name)
            with httpx.Client(base_url=server) as client:
                configs = client.get("/api/configs").json()["strategies"]
                assert next(item for item in configs if item["name"] == name)["retry_count"] == 0
                response = client.post("/api/signals", json={
                    "event_id": uuid.uuid4().hex, "strategy": name, "symbol": symbol, "price": "100",
                })
                assert response.status_code == 200
                page.get_by_role("button", name="Refresh positions").click()
                row = page.get_by_role("row").filter(has=page.get_by_role("cell", name=symbol, exact=True))
                expect(row).to_contain_text("OPEN")
                assert client.post("/api/ticks", json={"symbol": symbol, "price": "103"}).status_code == 200
                page.get_by_role("button", name="Refresh positions").click()
                expect(row).to_contain_text("CLOSED")
                expect(row).to_contain_text("TARGET")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors
        finally:
            context.close()
            browser.close()


def test_html_gateway_failure_is_readable(server):
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.route("**/api/positions", lambda route: route.fulfill(
                status=502, content_type="text/html", body="<h1>Bad Gateway</h1>"))
            page.goto(server)
            expect(page.get_by_role("status")).to_have_text("Unexpected server response (502)")
        finally:
            browser.close()
