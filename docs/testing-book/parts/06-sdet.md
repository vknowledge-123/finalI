# Part VI. Delivery, Reliability and SDET Practice

## Chapter 35. Git, GitHub and Reviewable Test Changes

### Version the reasoning as well as the code

Git records project history; GitHub hosts repositories and collaboration workflows. A commit should represent a coherent change with its tests. For a defect fix, include the smallest reproducer, implementation and explanation of risk. Do not commit credentials, browser storage state, downloaded customer reports, virtual environments or giant failure videos.

Recipe in your own practice repository, not a command to publish this client's code:

```bash
git status
git switch -c test/breakout-expiry
git diff
git add tests/test_breakout.py
git diff --cached
git commit -m "Test breakout expiry at the exact deadline"
```

Inspect the staged diff before committing. `git add .` can include unrelated files or secrets. A `.gitignore` helps with generated artifacts but does not remove a secret already committed. If exposure occurs, revoke/rotate the secret and follow a history-cleanup policy; deleting it in the next commit alone does not revoke access.

### Pull request review

A good test PR explains requirement, reproduction, changed behavior, verification and limitations. Reviewers should ask whether the test would fail before the fix, whether it uses an independent oracle, whether data is isolated, and whether cleanup runs on failure. Check that a mock did not remove the exact boundary implicated in the defect.

Merge conflicts in tests are not solved by always accepting one side. If two branches add different cases to the same parametrized list, both may be necessary. Read surrounding behavior and rerun the affected suite. Avoid rebasing or force-pushing shared history without team agreement.

### Learning from repository history

Use `git log --oneline`, `git show COMMIT` and `git blame` to understand why a rule exists. A blame result identifies the most recent line change, not moral responsibility. A formatter may own the line while an older commit introduced the behavior. Read related tests and issue context.

### Practice and checkpoint

Create a branch, add a regression test for a zero retry value, and write a PR description with five fields: symptom, cause hypothesis, test evidence, behavior change and remaining risks. Do not claim a live broker fix based solely on a fake response.

## Chapter 36. GitHub Actions and Jenkins Pipelines

### CI repeats a documented local process

Continuous integration runs checks for proposed changes in a controlled environment. It should start with dependency installation, collection/syntax checks, unit and API tests, then selected browser tests. Keep deployment permissions separate from test execution. Untrusted pull requests must not receive production secrets or privileged self-hosted access.

This GitHub Actions recipe assumes the standalone companion lab is the repository root. For the full trading repository, set a working directory and artifact paths deliberately. Review and pin action revisions according to your organization's supply-chain policy before production use.

```yaml
name: paper-lab-tests
on: [push, pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python -m pip install -r requirements.txt
      - run: python -m playwright install --with-deps chromium
      - run: python -m pytest --browser chromium --junitxml=artifacts/junit.xml --tracing retain-on-failure
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: test-evidence
          path: |
            artifacts/
            test-results/
          retention-days: 7
```

The version tags above are example compatible action major versions, not a claim they are the newest. A mature pipeline pins reviewed immutable revisions and updates them deliberately. The [GitHub Python CI guide](https://docs.github.com/en/actions/tutorials/build-and-test-code/python) explains the workflow structure. Store a reviewed dependency lock for repeatable builds; the lab ranges are a starting point.

### Jenkins declarative pipeline

This recipe requires a Linux agent with Python, browser OS dependencies and the standalone lab checked out. Installing OS packages should be an agent-image responsibility where possible, not unrestricted sudo granted to tests.

```groovy
pipeline {
  agent { label 'python-browser' }
  options { timeout(time: 15, unit: 'MINUTES') }
  stages {
    stage('Checkout') { steps { checkout scm } }
    stage('Environment') {
      steps {
        sh 'python3 -m venv .venv'
        sh '.venv/bin/python -m pip install -r requirements.txt'
        sh '.venv/bin/python -m playwright install chromium'
      }
    }
    stage('Test') {
      steps {
        sh '.venv/bin/python -m pytest --junitxml=artifacts/junit.xml'
      }
    }
  }
  post {
    always {
      junit allowEmptyResults: false, testResults: 'artifacts/junit.xml'
      archiveArtifacts allowEmptyArchive: true,
        artifacts: 'artifacts/**,test-results/**'
    }
  }
}
```

Consult [Jenkins pipeline syntax](https://www.jenkins.io/doc/book/pipeline/syntax/) for agent and plugin requirements. A shell failure must fail the stage. `allowEmptyArchive` for optional screenshots must not become an excuse for accepting no test results. Publish evidence even on failure, with sensitive data redacted and retention limited.

### Practice and checkpoint

Run a deliberately failing assertion in a disposable branch. Confirm CI is red and the report is still available. Then simulate a missing dependency and distinguish installation failure from a failed application test. Record which stage owns each failure.

## Chapter 37. Codegen, Playwright MCP, PyCharm and Copilot

### Generation accelerates discovery, not judgment

Playwright Codegen records browser interactions and suggests locators. Start against the local paper lab only:

```bash
python -m playwright codegen --target python-pytest http://127.0.0.1:8019
```

Inspect the generated code. Add assertions for backend persistence and important negative outcomes. Replace accidental selectors and remove unnecessary clicks. The recorder knows what you did, not why the result is correct. A recorded test that logs in with a real password may embed that password in generated code. Use synthetic identities and review before saving or sharing. See [Codegen documentation](https://playwright.dev/python/docs/codegen).

### What MCP adds

Model Context Protocol lets an AI client use tools exposed by a server. The [official Playwright MCP server](https://github.com/microsoft/playwright-mcp) enables browser interaction through that protocol. It is distinct from the Python pytest runner. An AI-guided exploration can discover a workflow; deterministic pytest tests remain the repeatable regression artifact.

A configuration recipe uses a reviewed, explicitly selected package version:

```json
{
  "mcpServers": {
    "paper-browser": {
      "command": "npx",
      "args": ["@playwright/mcp@REVIEWED_VERSION"]
    }
  }
}
```

Replace `REVIEWED_VERSION` with a version you have reviewed before using this recipe. It is intentionally not a runnable unpinned installation command. Node.js/npm must be available for this server. Exact configuration keys and locations depend on the client; follow that client's current instructions rather than assuming one JSON file works everywhere.

### PyCharm and Copilot workflow

In PyCharm, select the lab's Python interpreter and verify pytest runs normally first. Configure the desired MCP client using its settings, add the reviewed Playwright server and inspect exposed tools. JetBrains AI Assistant can act as an MCP client; PyCharm's built-in IDE MCP server is a different direction of integration. Read [JetBrains MCP client guidance](https://www.jetbrains.com/help/ai-assistant/mcp.html) and [GitHub Copilot MCP guidance](https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/extend-copilot-chat-with-mcp) for the installed edition/plugin.

Start a fresh browser profile with no real account cookies. Allow only the local lab workflow. Ask the assistant to inspect labels, propose test cases and explain assertions. Do not approve arbitrary shell commands or production navigation simply because a generated plan sounds confident. Page content is untrusted input and must not override your test scope.

### Practice and checkpoint

Record a save-strategy flow, then add API readback and a wrong-quantity test yourself. Explain why an AI-generated green result is not sufficient without reviewing setup, assertions, side effects and the target URL. Your SDET skill is the ability to validate the automation, not merely invoke it.

## Chapter 38. Performance, Resilience and Observability

### Separate speed claims from correctness tests

Latency is elapsed time for an operation. Throughput is work completed per unit time. Concurrency is work in progress. Averages can hide bad tail behavior; report percentiles, error rates and queue age. Define measurement points: webhook acknowledgment, decision completion, broker acceptance, confirmed fill and UI update are different durations.

A reproducible load test specifies hardware, dependency mode, data, arrival pattern, warm-up, duration and stop conditions. Start with a fake broker and staging infrastructure. Never load-test real order APIs. A closed-loop client waits for each response before sending another; it can hide overload because it slows down when the server slows. An arrival-rate test is useful for webhook bursts, with bounded safety limits.

### A measurement exercise

```python
import time
from statistics import median

def measure(call, samples=100):
    durations = []
    for _ in range(samples):
        start = time.perf_counter()
        call()
        durations.append(time.perf_counter() - start)
    ordered = sorted(durations)
    index = max(0, int(.95 * len(ordered) + .999999) - 1)
    return {"median": median(ordered), "p95": ordered[index]}
```

This serial micro-measurement teaches percentiles; it is not a representative production load test. Record environment and payload sizes. Use a mature load tool for sustained concurrent HTTP workloads and validate its metrics independently. Do not choose a performance threshold solely because your laptop happened to achieve it once.

### Recovery is part of correctness

Test Redis temporarily unavailable, broker timeout, SDK malformed response, worker termination, token rotation, stale feeds, disk pressure and graceful shutdown. Inject one fault at a time initially. Verify that no extra order is placed, unresolved state is visible, and recovery does not erase evidence. A kill switch can block new entries while exit/reconciliation behavior follows a separately defined policy; test that distinction.

Repeated reconnects can leak sockets, tasks or file descriptors. A soak test records resource counts over many cycles and checks they return toward a stable baseline. A process being active does not mean useful work is progressing. Monitor last successful tick, queue lag, oldest unresolved order, reconciliation failures and per-service heartbeats.

### Safe operational inspection

Read-only commands on an authorized staging machine can include `systemctl is-active`, recent `journalctl --no-pager` entries, disk usage and memory usage. Redact credentials before sharing logs. A Redis authentication error is different from Redis being slow; investigate the connection identity and active configuration rather than immediately increasing machine size.

### Practice and checkpoint

Design a recovery test where a fill arrives after a network timeout. The system should reconcile the existing intent before retrying. State how you would prove there was no duplicate submission and how long unresolved state may remain before operator intervention is required.

## Chapter 39. Full-Project Feature Matrix and Capstone

### Convert feature lists into evidence

The table below is a **test-design inventory**, not a claim that every row is implemented in the companion lab or verified against a live broker. Map it to the current production code and record actual results. The lab provides a safe foundation; advanced features are capstone extensions.

| Feature | Essential positive case | Essential failure or invariant |
| --- | --- | --- |
| Admin setup | Allowed identity claims once | Concurrent or unauthorized claim rejected |
| Password/TOTP | Valid two-step login | Expiry, replay policy, logout, throttling |
| Broker credentials | New credentials reach workers | Old client not reused after token change |
| Webhook URL | Correct configured origin | Old VM origin warns; secret not leaked |
| Alert names | Intended normalization matches | Collision and missing config explicit |
| Sizing | Fixed and capital policy | Zero, negative, expensive stock policy |
| Entry window | Boundary-time acceptance | After end blocked, timezone fixed |
| Trade limits | Allowed reservation | Concurrent entries cannot exceed policy |
| Sector cache | Valid previous session close | Zero denominator and stale cache rejected |
| Sector ranking | LONG gainers, SHORT losers | Unknown sector and stale ranking explicit |
| Breakout | Exact previous closed bar | No forming bar substitution |
| Breakout TTL | Cross within original TTL | Retry/restart cannot extend deadline |
| Tick stream | Resolve, subscribe, consume | Missing ID, stale/out-of-order tick |
| Fallback | Labelled REST price | Must not claim WebSocket healthy |
| Entry execution | Confirm actual fills | Acceptance alone never full position |
| Partial fills | Position uses filled quantity | Retry only reconciled remainder |
| Limit price | Side-aware tick alignment | Circuit/funds/unknown rejection handling |
| Stop and target | BUY and SELL exact boundary | No exit from unrelated strategy |
| Trailing stop | Favourable movement updates | BUY stop never decreases |
| Cost stop | Trigger at configured RR | Disabled toggle prevents application |
| Pyramiding | Confirm add, update average | Failed add does not inflate quantity |
| CNC carry | Restore owned carry records | Empty holdings alone not proof of sale |
| Auto square-off | Intended product/time scope | CNC policy not silently treated as MIS |
| Daily P&L | Threshold and scope correct | App kill and broker kill distinguished |
| Alert exits | Matching owner and symbol | Other strategy unaffected |
| Reconciliation | Missing updates recovered | External/manual trades not misattributed |
| Telegram | Enabled strategy emits once | Disabled, token failure, duplicate message |
| Backtest | Deterministic known candles | CPU task does not block live event loop |
| Dashboard | Correct row identity and P&L | Skipped/rejected row cannot become LIVE |
| Restart | State/ownership restored | Duplicate workers cannot double-submit |

### A staged capstone

Stage 1: write a manual test plan, ten defects seeded in your disposable fork, and a traceability matrix. Stage 2: implement domain tests for targets, stops, tick sizes and sizing. Stage 3: add API validation and idempotency cases. Stage 4: build browser configuration and position flows. Stage 5: add a fake delayed broker, partial fills, a fake clock and a durable store. Stage 6: run a CI pipeline and publish a redacted report with limitations.

Your deliverables are a runnable repository, architecture diagram, test plan, automated tests, sample failure trace, defect reports, CI evidence and release recommendation. Demonstrate one bug by making the regression test fail before the fix and pass after it. Do not submit screenshots of green badges without the code that produced them.

### Review criteria

Reviewers should be able to set up the project from a clean machine, understand its safety boundary, run unit/API tests without broker credentials, and reproduce a browser scenario. They should see why each critical expected result is correct. They should also see what remains untested. Senior judgment includes declining unsupported safety claims.

### Practice and checkpoint

Choose five rows from the matrix and assign each a unit test, integration test and manual observation. For at least one feature, explain why a browser-only test is insufficient. For reconciliation, the answer should include broker evidence and durable state rather than only a dashboard label.

## Chapter 40. From Beginner to SDET: Study Plan and Interview Practice

### A realistic learning path

An SDET combines programming, test design, debugging, system understanding and delivery engineering. Completing a course or book helps, but job readiness comes from independently solving problems and explaining tradeoffs. No book can guarantee a job title, salary or seniority. Use these milestones as evidence of growth, not a calendar promise.

| Stage | Suggested focus | Exit evidence |
| --- | --- | --- |
| Weeks 1-2 | Manual fundamentals and risk | Test plan, boundaries, five clear defects |
| Weeks 3-5 | Python and small algorithms | Own calculations with edge-case tests |
| Weeks 6-7 | Pytest and API contracts | Isolated fixtures, negative cases, coverage review |
| Weeks 8-10 | Playwright and framework | Reliable locators, tracing, mobile flows |
| Weeks 11-12 | CI, Git, reporting | Pipeline catches a seeded failure |
| Weeks 13-16 | Distributed failures and capstone | Reconciliation/race tests and release report |

Study time varies. Repeat a stage until you can explain and implement its evidence without copying. Spend roughly as much time debugging and designing tests as watching lessons. After each chapter, close the book and rebuild the example from its requirement. Compare afterward and explain differences.

### Coding skills for interviews

Practice strings, dictionaries, sets, sorting, searching, stacks, queues and complexity analysis. Useful project exercises include deduplicating alerts while preserving order, merging order updates by identity, detecting missing sequence numbers and finding top-N sectors. Explain why a dictionary lookup is typically efficient and why retaining an unbounded event list is a memory risk. Do not memorize algorithms without connecting them to data contracts.

### System-design discussion

Draw client -> API -> queue -> workers -> store -> broker, plus the return path for fills and ticks. Discuss ownership, idempotency, retries, observability and failure recovery before choosing many microservices. Explain which state must survive restart and what can be recomputed. Describe how you would test each boundary and how a test environment differs from production.

Interview prompts: How do you prevent duplicate orders after a timeout? How do you isolate parallel tests? Why can a 200 response be semantically wrong? How do you test a 9:00 IST job without waiting until tomorrow? How do you distinguish a flaky assertion from a real race? How do you test authentication without storing real credentials?

### Answer model

For duplicate orders, say: record a durable intent/correlation identity, treat timeout as unknown, query broker evidence, apply confirmed fills idempotently, and only retry a known remaining quantity under ownership control. Then describe the test: accept remotely, drop the response, restart the worker, redeliver the same event, and assert no second full order. This is much stronger than saying simply to add three retries.

### Final checkpoint

Can another beginner reproduce your tests? Can you explain every assertion? Can you diagnose a deliberately broken dependency? Can you state the limits of your evidence without becoming defensive? If yes, you are developing the habits expected of an effective SDET. Continue with production-like staging exercises, peer review and deeper database/network/security study rather than treating the final page as the end of learning.
