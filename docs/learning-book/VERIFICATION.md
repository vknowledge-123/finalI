# Expanded edition verification

Verified locally on 23 September 2026. These results concern the learning book and its isolated paper lab, not a production broker integration audit.

## Content and PDF

- 60 numbered chapters; all 46 original chapters have an additional worked lesson.
- 107 fenced code blocks; 43 Python examples pass AST syntax parsing. Syntax parsing does not prove every teaching fragment can run independently.
- Approximately 28,400 prose words, plus code listings.
- 120 PDF pages with automatic contents, 60 chapter bookmarks, page numbers, embedded fonts, and official documentation links.
- Every PDF page rendered. All 10 contact sheets visually reviewed, with a full-page cloud-command check.
- Automated PDF audit found no text outside safe page bounds, unresolved source includes, replacement glyphs, or pages under 100 extracted characters.
- Long code lines can wrap for print. Use the editable sources for exact indentation and commands.

## Executed tests

From `lab/`, run in a test environment with the dependencies in `requirements.txt` and the Playwright browser installed as described in its README:

```bash
python -m pytest -q tests/test_domain.py tests/test_api.py
python -m pytest -q tests/test_browser.py
```

Results: **37 domain/API tests and 3 Playwright browser tests passed**. Browser tests launch the actual disposable loopback paper API, operate the UI, and test target exit and non-JSON server-error handling. No real orders are placed.

The offline HTML reading edition was checked with Playwright at widths 1440, 820, and 390 pixels. Chapter navigation/search and image loading passed, with no JavaScript page errors or document-level horizontal overflow.

## Boundaries

- Docker, kind, kubectl, Terraform, and Google Cloud deployments were not executed in this environment. Those are reader labs, not certified production manifests.
- Kubernetes YAML is structurally parsed locally; real admission, IAM, CNI enforcement, image pulling, and workload behavior must be tested on the chosen cluster.
- No real Dhan/Zerodha order, email delivery, cloud-billing action, or live-market test was performed for this book.
- The paper lab is deliberately unauthenticated, single-replica, SQLite-based, and restricted to local/private access. Its container data is ephemeral. Implement the authentication and durable-state assignments before considering a separate production design.
- Educational dependency pins are reproducibility choices, not a declaration that those versions remain secure. Review supported versions and advisories before deployment.
- This book provides a certification study path and practice, not a guarantee of exam coverage, a pass, or senior-developer employment. Check the current official guide for the exam you choose.

Production trading application code was not changed for this edition.
