# Historical release verification: 0.1.0-alpha.1

Verified on September 24, 2026. These results describe the delivered implementation, not a certification of third-party services or production security.

## Results

| Verification | Result | What actually ran |
|---|---|---|
| Automated Python suite | **61 passed, 0 failed, 0 skipped**, 21.31 seconds | Pytest against real SQLite databases, HTTP application clients, subprocesses, Git repositories, and deterministic GitHub HTTP mocks |
| Browser interaction walkthrough | **9 checks passed, 0 JavaScript errors** | Actual Chromium DOM and application JavaScript; Python HTTP bridge to a running control plane |
| Installed-package walkthrough | **6 checks passed** | Wheel installed outside the source tree; real HTTP, installed CLI, real Git commits, failing/passing regression tests, and human-approved fixture publication |
| JavaScript syntax | Passed | `node --check src/co4/static/app.js` |
| Python compilation | Passed | `python -m compileall -q src scripts` |
| Wheel build | Passed | Setuptools wheel build, followed by installation and the installed-package walkthrough |

The browser and installed-package checks are separate walkthrough checks, not additional pytest test cases. No coverage percentage, throughput target, uptime guarantee, or security-audit result is asserted.

Machine-readable evidence is included in the alpha.1 pytest report in the original alpha.1 archive, [browser-test-report.json](browser-test-report.json), and [package-test-report-alpha1.json](package-test-report-alpha1.json). Screenshots in `docs/images/` were captured during the browser walkthrough and inspected visually.

## What the automated suite exercises

The policy tests cover validation, repository membership and verification, roles, label restrictions, manual versus automatic allocation, denial, device revocation, one-active-allocation-per-user, budget reservation, unknown usage, and private-project visibility. A concurrent allocation test sends 12 competing requests against a file-backed database.

The handover tests exercise the 12-hour stale threshold, the separate 12-hour recovery interval after dibs, explicit recovery, old-generation fencing, and retained checkpoints. They also check that mere staleness does not silently reassign work.

The worker tests create actual local Git repositories and execute a real regression-test workflow: passing baseline, specification and scenarios, failing test, passing implementation, and repeated verification. Tests reject production changes during specification/test-writing phases, frozen-test tampering, incorrect review SHA/digest, unauthorized publication, unsafe checkpoint contents, and improperly matched test paths. Subprocess tests exercise idle termination, output capture, and heartbeat-failure termination.

Integration tests exercise HMAC validation, webhook deduplication, issue changes/closure/deletion, installation removal, OAuth state, encrypted credentials, human comment authorization, allocated branch enforcement, outbox retry/claim behavior, remote SHA verification, and draft-PR reconciliation. GitHub responses are deterministic HTTP mocks, not a live GitHub installation.

Harness tests validate command construction and representative exposed event/usage shapes for Claude Code, Codex CLI, and Copilot CLI. They do not establish authenticated interoperability with every installed CLI version or account configuration.

## Browser-test boundary

The available system Chromium blocks native URL navigation through an environment policy. The policy was not changed. The release walkthrough therefore loads the application's actual static assets into Chromium and bridges its API requests through a Python HTTP client to the live local server, using `--api-bridge`.

This verifies actual DOM rendering and event handlers, maintainership flows, filtering, policy editing, device enrollment, real worker execution, full-diff review, affirmative approval, simulated submission, and a 390px mobile layout without page-level horizontal overflow. It **does not certify native browser networking, browser cookie transport, or browser CSP enforcement**. HTTP security behavior has separate application tests.

The same script supports ordinary browser navigation without `--api-bridge`. The supplied GitHub Actions browser job uses that ordinary mode, but that remote CI job was not executed here.

## Installed-package boundary

The final wheel was installed into a separate virtual environment, and the server and CLI were invoked from outside the repository. Its Python package resolved from the installed wheel, not the source tree. The walkthrough confirms packaged assets, server startup, the installed worker workflow, no publication before authenticated approval, and dispatcher publication afterward.

Existing runtime dependencies were made available to that environment through site-packages because fresh external dependency downloads were unavailable. This is a separate wheel installation, **not a clean network dependency installation test**.

The fixture does not call a model, contact GitHub, spend credits, or publish a real PR. Its deterministic editing adapter solves the supplied fixture only. Git, subprocess execution, test failures, API coordination, and human review are real.

## Environment and reproduction

Release execution used Linux, Python **3.13.5**, Node **22.16.0**, Git, SQLite, and system Chromium. Runtime versions are pinned in `pyproject.toml`. Python 3.11 and 3.12 appear in the authored CI matrix but were not executed locally. Native Windows worker behavior was not tested; use WSL for the initial Windows pilot.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
pytest -q --junitxml=docs/pytest-results.xml
node --check src/co4/static/app.js
python -m compileall -q src scripts
python -m pip wheel --no-build-isolation --no-deps --wheel-dir dist .
```

For a normal browser walkthrough, install Playwright Chromium and start a fresh demo database. Do not reuse a database whose fixture issue has already completed.

```bash
python -m playwright install chromium
co4 demo --port 8765 --database sqlite:///./browser-demo.db
# Another terminal with the same environment activated:
python scripts/browser_smoke.py --url http://localhost:8765
```

For an installed-wheel walkthrough, create another environment, install the wheel and its dependencies, then pass its interpreter to the script:

```bash
python -m venv .package-venv
.package-venv/bin/python -m pip install dist/co4_orchestrator-0.1.0a1-py3-none-any.whl
python scripts/package_smoke.py --python .package-venv/bin/python
```

## Explicitly unverified or not implemented

Authenticated Claude Code, Codex CLI, and Copilot CLI runs and a real GitHub App installation remain live acceptance gates. Test the exact installed harness version, local authentication, selected repository permissions, fork visibility, upstream snapshot-branch creation, and webhook delivery in a disposable repository before a pilot.

Docker/Compose definitions and PostgreSQL configuration are supplied but were not launched here. No Docker engine or PostgreSQL service was available. No remote GitHub Actions run, production load test, penetration test, multi-tenant deployment test, payment integration, or cross-platform certification is claimed.

The native worker is not an operating-system sandbox. Harness tool restrictions, scope checks, redaction, and worker-reported evidence are not proofs against malicious repository code or a compromised contributor. Dedicated execution isolation, independent CI, and a revocable credential broker remain requirements before opening enrollment to untrusted public execution.

See [GitHub App setup](github-app.md), [harness compatibility](harnesses.md), [security](../SECURITY.md), and [the release roadmap](roadmap.md) for the remaining acceptance gates and product boundaries.
