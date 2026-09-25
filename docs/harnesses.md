# Harness compatibility and telemetry

## What “support” means in this release

The repository includes real subprocess adapters for **Claude Code (`claude`)**, **OpenAI Codex CLI (`codex`)**, and **GitHub Copilot CLI (`copilot`)**. They support either native interactive terminal sessions or explicit noninteractive execution. Co4 feeds the governed prompt, retains exposed output, normalizes recognized structured usage where available, and stops on process errors or watchdogs. Interactive output is never parsed as a structured usage API. No adapter implements an agent reasoning loop, proxies model traffic, or shares a subscriber credential across contributors.

Command construction, representative telemetry parsing, and full Linux PTY fixture workflows for all three adapters were tested. The actual provider CLIs were not installed/authenticated during verification, so model-generated end-to-end behavior is **not live-certified**. Install and authenticate the tool on your device, review the current CLI flags and account entitlements, run `co4 doctor`, then perform one manual-allocation staging task before using automatic mode.

## Noninteractive execution

Selected by `--noninteractive` or `execution_mode = "noninteractive"`.

| Adapter | Invocation shape | Permission posture | Accounting |
|---|---|---|---|
| Claude Code | `claude -p --output-format stream-json --verbose --permission-mode dontAsk --allowedTools Read,Write,Edit,Glob,Grep` | No Bash permission granted; supervisor runs tests | Recognized terminal usage includes input, output, cache read/create; cost only if returned |
| Codex CLI | `codex --ask-for-approval never exec --sandbox workspace-write --json -` | Workspace-write sandbox; never dangerous/full-access bypass | Recognizes `turn.completed`; cached input is a subset of input |
| Copilot CLI | `copilot --output-format json --no-ask-user --no-auto-update --no-custom-instructions --allow-tool write --deny-tool shell -p ...` | Explicit write permission, shell denied; supervisor runs tests | Recognized explicit terminal usage only; unknown schemas stay unknown |

## Interactive execution

Selected by `--interactive` or `execution_mode = "interactive"`. The CLI receives a real controlling terminal, not redirected JSON/print-mode streams.

| Adapter | Invocation shape | Permission posture | Accounting |
|---|---|---|---|
| Claude Code | `claude --permission-mode default --tools Read,Write,Edit,Glob,Grep -- <instruction>` | Native permissions; file tools selected; Co4 runs tests | Unknown token/cost values; private terminal output retained |
| Codex CLI | `codex --ask-for-approval on-request --sandbox workspace-write --no-alt-screen -- <instruction>` | Native approvals and workspace-write sandbox | Unknown token/cost values; private terminal output retained |
| Copilot CLI | `copilot --no-auto-update --no-custom-instructions --deny-tool shell --interactive <instruction>` | Native approvals; shell denied | Unknown token/cost values; private terminal output retained |

The short instruction names a temporary private local phase-prompt file. After exiting the native CLI, the contributor explicitly confirms that Co4 may validate the phase. No terminal, unsupported flags, cancellation, or auth trouble cause a headless fallback. See [interactive setup](interactive.md) for Linux/WSL requirements, billing, privacy, and recovery limitations.

These flags supplement device isolation; they do not establish the same security guarantee across three products. Native user configuration, plugins, MCP servers, home-directory credentials, and provider policy can affect behavior. Keep the dedicated environment minimal. Co4 does not override enterprise restrictions or install authentication credentials for you.

In noninteractive mode, Claude and Codex prompts use stdin. Copilot's prompt is currently passed as an argv argument, so it may be visible to other sufficiently privileged local processes. Do not include secret values in issue text or prompts. Future ACP/SDK transports could reduce process-argument exposure without turning Co4 into a new harness.

## Authentication and execution

Authenticate using the provider's documented local CLI flow. The worker inherits only selected basic environment entries. `credential_policy = "native_login"` is the default environment guard and refuses forwarding known provider credentials explicitly named in `harness_env`. Set `credential_policy = "explicit_credentials"` to opt into forwarding selected provider variables. Normal local CLI sign-in remains available through the preserved home/configuration paths. This does not prove subscription authentication or prevent cached API credentials, credential helpers, or provider extra-usage billing. `CO4_*`, the configured device token variable, the configured Git token variable, and `GITHUB_APP_*` are stripped from the harness/test subprocess environment even if accidentally allowlisted. This is environment minimization, not protection against a process that can read the user's files or inspect its parent on an inadequately isolated host.

The doctor command checks executable discovery and version output, not entitlement, authentication freshness, model availability, OS sandbox health, or successful repository access. Unknown command flags will cause a controlled failed attempt; do not add an allow-all bypass to make an unattended run proceed.

Authentication failures, tool-denial exits, stalls and deadline expiry leave the attempt blocked with its local state. Fix the cause, explicitly recover the allocation in the UI, and restart the worker. A new invocation is made for an unfinished phase. Exposed native session IDs are retained locally, but native harness `--resume` is not used in this version.

## What is captured

Co4 captures its governed prompts, test command/output, timings, local state, and Git checkpoints. Noninteractive execution captures exposed stdout/stderr and structured events. Interactive execution captures terminal output, local launch IDs/PIDs, and phase confirmations, but not raw input keystrokes or a guaranteed complete semantic transcript. Echoed input may still appear in the screen output. It cannot observe hidden reasoning, internal service calls or tokens a provider does not report. “All interactions” therefore means all interactions visible at this process boundary, not invisible internals.

The full local stream is encrypted with a device key. Redacted server traces use a separate server key and contributor-only authorization. Public metadata is derived through an allowlist. Stream event retries use a lease-local monotonically increasing sequence; an identical replay is accepted and conflicting content at an existing sequence is rejected.

In noninteractive structured-event mode, each Claude terminal result is a snapshot, not an increment; repeated terminal snapshots are not double-counted. Claude input normalization includes fresh input plus cache reads and cache creation. Codex input already includes cached input, so the cached subset is not added again. For providers without explicit complete usage, totals retain known lower bounds but remain incomplete. Provider cost is never extrapolated from a guessed price table, subscription price, or missing model name.

Interactive receipts mark capture as `terminal_output`; token/cost fields remain unknown and incomplete. Mixed-mode recovery is labeled `mixed`. Token/cost hard caps cannot be enforced precisely against an interactive session with no exposed counters.

A reservation can be exceeded within an opaque provider invocation before counters arrive. The server stops subsequent execution at an observed boundary; it is not a provider billing firewall. Strong monetary limits require provider-side controls or an independently designed metering gateway and explicit user authorization.

## Adding a harness

Add an argument-array `Invocation`, a streaming event normalizer, and contract tests to `adapters.py`. Declare permission behavior, auth handling, terminal/error signals, usage semantics, and supported operating environments. Unknown events should be retained but not silently interpreted as billable counters. Add a real authenticated staging record before promoting an adapter from experimental to verified. OpenCode and Antigravity are future integrations, not aliases for the shipped adapters.

## Primary references

- [Claude Code headless/programmatic use](https://code.claude.com/docs/en/headless)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Codex noninteractive mode](https://developers.openai.com/codex/noninteractive)
- [Codex CLI reference](https://developers.openai.com/codex/cli/reference)
- [GitHub Copilot CLI command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
- [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)

Interfaces reviewed 2026-09-24. The installer's actual CLI version and permissions remain part of the live acceptance gate.
