# Part IV. Playwright Browser Automation

## Chapter 19. The Web, DOM, Browser and First End-to-End Test

### What the browser actually does

The browser downloads HTML, constructs a document tree, applies CSS and executes JavaScript. JavaScript can change the document after initial loading, send HTTP requests and receive streamed messages. The DOM is the current document representation, not necessarily the original source. A screenshot captures pixels; a locator finds an element through DOM and accessibility information.

Playwright launches a browser, creates isolated contexts and opens pages. A context is an independent browser session with its own cookies and storage. A page is a tab. The pytest plugin supplies a `page` fixture and handles its lifecycle. Install the browser binaries separately from the Python package. See the [official installation guide](https://playwright.dev/python/docs/intro) for platform support.

### A small, complete browser test

```python
from playwright.sync_api import expect

def test_form_contract(page):
    page.set_content('''
      <label for="name">Strategy name</label>
      <input id="name">
      <button>Save strategy</button>
    ''')
    page.get_by_label("Strategy name").fill("alpha")
    expect(page.get_by_label("Strategy name")).to_have_value("alpha")
    expect(page.get_by_role("button", name="Save strategy")).to_be_enabled()
```

This test proves the local HTML interaction, not backend persistence. `set_content` is excellent for learning a control in isolation. For end-to-end coverage, use `page.goto(lab_url)`, click the real save button and inspect both status message and API readback. The companion `test_browser.py` demonstrates that complete path.

A locator describes how to find an element and resolves when used. Prefer role and accessible name for buttons, labels for inputs and explicit test IDs for complex components. These choices survive many harmless layout changes. A selector built from the fifth nested `div` expresses layout coincidence rather than user intent.

### Locators and assertions work together

`expect(locator).to_have_text(...)` retries until the condition succeeds or times out. `assert locator.inner_text() == ...` reads once and may fail during a legitimate update. Use Python assertions for static API data and Playwright expectations for asynchronous browser state.

Multiple matching buttons cause strictness errors for a single-element action. Scope to the relevant dialog, form or row. Do not reflexively append `.first`; that may select another strategy's action. [Locator documentation](https://playwright.dev/python/docs/locators) explains the supported locator methods.

### Practice and checkpoint

Create two Save buttons in different forms and observe the strictness error. Scope one using a form test ID or accessible group. Explain what the test proves after clicking but before verifying a response: only that an action was attempted, not that the backend committed the change.

## Chapter 20. CSS, XPath, Shadow DOM and Selector Tools

### Understand before generating

CSS selectors describe elements through tag, ID, class, attributes and relationships. `#config` selects an ID, `.error` selects a class, and `[data-field="pnl"]` selects an attribute. A space means descendant; `>` means direct child. Prefer attributes created as stable test contracts over styling classes likely to change.

```python
def test_css_and_xpath(page):
    page.set_content('''
      <table><tr data-trade="paper-1">
        <td>TEST</td><td data-field="pnl">2.00</td>
      </tr></table>
    ''')
    row = page.locator('[data-trade="paper-1"]')
    assert row.locator('[data-field="pnl"]').inner_text() == "2.00"
    assert page.locator("xpath=//tr[@data-trade='paper-1']/td[1]").inner_text() == "TEST"
```

XPath navigates the document using expressions and supports parent/ancestor relationships. It can be useful in legacy markup, but absolute paths such as `/html/body/div[3]/div[2]` are fragile. CSS classes can also be fragile; neither language is automatically reliable. The stability of the chosen contract matters more than selector syntax.

### Shadow DOM and frames differ

A web component may contain an open shadow root. Playwright's normal locators can work through open shadow roots; XPath does not pierce them. Closed shadow roots are not a general automation target through normal locators. An iframe contains another document and needs a frame locator. Do not treat every element-not-found error as a timeout problem: first inspect which document or shadow root owns the element.

```python
from playwright.sync_api import expect

def test_open_shadow_component(page):
    page.set_content('<div id="host"></div>')
    page.evaluate('''() => {
      const root = document.querySelector('#host').attachShadow({mode:'open'});
      root.innerHTML = '<button>Reconnect feed</button>';
    }''')
    expect(page.get_by_role("button", name="Reconnect feed")).to_be_visible()
```

Selector helper extensions, including SelectorsHub, may help inspect and generate selectors. Review permissions and avoid installing extensions into a browser profile holding client credentials. Generated selectors are suggestions. Check uniqueness, semantic meaning and behavior after a rerender. Do not make an extension a required dependency of the CI test run.

### Practice and checkpoint

Find a control using role, CSS and XPath. Change its wrapper layout and see which selectors survive. The exercise is not to declare XPath forbidden; it is to recognize which assumptions each selector encodes. Add an explicit `data-testid` only when a stable accessible locator does not express the intended target well.

## Chapter 21. Inputs, Radios, Checkboxes and Dropdowns

### Model controls according to their behavior

Use `fill()` for a text input, `check()` or `uncheck()` for checkboxes, and `select_option()` for a native select. These actions express intent better than clicking coordinates. A toggle may be a checkbox, button with switch semantics, or a custom widget; inspect its accessibility role and use the matching contract.

```python
from playwright.sync_api import expect

def test_native_controls(page):
    page.set_content('''
      <label>Capital<input name="capital"></label>
      <label><input type="checkbox">Trailing SL</label>
      <label><input type="radio" name="side">Long</label>
      <label>Product<select><option value="MIS">Intraday</option>
      <option value="CNC">Delivery</option></select></label>
    ''')
    page.get_by_label("Capital").fill("5000")
    page.get_by_label("Trailing SL").check()
    page.get_by_label("Long", exact=True).check()
    page.get_by_label("Product").select_option("CNC")
    expect(page.get_by_label("Trailing SL")).to_be_checked()
    expect(page.get_by_label("Product")).to_have_value("CNC")
```

The [actions guide](https://playwright.dev/python/docs/input) documents these APIs. In your app, verify not only the control state but also the saved boolean. A checked appearance with a string-valued false can become true in weak backend parsing.

### Native versus Bootstrap-style dropdowns

A custom dropdown is often a button opening a list of clickable items. It is not a `<select>`, so `select_option()` is inappropriate. Open the menu, wait for the visible option, choose it, and assert the selected value. Hidden duplicate menus can cause strict locator errors; scope to the visible menu or the component that owns it.

Recipe for a component exposing listbox/option semantics:

```python
def choose_custom_product(page):
    page.get_by_role("button", name="Choose product").click()
    menu = page.get_by_role("listbox", name="Products")
    menu.get_by_role("option", name="Delivery", exact=True).click()
```

Do not use `force=True` to click a hidden option just to make a test pass. That bypasses an important user condition. A hidden menu or disabled setting may be behaving correctly because its parent toggle is off.

The executable `test_custom_dropdown` in `lab/tests/test_widgets.py` builds a listbox, opens it, chooses Delivery and verifies both the selected value and menu closure. Read its HTML alongside the test. Notice that the test does not assume the component is implemented with Bootstrap; it targets the same observable control contract regardless of styling library.

### Dependent settings

For breakout controls, test toggle off -> timeframe and TTL unavailable; toggle on -> enabled; configure; save; reload; values retained. For trailing stop, toggle off must disable trailing behavior, not merely grey out a field. That second requirement belongs in a backend risk test. This is how frontend and backend responsibilities complement each other.

### Practice and checkpoint

Test a zero buffer and a disabled buffer separately. They may currently produce similar numerical thresholds but represent different saved configurations. Your expected result should state both the persisted flag and the value, not only the final price.

## Chapter 22. Static Tables, Dynamic Rows and Pagination

### Row identity matters

A trading dashboard may show several alerts for the same symbol. A symbol alone is not a sufficient identity for every action. Use strategy plus trade ID or a dedicated row identifier. Scope child locators to the selected row so an assertion about one order does not read another order's P&L.

Recipe for the lab's table:

```python
from playwright.sync_api import expect

def expect_paper_position(page, strategy, symbol, pnl):
    table = page.get_by_role("table", name="Positions")
    row = table.get_by_role("row").filter(has_text=strategy).filter(has_text=symbol)
    expect(row).to_have_count(1)
    expect(row.locator('[data-field="pnl"]')).to_have_text(pnl)
```

For ambiguous substring names such as `alpha` and `alpha-long`, use exact cells or a trade ID. The helper above is appropriate only when its fixture guarantees unique matching text. A careful helper documents that precondition rather than silently calling `.first`.

### Rerendering and stale assumptions

After a tick or refresh, the application may replace the entire table body. Locators re-resolve; cached element handles can point to detached nodes. Prefer locators for ongoing interaction. If ordering changes with P&L, do not assert that the first row remains a particular symbol unless sorting itself is the feature under test.

The historical bug in this project is instructive: a skipped alert row became LIVE when a later position existed for the same symbol. A regression test should seed an earlier skipped alert and a later successful trade, then assert their statuses independently. It should also verify that P&L updates do not attach to the skipped row and that only the live row has a square-off control.

### Pagination and virtualized lists

For numbered pagination, verify current-page indicator, row range, next/previous transitions and final-page disabled state. For cursor pagination, assert no duplicated or missing IDs across the collected pages. Sorting or filtering should reset the page or follow an explicit policy. An empty filtered result must not display stale rows.

A virtualized table renders only visible rows. Counting DOM rows does not count the full dataset. Test data-source totals through an API contract and test visible rendering separately. Scroll through representative segments and assert stable identity. Avoid scraping thousands of rows when an API test can validate the underlying dataset more cheaply.

Run the companion `test_pagination_identity`. It uses three synthetic pages and a bounded loop. Every iteration checks the expected row, records its identity and moves only when another page is expected. The final assertions verify the last-page button is disabled and all expected identities were visited exactly once. Extend it with a filter that resets pagination and a deliberately duplicated record to see the failure.

### Practice and checkpoint

Create a fixture with two strategies owning the same symbol and one rejected historical alert. Write three row assertions. Explain why selecting the first matching symbol is insufficient for choosing the exit button safely.

## Chapter 23. Date Pickers, Dialogs and Frames

### Dates have both representation and meaning

A native date input accepts an ISO-style value such as `2026-09-23`. The displayed text depends on browser locale. Assert the value rather than assuming the rendered order is day/month/year. A custom jQuery or Bootstrap calendar may require opening a panel, navigating month/year and selecting a day button. Scope the day to the intended month because adjacent months can show duplicate day numbers.

```python
from playwright.sync_api import expect

def test_native_date(page):
    page.set_content('<label>From date<input type="date"></label>')
    page.get_by_label("From date").fill("2026-09-23")
    expect(page.get_by_label("From date")).to_have_value("2026-09-23")
```

For a custom calendar, write a bounded navigation loop or select month/year through supported controls. Do not loop forever until text happens to match. Test invalid ranges, leap days, start after end, timezone conversion and a range including a market holiday. The calendar widget cannot establish whether a broker has candle data for the selected session.

The companion `test_custom_calendar_scopes_duplicate_day` intentionally contains two buttons labelled 23 in different month regions. A global locator would be ambiguous. Scoping to September selects the intended date and verifies the stored ISO value. This small example isolates the central problem in larger jQuery/Bootstrap calendars without requiring an external demo website.

### Browser dialogs versus HTML modals

`alert`, `confirm` and `prompt` are browser dialogs. An HTML modal is ordinary page content and should be located by its dialog role or container. Register a browser dialog handler before the action that opens it:

```python
def test_confirm_accept(page):
    page.set_content('''<button onclick="document.body.dataset.ok=confirm('Close paper?')">
      Close paper position</button>''')
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Close paper position").click()
    assert page.locator("body").get_attribute("data-ok") == "true"
```

Write a separate dismiss test and verify no mutation occurs. A handler that observes but never accepts or dismisses can stall the action. The [dialog guide](https://playwright.dev/python/docs/dialogs) describes this lifecycle.

### Frames contain another document

```python
from playwright.sync_api import expect

def test_frame(page):
    page.set_content('''<iframe title="Paper help"
      srcdoc="<button>Read risk notice</button>"></iframe>''')
    button = page.frame_locator('iframe[title="Paper help"]').get_by_role("button")
    expect(button).to_have_text("Read risk notice")
```

Use [frame locators](https://playwright.dev/python/docs/frames) rather than assuming a top-level locator searches another document. Real broker authentication can involve redirects or external frames; mock the boundary in routine CI and never automate a real customer's PIN as a learning exercise.

### Practice and checkpoint

Test accepting and cancelling a paper-only close dialog. Explain why a browser confirmation dialog cannot be found with `page.locator('.modal')`. They are different UI mechanisms with different automation APIs.

## Chapter 24. Mouse, Keyboard, Uploads and Downloads

### Interactions should express user intent

Use `hover()` for tooltips, `dblclick()` for a deliberate double-click action, and `click(button='right')` for a context menu. A drag operation needs a defined source and target; use `drag_to()` where appropriate and assert the resulting order or value. Coordinate clicks are a last resort for surfaces such as canvases and should be tied to stable geometry.

Keyboard tests reveal accessibility and focus defects. Tab, Enter and Escape test navigation and dismissal. Use `ControlOrMeta` for platform-aware select-all shortcuts when supported by the installed version. `fill()` is preferable for normal text entry; sequential typing is useful only when the feature depends on individual key events.

### Upload files without operating the OS dialog

```python
def test_upload(page, tmp_path):
    file = tmp_path / "symbols.csv"
    file.write_text("symbol,qty\nTEST,2\n", encoding="utf-8")
    page.set_content('<label>Import symbols<input type="file"></label>')
    control = page.get_by_label("Import symbols")
    control.set_input_files(file)
    assert control.evaluate("el => el.files.length") == 1
    assert control.evaluate("el => el.files[0].name") == "symbols.csv"
```

Upload tests should cover file type, size, missing headers, malformed records and partial import policy. Client-side validation alone is insufficient; API tests must bypass it and verify backend rejection. Never test malware against an unauthorized public system. Use harmless synthetic strings to test sanitization contracts.

### Downloads need both event and content checks

Recipe for a page with an Export CSV control:

```python
import csv

def check_export(page, tmp_path):
    with page.expect_download() as pending:
        page.get_by_role("button", name="Export CSV").click()
    download = pending.value
    target = tmp_path / "positions.csv"
    download.save_as(target)
    with target.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["symbol"] == "TEST"
```

Register the wait before clicking so a fast download is not missed. Choose your own safe destination rather than trusting a remote filename as a filesystem path. Verify headers, records, encoding and formula-injection handling when spreadsheets may open exported values. The [download guide](https://playwright.dev/python/docs/downloads) covers the event API.

### Practice and checkpoint

Test an empty CSV and a valid two-row CSV. Decide whether one invalid row rejects the whole import or reports partial success; both need explicit acceptance criteria. Verify that a failed upload does not silently create half a strategy configuration.

## Chapter 25. Browser Contexts, Tabs and Authentication State

### Isolate sessions deliberately

A new page in the same context shares cookies. A new context has a separate session. Use two contexts to test that one account cannot see another account's data; two tabs in one context do not provide that isolation. Context storage state is convenient for authenticated tests but is also a credential-bearing artifact.

Recipe for a paper UI that opens help in a new tab:

```python
from playwright.sync_api import expect

def open_help(page):
    with page.expect_popup() as pending:
        page.get_by_role("link", name="Help").click()
    help_page = pending.value
    expect(help_page.get_by_role("heading", name="Risk guide")).to_be_visible()
```

Register popup waits before triggering them. Use the returned page, not an assumption about `context.pages[1]`, because unrelated tabs may exist. Closing a popup should not log the original page out unless that is an explicit application policy.

### Reuse login carefully

After a synthetic test login, save state to an ignored temporary path. Start another context with that state and verify access. Also test expiry and logout: stale cookies must no longer grant protected access. [Playwright authentication guidance](https://playwright.dev/python/docs/auth) explains storage-state reuse and its sensitivity.

For admin/TOTP in the real project, routine UI tests should use a test identity and controllable authenticator time in an isolated environment. Do not disable production authentication to make a curl command easier. A terminal request does not inherit the session cookie from your browser, explaining why an already logged-in user can still receive `ADMIN_AUTH_REQUIRED` from curl.

### Multi-user and cross-strategy boundaries

Test account A requesting account B's positions with a modified query parameter. Expect denial or enforced A-only scope according to the API design. Separately test strategy A's exit alert against strategy B's symbol. Authentication answers who you are; authorization answers what you may do. A single-admin app still needs authorization on sensitive API routes because someone can call them without using the dashboard.

### Practice and checkpoint

Open two independent contexts with no real credentials and use a local cookie demonstration. Set a cookie in one, assert it is absent in the other. Then use two pages in the first context and observe sharing. Explain why worker-specific accounts or per-test state are necessary for parallel authenticated tests that modify settings.

## Chapter 26. Waiting, Screenshots, Video, Traces and Flaky Tests

### Wait for evidence, not arbitrary time

Page navigation can finish before a dashboard is useful. `networkidle` is especially unsuitable as a universal readiness signal for polling or streaming apps. Wait for a meaningful state: a configuration heading, a loaded form, or a particular response. A button being visible does not prove its data was loaded.

```python
from playwright.sync_api import expect

def save_and_check(page):
    with page.expect_response(lambda r:
        r.url.endswith("/api/strategies") and r.request.method == "POST"
    ) as pending:
        page.get_by_role("button", name="Save strategy").click()
    assert pending.value.status == 201
    expect(page.get_by_role("status")).to_have_text("Strategy saved")
```

This recipe uses the lab endpoint. With production routes, update both the URL and documented success status. Waiting for any response with a similar path can accidentally match a GET rather than the save POST.

### Preserve failure evidence

```bash
python -m pytest -m browser --tracing retain-on-failure --screenshot only-on-failure --video retain-on-failure
python -m playwright show-trace test-results/PATH/trace.zip
```

Use the actual generated trace path. These pytest plugin options apply to plugin-managed contexts; a context you create manually may require explicit recording and closure. Screenshots capture final appearance, video shows visible timing, and traces show actions, DOM snapshots and network information. See the [trace viewer](https://playwright.dev/python/docs/trace-viewer). Restrict artifact access and retention because requests and screenshots may contain private data.

### Classify flakiness

Common sources are shared state, unstable selectors, missing waits, genuine races, variable resources and external dependencies. Record repeat frequency, environment and the first failing assertion. Re-running until green hides evidence. A bounded diagnostic rerun can distinguish reproducibility, but release reporting must keep the original failure visible.

In this project's browser smoke test, checking a JavaScript function before dashboard initialization produced intermittent undefined-function errors. The fix was explicit readiness, not changing financial behavior. Another test demanded a 50 ms scheduling result on a busy workstation. Replacing that assertion with event-controlled ordering tested concurrency more reliably; a separate benchmark is needed for latency claims.

### Practice and checkpoint

Create a button that displays a message after a short asynchronous delay. Write one brittle immediate assertion, then replace it with an expectation. Explain why increasing every timeout to two minutes would slow diagnosis without fixing a wrong selector or broken server response.

## Chapter 27. Network Mocking and WebSocket Evidence

### Mock a boundary, not the whole feature

Routing lets a test supply a controlled response or abort a request. Use it to test frontend behavior for server errors, delays and malformed content. It does not test the real backend implementation for that endpoint. Keep complementary API tests so a mocked browser suite cannot pass while the real route is broken.

```python
from playwright.sync_api import expect

def test_read_failure_visible(page, lab_url):
    page.route("**/api/positions", lambda route: route.fulfill(
        status=502, content_type="application/json", body='{"detail":"UPSTREAM_DOWN"}'
    ))
    page.goto(lab_url)
    expect(page.get_by_role("status")).to_have_text("UPSTREAM_DOWN")
```

Also test an HTML error page. The frontend should present a useful safe message instead of a raw JSON parse error. Our deliberately small lab can be extended for this assignment; the production dashboard's fallback behavior must be tested through its own route. [Network documentation](https://playwright.dev/python/docs/network) describes interception and event observation.

### Two separate WebSocket tests

A broker-stream test feeds normalized or recorded synthetic packets into the market-feed adapter and verifies security-ID mapping, source, timestamp and downstream publication. A dashboard-stream test sends an app message to a connected browser and checks LTP/P&L rendering. Neither alone proves the full broker-to-risk-to-browser chain. Add a system test that joins those boundaries using a fake broker transport and disposable shared store.

Recipe requiring a page that actually creates a WebSocket:

```python
def capture_socket_frames(page, lab_socket_url):
    frames = []
    def on_socket(socket):
        socket.on("framereceived", lambda payload: frames.append(payload))
    page.on("websocket", on_socket)
    page.goto(lab_socket_url)
    return frames
```

The compact companion lab uses manual refresh and does not implement this endpoint. A robust stream test waits for a particular validated frame and UI outcome rather than immediately inspecting an initially empty list.

### Test failure sequences

Cover connect without ticks, subscription before credentials, symbol added after connection, reconnect preserving subscriptions, stale timestamp, out-of-order tick and malformed packet. A restart should not create an unlimited number of threads or event loops. A REST fallback price must remain labelled as fallback; it must not falsely certify the WebSocket healthy.

### Practice and checkpoint

Build a timeline: subscription requested at t=0, transport connected at t=1, tick received at t=3, UI updated at t=3.1. Name the evidence required at each step. Then delay the tick past the freshness budget. Expected behavior must follow the application's configured fallback/block policy, not an invented guarantee of instant execution.

## Chapter 28. Mobile, Cross-Browser and Parallel Testing

### A viewport is only one dimension

Test desktop, tablet and mobile widths with the same important workflows: configure, save, reload, observe a position and inspect an error. Check that text fits, controls remain reachable, tables scroll predictably and the document has no unintended horizontal overflow. Use DOM geometry assertions together with screenshots. A screenshot can look fine while a covered button is unclickable.

```python
import pytest
from playwright.sync_api import expect

@pytest.mark.parametrize("size", [(1280, 900), (768, 1024), (390, 844)])
def test_responsive_form(page, lab_url, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(lab_url)
    expect(page.get_by_role("button", name="Save strategy")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
```

Viewport changes are not full device emulation. Touch, device scale factor, user agent, virtual keyboard and actual hardware can reveal additional issues. Browser automation cannot claim that a site works on every phone. Include manual checks on representative real devices where the audience requires them.

### Browser engines and parallel workers

```bash
python -m playwright install chromium firefox webkit
python -m pytest -m browser --browser chromium --browser firefox --browser webkit
python -m pytest -m browser -n 2 --browser chromium
```

The second command runs the selected engines using the pytest plugin. The third requires `pytest-xdist`. More workers may make a small machine slower due to memory and CPU contention. Measure total duration and failures before increasing parallelism. The [plugin reference](https://playwright.dev/python/docs/test-runners) documents browser and worker options.

Each worker needs independent server state, data identities and artifact paths. The companion `lab_url` binds an available port and creates a fresh app per test. A shared fixed port or a global `demo` record on one server would cause cross-worker interference. Do not share a mutable browser Page across threads.

### Practice and checkpoint

Run the lab browser suite serially and with two workers. If parallel execution fails, inspect ports, shared IDs and fixture scope before adding retries. Record which engines actually ran; a Chromium-only result is not cross-browser evidence, even if WebKit was installed successfully.
