# Contributing to Co4

Start with `README.md`, `docs/architecture.md`, `docs/workflow.md` and `SECURITY.md`. Use a virtual environment and install `.[test]`. Run `pytest -q` before submitting a change. Browser tests require a fresh offline demo database and Playwright Chromium; see `scripts/browser_smoke.py`.

Define the user-visible behavior and invariant before implementation. Add a failing regression test, make the smallest useful change, and run the complete suite. Map new behavior to the product scenarios under `specs/`. Do not present unexecuted Gherkin text as executable test results; pytest currently owns the automated assertions.

Changes to allocation must retain concurrency and budget tests. Changes to human approval must retain cross-credential authorization, exact SHA/package binding, and optional two-person approval. Provider changes require argument and parser fixtures with secrets removed, official interface references, and a separately labeled live verification record. A mock transport passing is not proof that a real account has the required permission or entitlement.

Never commit credentials, live transcripts, worker checkouts, database files, or private issues. Use synthetic fixtures. Do not put privileged integration secrets in CI for untrusted pull requests. All new side effects should be durable, idempotent intentions with explicit authorization and failure handling. Discuss security-sensitive behavior privately with the repository owner.
