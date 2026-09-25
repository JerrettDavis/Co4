# Release verification: 0.1.0-alpha.2

Verified September 24, 2026, on Linux with Python 3.13.5. These results describe Co4 and deterministic local fixtures, not a certification of provider accounts or production security.

## Results

| Verification | Result | What ran |
|---|---|---|
| Automated Python suite | **94 passed, 0 failed, 0 skipped** | Real SQLite databases, application HTTP clients, subprocesses, Git repositories, Linux PTYs, and deterministic external-service fixtures |
| Installed-package workflow | **6 checks passed** | Separately installed alpha.2 wheel, real local HTTP, Git/test workflow, approval gate and simulated publication |
| Installed-terminal transport | **6 checks passed** | Separately installed wheel and PTY helper, native argv for all three adapters, controlling TTY, keyboard I/O, restoration, and no-terminal refusal |
| JavaScript syntax | Passed | `node --check src/co4/static/app.js` |
| Python compilation | Passed | `python -m compileall -q src scripts` |
| Wheel build | Passed | Setuptools wheel build and separate installation |

The two installed-package checklists are walkthrough checks, not additional pytest cases. No coverage percentage, security audit, provider invoice verification, or throughput result is asserted. Machine-readable reports: [pytest-results.xml](pytest-results.xml), [package-test-report.json](package-test-report.json), and [installed-terminal-test-report.json](installed-terminal-test-report.json).

## New interactive verification

The 33 new parametrized cases in `tests/test_interactive.py` supplement the 61 alpha.1 regression cases. They verify native rather than print/exec/JSON argv for Claude Code, Codex, and Copilot; explicit mode selection; environment minimization; explicit API-credential opt-in; invalid configuration; terminal validation before polling; and no silent noninteractive fallback.

The Linux PTY tests use real child processes, not mocked terminal descriptors. They exercise a controlling `/dev/tty`, bidirectional keyboard input, non-newline output, resize forwarding, idle and absolute watchdogs, cancellation on lease failure, Ctrl+] stop, bounded output backpressure, terminal-settings restoration, blocking-mode restoration, and actual continue/stop input at a phase boundary.

Three full workflow tests create temporary executable fixtures named `claude`, `codex`, and `copilot`. Each receives the adapter's actual interactive argument array and a controlling terminal. The fixtures read a real private governed prompt file, require terminal input, create the spec/Gherkin artifacts, add the regression test, and implement the fixture fix. Co4 runs actual baseline/red/green/verify commands and Git checkpoints, and the application creates an awaiting-review package without a PR. A fourth workflow covers contributor refusal at the phase boundary and checks blocking/cleanup.

A fixture deliberately emits JSON-looking token and cost values through its terminal UI. Tests confirm that Co4 does not accept them as accounting: interactive usage remains incomplete and unknown. Evidence labels interactive capture, and private prompt files do not appear in checkpoint diffs. A staging regression also covers a literal filename resembling a Git pathspec and rejection of forcibly staged coordinator-private files.

The new behavioral contract is [specs/interactive.feature](../specs/interactive.feature). The implementation work began with failing interactive adapter/environment/PTY tests, then added the transport and full workflow tests. No paid model calls were made to turn those tests green.

## Existing behavior rechecked

The regression suite still covers membership, validation, roles, labels, device autonomy and revocation, concurrent allocation, usage reservations, unknown usage, event replay, checkpoint safety, the 12-hour stale threshold plus separate 12-hour recovery window, generation fencing, workflow scope gates, immutable red tests, repeated verification, exact-SHA/package approval, and GitHub webhook/auth/outbox contracts. GitHub API responses are fixtures, not a live installation.

The installed-package walkthrough launches the installed server and worker from outside the source checkout. It verifies packaged static assets, a real Git and regression-test flow, no publication before authenticated human approval, and dispatcher publication afterward. The installed-terminal walkthrough independently imports the package from the separate environment's site-packages, checks that `_pty_child.py` was packaged, then runs a real terminal round trip and refusal of piped input.

Runtime dependencies were made available from the existing environment through site-packages. This is a separate wheel installation, **not a fresh network dependency download/install test**. The fixtures neither contact a model nor spend provider credits nor publish a real GitHub PR.

## Browser evidence boundary

The alpha.1 Chromium walkthrough remains available as historical evidence in [browser-test-report.json](browser-test-report.json), `docs/images/`, and [testing-alpha1.md](testing-alpha1.md). **It was not rerun for alpha.2.** This release changes the device setup command and displayed version; its JavaScript passed syntax validation. No new browser interaction, native browser networking, cookie-transport, or CSP certification is claimed.

The historical browser run used actual Chromium DOM/JavaScript with a Python API bridge because native browser navigation was blocked in the verification environment. The script still supports ordinary navigation for local/CI use.

## Reproduce

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
pytest -q --junitxml=docs/pytest-results.xml
node --check src/co4/static/app.js
python -m compileall -q src scripts
python -m pip wheel --no-build-isolation --no-deps --wheel-dir dist .
```

Install the resulting wheel into a separate environment with its dependencies, then run:

```bash
python scripts/package_smoke.py --python /absolute/path/to/installed-venv/bin/python
python scripts/interactive_smoke.py --python /absolute/path/to/installed-venv/bin/python
```

The source-suite verification here disabled unrelated ambient pytest plugins using `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; the tests do not depend on a third-party pytest plugin. POSIX-specific tests skip on native Windows, but none were skipped in this Linux run.

## Explicitly unverified

Actual authenticated Claude Code, Codex, and Copilot sessions, subscription entitlements, native account selection, API invoices, and provider tool-permission behavior remain live acceptance gates. Contract/fixture tests do not establish that every installed CLI version supports identical flags. Verify each chosen installed version and account on one disposable repository before automatic enrollment.

Linux was exercised. Native Windows/ConPTY is not implemented for the interactive runner; use a Linux worker in WSL. WSL itself and macOS were not executed here. Provider-native conversation resume/import is not implemented for interactive mode; recovery retains Co4 phase/worktree state and starts a new unfinished-phase invocation.

Live GitHub installation, fork/snapshot behavior, Docker/Compose, PostgreSQL, remote Actions CI, production load, and public multi-tenant execution were not tested. The worker is not an operating-system sandbox. Terminal recording is not a complete semantic transcript, and unknown interactive usage cannot enforce an exact live monetary cap. See [interactive execution](interactive.md), [harnesses](harnesses.md), and [security](../SECURITY.md).
