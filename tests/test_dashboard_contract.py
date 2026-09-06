import json
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main as main_app


class DashboardMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.fields = {}
        self.in_script = False
        self.select_id = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.fields[attrs["id"]] = attrs.get("value", "")
        if tag == "select":
            self.select_id = attrs.get("id")
        if tag == "option" and self.select_id:
            if not self.fields[self.select_id] or "selected" in attrs:
                self.fields[self.select_id] = attrs.get("value", "")
        if tag == "script" and not attrs.get("src"):
            self.in_script = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False
        if tag == "select":
            self.select_id = None

    def handle_data(self, data):
        if self.in_script:
            self.scripts.append(data)


class DashboardContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript tests")
    def test_rendering_error_handling_and_config_round_trip(self):
        with patch.object(main_app, "_is_test_mode", return_value=True), \
             patch.object(main_app, "_admin_auth_enabled", return_value=False), TestClient(main_app.app) as client:
            config = {"user_id": 1, "alert_name": "QA", "enabled": True, "strategy_mode": "CLASSIC",
                      "direction": "LONG", "product": "MIS", "qty_mode": "QTY", "qty": 1,
                      "target_pct": 2, "stop_loss_pct": 1, "trailing_sl_enabled": False,
                      "cost_sl_enabled": True, "cost_sl_rr": 2, "exit_alert_enabled": True,
                      "exit_alert_name": "QA EXIT"}
            saved = client.post("/api/alert-config", json=config)
            self.assertEqual(saved.status_code, 200)
            self.assertNotIn("error", saved.json())
            markup = DashboardMarkup()
            markup.feed(client.get("/dashboard").text)
            fixture = {"scripts": markup.scripts, "fields": markup.fields,
                       "configs": client.get("/api/alert-config?user_id=1").json()}
            result = subprocess.run([shutil.which("node"), str(Path(__file__).with_name("dashboard_contract.cjs"))],
                                    input=json.dumps(fixture), capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertGreaterEqual(output["checks"], 8)
            for payload in output["saved"]:
                response = client.post("/api/alert-config", json=payload)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("error", response.json())
            final = client.get("/api/alert-config?user_id=1").json()["configs"]["qa"]
            self.assertFalse(final["trailing_sl_enabled"])
            self.assertTrue(final["cost_sl_enabled"])
            self.assertTrue(final["exit_alert_enabled"])
