#!/usr/bin/env python3
"""Check or regenerate MANIFEST.sha256 against the current working tree.

MANIFEST.sha256 pins the sha256 hash of a fixed set of source-of-truth files
(config, source, tests, docs, packaging metadata). It intentionally does not
cover generated/build artifacts (e.g. anything under dist/) -- those are not
tracked in source control and should never appear in the manifest.

Usage:
    python scripts/verify_manifest.py            # check (default)
    python scripts/verify_manifest.py --check    # verify hashes, exit 1 on mismatch
    python scripts/verify_manifest.py --write    # regenerate MANIFEST.sha256

The output format matches standard `sha256sum` text-mode output: one line per
file as "<sha256hex>  <path>" (two spaces), LF line endings, paths relative to
the repo root using forward slashes, sorted lexicographically.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "MANIFEST.sha256"

# The fixed set of paths tracked by MANIFEST.sha256, relative to the repo
# root. Update this list deliberately when files are added/removed/renamed;
# do not add build artifacts (anything under dist/, __pycache__/, etc.) --
# those are gitignored and regenerated, never tracked in source control.
TRACKED_PATHS = [
    ".dockerignore",
    ".env.example",
    ".github/ISSUE_TEMPLATE/bug.yml",
    ".github/ISSUE_TEMPLATE/feature.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/workflows/ci.yml",
    ".gitignore",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "LICENSE",
    "Makefile",
    "README.md",
    "SECURITY.md",
    "compose.postgres.yaml",
    "compose.yaml",
    "deploy/Caddyfile",
    "docs/architecture.md",
    "docs/browser-test-report.json",
    "docs/deployment.md",
    "docs/extensions.md",
    "docs/github-app.md",
    "docs/harnesses.md",
    "docs/images/landing.png",
    "docs/images/mobile.png",
    "docs/images/overview.png",
    "docs/images/review.png",
    "docs/installed-terminal-test-report.json",
    "docs/interactive.md",
    "docs/naming.md",
    "docs/package-test-report-alpha1.json",
    "docs/package-test-report.json",
    "docs/product.md",
    "docs/pytest-results.xml",
    "docs/roadmap.md",
    "docs/testing-alpha1.md",
    "docs/testing.md",
    "docs/workflow.md",
    "examples/worker.toml",
    "examples/workflows/bugfix.v1.json",
    "examples/workflows/feature.v1.json",
    "examples/workflows/maintenance.v1.json",
    "pyproject.toml",
    "scripts/browser_smoke.py",
    "scripts/interactive_smoke.py",
    "scripts/package_smoke.py",
    "scripts/wait_http.py",
    "specs/contribution.feature",
    "specs/interactive.feature",
    "specs/traceability.md",
    "src/co4/__init__.py",
    "src/co4/__main__.py",
    "src/co4/_pty_child.py",
    "src/co4/adapters.py",
    "src/co4/app.py",
    "src/co4/cli.py",
    "src/co4/config.py",
    "src/co4/db.py",
    "src/co4/domain.py",
    "src/co4/github.py",
    "src/co4/gitops.py",
    "src/co4/models.py",
    "src/co4/outbox.py",
    "src/co4/process.py",
    "src/co4/schemas.py",
    "src/co4/security.py",
    "src/co4/seed.py",
    "src/co4/static/app.css",
    "src/co4/static/app.js",
    "src/co4/static/favicon.svg",
    "src/co4/static/index.html",
    "src/co4/terminal.py",
    "src/co4/worker.py",
    "tests/conftest.py",
    "tests/test_distribution.py",
    "tests/test_governance.py",
    "tests/test_handover.py",
    "tests/test_integrations.py",
    "tests/test_interactive.py",
    "tests/test_worker.py",
]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_current() -> tuple[dict[str, str], list[str]]:
    """Return (path -> hash) for existing tracked paths, and a list of tracked
    paths that are missing from the tree."""
    current: dict[str, str] = {}
    missing: list[str] = []
    for rel in TRACKED_PATHS:
        full = REPO_ROOT / rel
        if not full.is_file():
            missing.append(rel)
            continue
        current[rel] = sha256_of(full)
    return current, missing


def read_manifest() -> dict[str, str]:
    if not MANIFEST_PATH.exists():
        return {}
    entries: dict[str, str] = {}
    with MANIFEST_PATH.open("r", encoding="utf-8", newline="") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n").rstrip("\r")
            if not line.strip():
                continue
            digest, rel = line.split("  ", 1)
            entries[rel] = digest
    return entries


def cmd_write() -> int:
    current, missing = compute_current()
    if missing:
        print("error: tracked paths missing from tree, refusing to write manifest:", file=sys.stderr)
        for rel in missing:
            print(f"  {rel}", file=sys.stderr)
        return 1

    lines = [f"{current[rel]}  {rel}" for rel in sorted(current)]
    MANIFEST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {MANIFEST_PATH} ({len(lines)} entries)")
    return 0


def cmd_check() -> int:
    manifest = read_manifest()
    current, missing = compute_current()

    problems: list[str] = []

    for rel in missing:
        problems.append(f"missing file (tracked but absent from tree): {rel}")

    for rel in sorted(current):
        expected = manifest.get(rel)
        actual = current[rel]
        if expected is None:
            problems.append(f"no manifest entry for tracked file: {rel}")
        elif expected != actual:
            problems.append(f"hash mismatch: {rel}")

    tracked_set = set(TRACKED_PATHS)
    for rel in sorted(manifest):
        if rel not in tracked_set:
            problems.append(f"stale manifest entry (no longer tracked): {rel}")

    if problems:
        print("MANIFEST.sha256 is out of date or invalid:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("\nRun: python scripts/verify_manifest.py --write", file=sys.stderr)
        return 1

    print(f"MANIFEST.sha256 OK ({len(TRACKED_PATHS)} files verified)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify hashes against MANIFEST.sha256 (default)")
    mode.add_argument("--write", action="store_true", help="regenerate MANIFEST.sha256 from the current tree")
    args = parser.parse_args(argv)

    if args.write:
        return cmd_write()
    return cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
