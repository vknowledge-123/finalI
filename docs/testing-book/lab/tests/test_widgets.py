"""Custom widgets are DOM contracts, not native select/date controls."""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_custom_dropdown(page):
    page.set_content('''
      <button onclick="document.querySelector('#menu').hidden=false">Choose product</button>
      <div role="listbox" aria-label="Products" id="menu" hidden>
        <button role="option" onclick="document.querySelector('#chosen').textContent='CNC';
          document.querySelector('#menu').hidden=true">Delivery</button>
      </div><output id="chosen" aria-label="Selected product"></output>
    ''')
    page.get_by_role("button", name="Choose product").click()
    menu = page.get_by_role("listbox", name="Products")
    menu.get_by_role("option", name="Delivery", exact=True).click()
    expect(menu).to_be_hidden()
    expect(page.locator('#chosen')).to_have_text("CNC")


def test_pagination_identity(page):
    page.set_content('''
      <table aria-label="Signals"><tbody id="rows"></tbody></table>
      <button id="next" onclick="render(++index)">Next</button>
      <script>
        let index=0; const records=['TEST','DEMO','PAPER'];
        function render(i){
          document.querySelector('#rows').innerHTML='<tr><td>'+records[i]+'</td></tr>';
          document.querySelector('#next').disabled=i===records.length-1;
        } render(index);
      </script>
    ''')
    names = []
    for expected in ["TEST", "DEMO", "PAPER"]:
        row = page.get_by_role("table", name="Signals").get_by_role("row")
        expect(row).to_have_text(expected)
        names.append(row.inner_text())
        if expected != "PAPER":
            page.get_by_role("button", name="Next").click()
    expect(page.get_by_role("button", name="Next")).to_be_disabled()
    assert names == ["TEST", "DEMO", "PAPER"]
    assert len(names) == len(set(names))


def test_custom_calendar_scopes_duplicate_day(page):
    page.set_content('''
      <section aria-label="August"><button>23</button></section>
      <section aria-label="September"><button onclick="document.querySelector('#date').value='2026-09-23'">23</button></section>
      <label>Chosen date<input id="date" readonly></label>
    ''')
    page.get_by_role("region", name="September").get_by_role("button", name="23", exact=True).click()
    expect(page.get_by_label("Chosen date")).to_have_value("2026-09-23")


def test_keyboard_and_tooltip(page):
    page.set_content('''
      <button id="tool" onmouseenter="document.querySelector('#tip').hidden=false"
        onmouseleave="document.querySelector('#tip').hidden=true"
        onclick="document.querySelector('#result').textContent='requested'">Reconnect</button>
      <span id="tip" role="tooltip" hidden>Reconnect paper feed</span>
      <output id="result"></output>
    ''')
    button = page.get_by_role("button", name="Reconnect", exact=True)
    button.hover()
    expect(page.get_by_role("tooltip")).to_have_text("Reconnect paper feed")
    button.focus()
    expect(button).to_be_focused()
    button.press("Enter")
    expect(page.locator('#result')).to_have_text("requested")


def test_popup_uses_returned_page(page):
    page.set_content('''<button onclick="window.open('about:blank')">Open paper help</button>''')
    with page.expect_popup() as pending:
        page.get_by_role("button", name="Open paper help").click()
    popup = pending.value
    popup.set_content('<h1>Risk guide</h1>')
    expect(popup.get_by_role("heading", name="Risk guide")).to_be_visible()
    popup.close()
    expect(page.get_by_role("button", name="Open paper help")).to_be_visible()
