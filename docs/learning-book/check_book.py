"""Offline HTML QA using Playwright. Requires installed Chrome or Chromium."""
import ast
import json
from pathlib import Path
import re

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "__pycache__" / "book-qa"
OUTPUT.mkdir(parents=True, exist_ok=True)
text = (ROOT / "BOOK.md").read_text(encoding="utf-8")
examples = re.findall(r"```python\n(.*?)\n```", text, flags=re.DOTALL)
for example in examples:
    ast.parse(example)

results = []
with sync_playwright() as playwright:
    try:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
    except Exception:
        browser = playwright.chromium.launch(headless=True)
    try:
        for name, width, height in [("desktop", 1440, 1000), ("tablet", 820, 1180), ("mobile", 390, 844)]:
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto((ROOT / "BOOK.html").as_uri())
            page.wait_for_function("Array.from(document.images).every(image => image.complete && image.naturalWidth > 0)")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
            assert page.locator("main h2").filter(has_text=re.compile(r"^Chapter \d+ ")).count() == 46
            page.screenshot(path=str(OUTPUT / f"{name}-opening.png"))
            page.get_by_label("Find a chapter").fill("TOTP")
            matching = page.locator("nav a:visible").filter(has_text="Chapter 15")
            assert matching.count() == 1
            matching.click()
            assert "chapter-15" in page.url
            page.get_by_label("Find a chapter").fill("")
            page.locator("#chapter-26-playwright-with-python").scroll_into_view_if_needed()
            page.screenshot(path=str(OUTPUT / f"{name}-testing.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
            broken = page.evaluate("""() => Array.from(document.querySelectorAll('nav a[href^="#"]'))
                .filter(a => !document.getElementById(decodeURIComponent(a.hash.slice(1)))).map(a => a.hash)""")
            assert not broken, broken
            assert not errors, errors
            results.append({"viewport": name, "width": width, "page_errors": errors,
                            "horizontal_overflow": False, "chapter_navigation": "passed", "image_loaded": True})
            context.close()
    finally:
        browser.close()
(OUTPUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results, indent=2))
