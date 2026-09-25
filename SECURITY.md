# Security and responsible deployment

**Do not invite arbitrary public repositories to execute on a personal workstation.** This release coordinates execution; it does not provide a hardened OS sandbox. Use a dedicated disposable VM or comparable isolation, a separate account/home, minimal credentials, and restricted network access. Keep personal files, cloud credentials, SSH keys, browser sessions, and production access out of that environment.

## Explicit boundaries

Repository code, dependency installers, tests, tool output, issue text and custom harness configuration can all be hostile. Prompt restrictions and tool allowlists reduce accidental behavior but are not an isolation proof. The locally configured test command executes code with the worker user's permissions. The default native worker must not be sold as safe “donate your laptop” capacity.

Early checkpoint pushes are an independent consent, not protected by the later PR gate. A push may publish incomplete code and trigger GitHub Actions. Restrict work/submission branch permissions and CI secrets before real enrollment. Never use privileged `pull_request_target` or equivalent workflows to execute untrusted contributions with secrets. Human-reviewed draft PRs still require normal CI and maintainer review before merge.

The server fences obsolete devices/leases within Co4. It cannot physically stop an intentionally modified worker or revoke a GitHub token independently issued to that worker. Use short-lived, narrow Git permissions where possible and revoke credentials directly at their issuer after compromise. There is a documented cancellation-versus-HTTP-publication race; maintainers must close or inspect a PR that was created just as governance changed.

## Included protections

Production configuration requires HTTPS and secrets; sessions are HttpOnly/SameSite cookies; state-changing cookie-authenticated calls require same-origin CSRF protection. Device credentials and human approval credentials are distinct. Tokens are stored hashed where lookup is sufficient. OAuth state is cookie-bound, expiring, and one-use. App/OAuth and trace material are encrypted with a configured server key. Webhook HMAC is checked over raw bytes; delivery IDs are deduplicated. Repository and installation IDs are matched before applying events.

Private trace reads are contributor-only. The public receipt is an aggregate allowlist rather than a transcript. Common secret patterns are redacted in transmitted events and detected in staged patches, and several risky file types/symlinks/binary changes block auto-push. These are best-effort controls, not complete credential scanning or irreversible anonymization. An approved diff or contributor-written summary can still contain sensitive content; review them.

Fresh worker checkouts, no supervisor Git hooks, noninteractive Git transport, no force pushes, explicit refspecs, direct argv execution, local repository/test approval, phase restrictions, fixed test/spec hashes, generation checks, process timeouts, and heartbeat failure cancellation are included. None protects against every attack possible for a process running as the worker user.

## Operator requirements before live use

Use TLS, a protected secret store, App installation on selected repositories, minimal App permissions, and a narrowly scoped contributor Git credential. Restrict reverse-proxy request rates and connections; the application has a size limit but no comprehensive abuse/rate-limiting system. Isolate database/network access. Back up the database **and** the encryption key separately, encrypted, and exercise restore. Do not rotate an encryption key by simply replacing its value; migration/rewrapping is required. Keep the App private key outside Git and Docker image layers.

Server traces expire after 30 days through the dispatcher. Local encrypted transcripts are retained until the contributor deletes them. Audit records, issue bodies, review packages, webhook IDs and contribution history are not automatically erased by trace expiry. A full retention/export/erasure policy, compliance review, legal basis and privacy notices are operator responsibilities not implemented by this alpha.

There is no SSO/SCIM, enterprise organization RBAC, audit signing, sandbox attestation, artifact signature verification, formal penetration test, or automated schema migration suite in this release. Public multi-tenant operation needs these gaps evaluated, not merely a larger database.

## Report an issue

Use the repository owner's private security reporting channel when enabled. Do not publish exploitable details, tokens, private project content, or traces in a public issue. This distribution has no preconfigured hosted reporting address or incident-response SLA; the deploying maintainer must establish those before inviting users.


## Interactive execution and billing (alpha.2)

Native interactive mode is not an additional OS sandbox or a guarantee of included subscription usage. It retains normal provider permission prompts, never synthesizes permission approvals, and never silently retries as noninteractive. `native_login` minimizes the subprocess environment and rejects explicit known provider credentials without opt-in; cached native API credentials, alternate providers, helpers, plugins, user configuration, and provider overages remain outside that check. Inspect the active CLI account and enforce monetary limits provider-side. An unknown interactive usage receipt cannot enforce an exact token/cost stop threshold.

Interactive terminal output is forwarded to the local terminal and retained privately. ANSI terminal control sequences and echoed secrets can occur in untrusted output; this is not a sanitized terminal emulator. Co4 does not log raw input keystrokes. Authenticate before recorded work, do not paste secrets into the harness, and use a dedicated terminal/VM. Native auth dialogs and account details must not be treated as public receipt data.

The governed prompt is temporarily materialized beneath ignored `.co4-private/` with owner-only POSIX permissions. It must be readable by the native CLI and is not encrypted during that handoff. Normal/error cleanup removes it, but host crashes/SIGKILL can leave a file that the operator must remove during recovery. Same-user processes are not isolated from this file or native login state. Checkpoints reject staged `.co4-private`/`.co4` content. This defense does not stop a malicious same-user process copying a secret elsewhere.

The PTY transport bounds stalled terminal writes, supervises absolute and idle deadlines, checks coordinator liveness, kills the child process group on cancellation, and restores terminal settings. No native Windows ConPTY backend is provided; use a Linux worker in WSL. Linux PTYs were tested; macOS/WSL themselves were not. See [interactive execution](docs/interactive.md) for all capture and recovery boundaries.
