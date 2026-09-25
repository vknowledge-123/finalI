"""Check the offline HTML reader, not the production trading application."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parents[1] / "tmp/pdfs/testing-book"
OUT.mkdir(parents=True, exist_ok=True)
results = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    try:
        for width, height in [(1440, 1000), (390, 844)]:
            page = browser.new_page(viewport={"width": width, "height": height})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto((ROOT / "BOOK.html").as_uri(), wait_until="load")
            assert page.locator('main h2').count() == 46
            broken = page.evaluate('''() => [...document.querySelectorAll('nav a')]
              .filter(a => !document.getElementById(decodeURIComponent(a.hash.slice(1))))
              .map(a => a.hash)''')
            assert not broken, broken
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            page.screenshot(path=str(OUT / f"reader-{width}.png"))
            assert not errors, errors
            results.append({"width": width, "chapter_and_appendix_links": 46, "page_errors": errors})
            page.close()
    finally:
        browser.close()
print(json.dumps(results))
