# Interactive contributor sessions

Added in **0.1.0-alpha.2**. This is a local execution-transport choice, independent of the contributor's manual/automatic allocation setting. Co4 remains the coordinator, not a replacement agent or subscription credential proxy.

## Start an interactive worker

Install this release, enroll a contributor device in the web app, and configure the repository allowlist and the two execution acknowledgments in `worker.toml`. Install and sign into the matching provider CLI separately, under the same operating-system user and home directory as the worker.

```bash
python -m pip install .
co4 doctor --config worker.toml
co4 worker --config worker.toml --interactive
```

Persist the choice in your local configuration:

```toml
execution_mode = "interactive"
credential_policy = "native_login"
harness_env = []
interactive_idle_seconds = 1800
phase_timeout_seconds = 3600
pick_next = true
```

The device's enrolled harness still determines which CLI handles an allocation. The worker's `harnesses` list is its local allowlist. Mode is not a remotely editable device permission, so the control plane cannot switch a contributor from interactive to unattended execution.

Explicit alternatives are `--noninteractive` and `--execution-mode noninteractive`. Command-line selection overrides the file for that invocation. Files copied from the new example select interactive mode. **Old files with no `execution_mode` retain their existing noninteractive behavior**; add the field or pass the flag during upgrade. The deterministic `demo-worker` remains a provider-free, noninteractive walkthrough. An existing configuration that deliberately forwarded provider API/token variables in `harness_env` now also needs `credential_policy = "explicit_credentials"`; without it, startup fails rather than forwarding those credentials implicitly.

## Native CLI behavior

| Harness | Native entry point selected by Co4 | Permission posture |
|---|---|---|
| Claude Code | `claude ... -- <initial instruction>` | Default interactive permissions, with Read/Write/Edit/Glob/Grep tools selected |
| Codex | `codex --ask-for-approval on-request --sandbox workspace-write --no-alt-screen -- <initial instruction>` | Native approval prompts and workspace-write sandbox |
| GitHub Copilot | `copilot --no-auto-update --no-custom-instructions --deny-tool shell --interactive <initial instruction>` | Native interactive prompts; shell denied |

The initial instruction points to a local, temporary governed-phase prompt file, rather than placing the issue's complete text in the process argument list. Co4 runs the separately approved test command itself. It does not automatically approve prompts, inject answers to permissions dialogs, select an alternative paid account, or retry in `-p`/`exec` mode. An unsupported installed flag fails visibly and leaves the allocation blocked.

Use the native interface for the current specification, regression-test, or implementation phase. You can answer questions, inspect its output, and grant or deny supported native permissions. When the phase is done, exit the CLI using its normal exit command. Co4 then asks you to type `continue` to run the phase's validation, or `stop` to pause. Exiting successfully is not itself proof of phase completion. Queued keystrokes from the CLI are discarded before that confirmation prompt.

Baseline, spec scope, red-test failure, immutable red-phase tests, green success, and repeat verification still apply. Only after these gates does the review package appear in Co4. **Phase confirmation is not PR approval.** The contributor must still approve the exact reviewed commit/package, and any project-required maintainer approval still applies.

## Sign-in and billing are separate from execution mode

The interactive flag is not a promise that a request is free, included in a plan, or billed differently from an unattended request. Claude documents that an API-key environment variable can take precedence over a signed-in subscription. Codex supports both ChatGPT and API-key authentication. Account entitlements, cached credentials, configured providers, and provider-side extra-usage settings still matter. Consult the references below and check your own provider account before starting real work.

The default `credential_policy = "native_login"` preserves selected basic environment variables including the home/configuration paths needed to locate the CLI's normal local sign-in. Known API-key, provider-routing, and token variables are not inherited from the ambient environment. Attempting to explicitly forward them via `harness_env` fails before allocation. This is an environment guard, **not authentication verification or a billing firewall**. It cannot guarantee that credentials in a native config file, keychain, plugin, credential helper, or other local account setting are subscription-backed.

For deliberately authorized API/provider credentials, opt in explicitly:

```toml
execution_mode = "noninteractive" # May also be interactive; these are independent choices.
credential_policy = "explicit_credentials"
harness_env = ["ANTHROPIC_API_KEY"]
```

This example forwards only the named variable when present. No secret values belong in the TOML file. The worker strips Co4 control-plane/device/Git credentials from subprocess environments even when accidentally allowlisted. `native_login` can allow alternate configuration paths such as `CODEX_HOME` or `CLAUDE_CONFIG_DIR`; the contents of those directories are still your responsibility. On a dedicated VM, perform provider login before starting a recorded work session. `co4 doctor` reports installation/version and terminal readiness, not subscription eligibility or spending authorization.

Check authentication using the provider's current CLI flow, for example `claude auth status`, `codex login status`, and the Copilot account UI. Provider-side spending controls remain necessary. Co4 does not pool, extract, or exchange subscription tokens on behalf of other contributors.

## Supervision, checkpoints, and recovery

The child gets a real controlling pseudo-terminal, with terminal input, output, error output, and resize events. Co4 continues lease heartbeats and periodic Git checkpoints while the UI runs. It stops on coordinator refusal/unreachability, time limits, prolonged terminal inactivity, terminal disconnect, or blocked terminal output. A still-animated TUI does not defeat the absolute phase time limit. A heartbeat proves worker communication, not meaningful coding progress.

`Ctrl+]` stops the entire current allocation, including the child process group. `Ctrl+C` is forwarded to the native CLI, whose own behavior may be cancellation of only its current turn. On exit or error, Co4 restores terminal settings and removes the temporary phase prompt file. The configured overall phase limit also applies when a contributor is answering questions; raise it locally for an intentionally longer session.

When an attempt is blocked, fix authentication/connectivity/tool settings as appropriate, use Co4's explicit recovery action, then restart the worker. The local state retains phase progress, checkpoint, launch ID, process ID, execution mode, and timing. It reopens an unfinished phase against the retained worktree. **Automatic provider-native conversation resumption is not implemented for interactive mode.** Do not use a global “continue last session” fallback that could join unrelated work. Native conversation IDs are not guessed from screen text.

Staleness, dibs, and the separate 12-hour recovery window are unchanged. Leaving an attached worker healthy keeps its lease communicating. Merely minimizing a terminal is not the same thing as stale work. To stop accepting further allocations, use the existing device controls or set `pick_next = false` before launching.

## Captured evidence and privacy

Co4 retains its supplied phase prompts, exposed terminal output, phase confirmations, subprocess lifecycle, test results, elapsed time, and Git evidence. It does **not** separately log raw input keystrokes because they can contain passwords or login codes. Text the CLI echoes back can nevertheless appear in the terminal output capture. Screen redraw/control sequences may make the private trace less readable than structured events. Authenticate before recording a work session and keep credentials out of prompts.

The full prompt is also temporarily present as a local UTF-8 file under ignored `.co4-private/`: directory mode 0700, file mode 0600 on POSIX. It is removed in normal and handled-error cleanup. A host crash or forcible SIGKILL can prevent cleanup; inspect this directory during recovery. The running CLI must be able to read the file. Other processes with the same user's privileges are not isolated from it. Encrypted trace-at-rest does not make this temporary handoff file encrypted.

Interactive receipts record `execution_mode = "interactive"` and `interaction_capture = "terminal_output"`. Token counts and monetary cost remain **null/unknown and incomplete** because native screen output is not a stable usage API. Even JSON-looking terminal text is not accepted as accounting. Mixed-mode recovery is explicitly labeled. No screen scraping, fabricated zero-cost claim, or guessed price estimate is used. Token/cost-based stop rules cannot provide an exact live spending cap when the CLI provides no counters; use time limits and provider-side controls. Full semantic transcript/usage import via verified native session formats is a future extension, not a feature claimed here.

Raw traces, prompts, local account details, process IDs, and native credentials are not added to the public PR receipt. Existing allowlisted public metrics and final human review remain in force.

## Operating-system and deployment boundary

Use a foreground terminal on Linux or a Linux worker inside WSL on Windows. A POSIX PTY implementation is included; Linux was exercised. macOS, WSL itself, and native Windows were not executed during release verification. **There is no native Windows ConPTY implementation in this alpha.** The worker and CLI must both run inside the same WSL environment when following the Windows path.

Piped stdin/stdout, redirected output, and services without a terminal are rejected before polling for work. There is no silent headless fallback. For remote work, attach a real terminal with `ssh -t`; `tmux` is a practical way to preserve an attached session. The web app is the governance/review surface, not a browser terminal multiplexer. Public multi-user execution still requires a proper worker sandbox and independent CI.

## References and validation

Provider interfaces reviewed September 24, 2026:

- [Claude Code CLI modes, permissions, and authentication commands](https://code.claude.com/docs/en/cli-reference)
- [Claude API-key precedence and subscription billing](https://support.claude.com/en/articles/12304248-manage-api-key-environment-variables-in-claude-code)
- [Claude usage and cost controls](https://code.claude.com/docs/en/costs)
- [Codex authentication modes](https://developers.openai.com/codex/auth)
- [Codex CLI reference](https://developers.openai.com/codex/cli/reference)
- [Copilot interactive command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)

The automated suite uses actual Linux PTYs, Git repositories, application endpoints and tests, with local deterministic executables named after each harness. It verifies Co4 behavior without model calls or spending. It is not a live provider-account certification. See [the release verification report](testing.md).
