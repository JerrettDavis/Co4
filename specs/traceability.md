# Behavioral traceability

The Gherkin files in this directory are the product contract **and are executed** by
[pytest-bdd](https://pytest-bdd.readthedocs.io/) as part of the normal `pytest` run
(`bdd_features_base_dir = "specs"` in `pyproject.toml`). Each feature has one step-definition
module; the steps reuse the same fixtures and helpers as the unit-level suites, so a scenario and
its pytest assertions exercise one code path.

| Feature | Step definitions | Notes |
|---|---|---|
| `contribution.feature` | `tests/test_bdd_contribution.py` | All 5 scenarios run on every platform. |
| `interactive.feature` | `tests/test_bdd_interactive.py` | The native-terminal outline (claude/codex/copilot) and *Stop and recover* use a real POSIX PTY and are skipped on Windows (run them in WSL/Linux CI). *Do not silently switch…*, *Do not invent accounting…* and *Preserve existing configurations…* replace only the PTY relay with a simulated native CLI and run everywhere. |
| `device-flow.feature` | `tests/test_bdd_device_flow.py` | GitHub is simulated at the HTTP boundary with `httpx.MockTransport`; the real `co4.github.GitHub` gateway runs. |

Run only the executable specifications with `pytest tests/test_bdd_*.py`.

## Supporting unit-level coverage

| Contract | Automated coverage |
|---|---|
| Validation, verification, consent and scoped access | `test_governance.py`, `contribution.feature` |
| Simultaneous allocation, reservation and per-user limit | `test_governance.py` |
| 12-hour stale and 12-hour recovery boundaries | `test_handover.py`, `contribution.feature` |
| Real baseline/red/green/verify workflow and human gate | `test_worker.py`, `contribution.feature` |
| Phase file boundaries and immutable regression tests | `test_worker.py`, `contribution.feature` |
| Process timeout, loss of coordination and private traces | `test_worker.py` |
| Native terminal execution, no print-mode fallback, terminal accounting | `test_interactive.py`, `interactive.feature` |
| HMAC, delivery replay, OAuth, issue changes and App removal | `test_integrations.py` |
| App-controlled draft SHA, remote checkpoint and outbox retries | `test_integrations.py` |
| GitHub device-flow endpoints, HttpOnly session cookie, loopback HTTP serve mode | `test_device_flow.py`, `device-flow.feature` |
| GitHub base-URL test seam (`CO4_GITHUB_URL`, `CO4_GITHUB_API_URL`) | `test_github_urls.py` |
| Maintainer and contributor UI workflow (browser) | `scripts/browser_smoke.py` (CI `browser` job) |
| Device-flow sign-in UI transition, HttpOnly cookie invisible to `document.cookie`, denied/expired/cancel (browser) | `scripts/device_flow_e2e.py` (CI `browser` job) |

`scripts/device_flow_e2e.py` starts `co4 serve` on loopback HTTP, points it at a local stdlib
stand-in for github.com via `CO4_GITHUB_URL`/`CO4_GITHUB_API_URL`, and drives headless Chromium
through the real sign-in dialog and verification page. It needs `python -m playwright install chromium`.

See `docs/testing.md` for actually executed results and limitations.
