# Changelog

## 0.1.0-alpha.2 / 2026-09-24

Adds optional foreground native-terminal execution for Claude Code, Codex, and GitHub Copilot. `--interactive`, `--noninteractive`, or local `execution_mode` select the transport without changing allocation or publication policy. New example configs prefer interactive execution; omitted mode fields preserve the alpha.1 noninteractive default.

Includes a real controlling PTY, keyboard/resize forwarding, Ctrl+] cancellation, terminal restoration, heartbeat and checkpoint supervision, output-backpressure and time limits, explicit phase confirmations, persisted local launch metadata, and evidence capture labels. Native-login environment policy is separate from execution mode; it is not subscription or billing verification. No automatic headless/API fallback.

Fixes checkpoint staging when an ignored private prompt directory exists, preserves literal/NUL-delimited filenames, and rejects staged coordinator-private files. The private prompt file is removed after each session. Interactive screen output is never parsed as trusted token/cost telemetry.

Real-PTY fixture tests cover all three adapter workflows plus interruption, phase gates, credentials, cleanup and unchanged review requirements. Live CLI accounts, Windows ConPTY, and provider invoices remain unverified. See [verification](docs/testing.md) and [interactive setup](docs/interactive.md).

## 0.1.0-alpha.1 / 2026-09-24

Initial GitHub-first cooperative development orchestration alpha. Includes control plane, responsive web workspace, GitHub App gateway, contributor worker, Claude/Codex/Copilot subprocess adapters, deterministic offline walkthrough, role and queue policies, strict evidence gates, SHA-bound human approval, draft publication, checkpoint handover, encrypted traces, quota ledger, transactional outbox, deployment source and verification tests.

See `docs/testing.md` for actual executed checks and explicit unverified surfaces. No live provider certification, payment processing, hardened sandbox, or production scale claim is made.
