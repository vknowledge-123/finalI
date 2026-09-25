# Verification and Limits

Edition date: 24 September 2026.

## Delivered content

- 40 numbered chapters, 6 appendices, 18,591 prose words and 76 code blocks.
- 76-page PDF with chapter bookmarks, linked contents and page numbering.
- Offline responsive HTML, editable Markdown/parts and a companion ZIP.
- 48 manual test-case templates, worked debugging drills, syllabus mapping,
  feature inventory, capstone, study roadmap and release-report template.
- Runnable paper-only FastAPI/pytest/Playwright lab, including synthetic
  browser-control exercises. No brokerage SDK or real-order capability.

## Checks actually run

- All 40 lab tests passed serially: 22 domain/API cases and 18 browser cases.
- All 40 lab tests passed with two pytest-xdist workers.
- Browser runs used installed Google Chrome through Playwright's Chromium
  channel, not Firefox or WebKit. Cross-browser commands are supplied as an
  exercise; those other engines were not executed for this edition.
- Browser integration exercised form save/API readback, synthetic entry,
  tick-triggered exit, P&L, mobile layout, controls, tables, calendar, dialog,
  iframe, shadow root, upload/download, custom dropdown, pagination, popup,
  keyboard, cookie isolation and mocked API failure.
- Offline reader checked at 1440x1000 and 390x844: valid navigation targets,
  no JavaScript errors and no document-level horizontal overflow.
- All 47 Python teaching snippets before the source appendix passed syntax
  parsing. Syntax parsing is NOT execution. Recipes requiring additional
  endpoints, authentication, optional dependencies or HTML remain exercises.
- Companion Python files were parsed by the builder and executed through the
  stated tests. The lab's advanced security and broker-failure assignments
  are not implemented production features.
- Entire PDF rendered to PNG for visual review. Automated audit checked all
  pages for near-empty output, replacement characters and out-of-page text;
  no issues remained. All 40 chapter bookmarks were present.
- PDF rendered using PyMuPDF because Poppler was not available in this
  environment. Representative full-size pages and all-page contact sheets
  were reviewed. Six long source-code lines wrap in print; companion source
  preserves exact indentation and is the canonical executable copy.

## Environment and remaining warnings

Executed on Windows with Python 3.13, FastAPI 0.115.6, Pydantic 2.10.4,
Playwright 1.63.0 and pytest-playwright 0.9.0. The serial runner used the
existing pytest 8.3.4 environment; isolated additional tooling was installed
under a temporary directory, without changing the application's dependencies.
The parallel workers used the temporary tool environment. This is not a
cross-Python-version compatibility certification.

Two Uvicorn/websockets deprecation categories appeared; they did not fail the
tests. Parallel workers emitted the same categories independently.

## Explicit exclusions

No real orders, broker logins, public webhooks, client Redis instances, external
email or Telegram delivery, production deployment, live exchange execution,
cloud load tests or penetration tests were performed. The production app was
not modified to create the book. Passing the lab does not certify the production
app, guarantee employment, or prove that all possible defects are absent.

Jenkins and GitHub Actions pipelines, Allure reporting, OAuth/TOTP extension
tests, actual third-party widget libraries and MCP setup are documented recipes,
not remotely deployed or executed services in this verification run.
