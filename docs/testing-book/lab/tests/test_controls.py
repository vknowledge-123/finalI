"""Self-contained DOM exercises; no external website is contacted."""
import csv
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_inputs_radios_select(page):
    page.set_content('''<label>Capital<input></label>
      <label><input type="checkbox">Trailing SL</label>
      <label><input type="radio" name="side">Long</label>
      <label>Product<select><option value="MIS">Intraday</option>
      <option value="CNC">Delivery</option></select></label>''')
    page.get_by_label("Capital").fill("5000")
    page.get_by_label("Trailing SL").check()
    page.get_by_label("Long", exact=True).check()
    page.get_by_label("Product").select_option("CNC")
    expect(page.get_by_label("Capital")).to_have_value("5000")
    expect(page.get_by_label("Trailing SL")).to_be_checked()
    expect(page.get_by_label("Long", exact=True)).to_be_checked()
    expect(page.get_by_label("Product")).to_have_value("CNC")


def test_css_xpath_and_row_scope(page):
    page.set_content('''<table><tr data-trade="one"><td>TEST</td><td>2.00</td></tr>
      <tr data-trade="two"><td>TEST</td><td>-1.00</td></tr></table>''')
    expect(page.locator('[data-trade="one"]').locator('td').nth(1)).to_have_text("2.00")
    expect(page.locator("xpath=//tr[@data-trade='two']/td[2]")).to_have_text("-1.00")


def test_open_shadow_root(page):
    page.set_content('<div id="host"></div>')
    page.evaluate('''() => {
      document.querySelector('#host').attachShadow({mode:'open'}).innerHTML =
        '<button>Reconnect feed</button>';
    }''')
    expect(page.get_by_role("button", name="Reconnect feed")).to_be_visible()


def test_native_date(page):
    page.set_content('<label>From date<input type="date"></label>')
    page.get_by_label("From date").fill("2026-09-23")
    expect(page.get_by_label("From date")).to_have_value("2026-09-23")


@pytest.mark.parametrize("accept,expected", [(True, "true"), (False, "false")])
def test_dialog_both_outcomes(page, accept, expected):
    page.set_content('''<button onclick="document.body.dataset.ok=confirm('Close paper?')">
      Close paper</button>''')
    page.once("dialog", lambda dialog: dialog.accept() if accept else dialog.dismiss())
    page.get_by_role("button", name="Close paper", exact=True).click()
    expect(page.locator("body")).to_have_attribute("data-ok", expected)


def test_frame(page):
    page.set_content('''<iframe title="Paper help"
      srcdoc="<button>Read risk notice</button>"></iframe>''')
    expect(page.frame_locator('iframe[title="Paper help"]').get_by_role("button")).to_have_text("Read risk notice")


def test_upload(page, tmp_path):
    path = tmp_path / "symbols.csv"
    path.write_text("symbol,qty\nTEST,2\n", encoding="utf-8")
    page.set_content('<label>Import symbols<input type="file"></label>')
    control = page.get_by_label("Import symbols")
    control.set_input_files(path)
    assert control.evaluate("el => el.files.length") == 1
    assert control.evaluate("el => el.files[0].name") == "symbols.csv"


def test_download_content(page, tmp_path):
    page.set_content('<a download="positions.csv">Export CSV</a>')
    page.locator('a').evaluate(r'''el => {
      el.href = URL.createObjectURL(new Blob(['symbol,qty\nTEST,2\n'], {type:'text/csv'}));
    }''')
    with page.expect_download() as pending:
        page.get_by_role("link", name="Export CSV").click()
    target = tmp_path / "positions.csv"
    pending.value.save_as(target)
    with target.open(newline="", encoding="utf-8") as stream:
        assert list(csv.DictReader(stream)) == [{"symbol": "TEST", "qty": "2"}]


def test_context_cookie_isolation(browser):
    first, second = browser.new_context(), browser.new_context()
    try:
        first.add_cookies([{"name": "paper", "value": "one", "url": "http://127.0.0.1"}])
        assert first.cookies()[0]["value"] == "one"
        assert second.cookies() == []
    finally:
        first.close()
        second.close()


def test_network_error_message(page, lab_url):
    page.route("**/api/positions", lambda route: route.fulfill(
        status=502, content_type="application/json", body='{"detail":"UPSTREAM_DOWN"}'))
    page.goto(lab_url)
    expect(page.get_by_role("status")).to_have_text("UPSTREAM_DOWN")
