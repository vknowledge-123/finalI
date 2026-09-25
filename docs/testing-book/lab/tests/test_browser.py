import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_ui_api_position_lifecycle(page, lab_url, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(lab_url)
    page.get_by_label("Strategy name").fill("student")
    page.get_by_label("Quantity").fill("2")
    page.get_by_role("button", name="Save strategy").click()
    expect(page.get_by_role("status")).to_have_text("Strategy saved")
    configs = page.request.get(lab_url + "/api/strategies").json()
    assert configs[0]["name"] == "student" and configs[0]["qty"] == 2
    response = page.request.post(lab_url + "/api/signals", data={
        "event_id": "browser-one", "strategy": "student", "symbol": "TEST", "price": "100"})
    assert response.ok
    page.get_by_role("button", name="Refresh positions").click()
    row = page.get_by_role("row").filter(has_text="TEST")
    expect(row.locator('[data-field="status"]')).to_have_text("OPEN")
    assert page.request.post(lab_url + "/api/ticks/TEST", data={"price": "101"}).ok
    page.get_by_role("button", name="Refresh positions").click()
    expect(row.locator('[data-field="status"]')).to_have_text("CLOSED")
    expect(row.locator('[data-field="pnl"]')).to_have_text("2.00")
    expect(row.locator('[data-field="exit_reason"]')).to_have_text("TARGET")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
