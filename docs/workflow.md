# Enforced work contract

## Policy before execution

An issue becomes allocatable only after the project is active, validation requirements are satisfied, labels permit it, and a watching contributor/device meets role and verification rules. Manual validation exists in the UI and `/co4 validate`. An accepted GitHub ready-label event is privileged only after a permission lookup; the webhook sender is not trusted just because the event has a signature. A maintainer's explicit import operates under their authenticated authority.

The policy templates under `examples/workflows/` are complete `Policy` objects. Use their values in the policy editor or as the `policy` field of a maintainer-authenticated `PUT /api/projects/{id}` request with `budget_tokens` and `active`. They are checked against the same schema in tests. They are source-controlled reusable presets, not a server-side template version history. Editing workflow instructions produces the saved project policy; future template editing must not retroactively alter existing approved packages.

## Phases

| Phase | Required result | What stops it |
|---|---|---|
| Baseline | Locally approved test command exits 0; baseline commit and test digest captured | Existing failure, missing tests, timeout, execution error |
| Specification | `specs/co4/<work-id>/spec.md` and `behavior.feature`; required Gherkin structure | Empty/unsafe artifacts or modification of non-spec files |
| Red | Changed tests match the local test globs; test command exits 1 | No regression change, tests already pass, infrastructure failure, production code changed |
| Green | Implementation passes; red-phase tests and specification unchanged; summary written | Failing tests, weakened tests, edited contract, unsafe checkpoint |
| Verify | Repeat tests exit 0; final checkpoint equals tested green SHA | Files change during verification, mismatch, timeout |
| Ready | Evidence, diff, summary, usage and checkpoint become a review package | Missing evidence, wrong lease generation/branch, remote SHA drift |
| Approval | Contributor approves exact SHA and review digest; optional separate maintainer agrees | Device credential used, wrong person, changed SHA/digest, missing affirmative review |
| Publish | App freezes SHA on submission branch and opens draft PR | Revocation, drift, provider error; retry through outbox |

The supervisor executes test argv directly, never through a shell string. A test runner whose legitimate test-failure exit is not 1 needs a locally reviewed normalization wrapper. Do not map dependency/install failures to a regression pass or fake a red result. Test infrastructure can still be abused by malicious repository code; the workflow is evidence collection, not a replacement for isolation or independent CI.

A specification file with the expected words is not proof of meaningful BDD. Tests can assert the wrong behavior. The human gate and downstream CI must inspect relevance and quality. This implementation intentionally labels all run evidence worker-reported.

## Checkpoints

The worker uses `co4/work/<issue-number>/<lease-id>`. It checkpoints on accepted checkout and stage completion, and attempts additional checkpoints every 180 seconds while a process is active. Every push is preceded by a valid lease heartbeat. Hooks, interactive credential helpers, force pushes, and external Git protocols are disabled for supervisor commands. Changed symlinks, likely secret files, binary diffs, detected credential patterns, and diffs above 2 MB stop automatic checkpointing.

A checkpoint can contain unfinished or failing work. This is required for handover and is independently consented to. The human approval gate protects PR creation, not early work-branch publication. Git pushes can trigger repository workflows; configure work branches to run only safe, no-secret CI. Never attach production deployment secrets to untrusted work-branch pushes.

Git itself and other local processes remain beyond a mere prompt boundary. A modified worker or malicious process with its own credentials can write to GitHub outside Co4. Server fencing prevents accepted updates through Co4; it does not physically revoke a previously issued PAT.

## Stale work and recovery

The stale threshold is **43,200 seconds since last communication** for running, blocked, or awaiting-review work. Nothing is reassigned solely because a clock crosses the threshold. An eligible, otherwise idle contributor requests dibs with an enabled device. The original lease enters a paused recovery window ending **43,200 seconds after dibs**. A late heartbeat cannot silently cancel dibs or continue an old process.

The original contributor can explicitly recover in Co4 or comment `/co4 recover` before the deadline. A completed review package returns to review; unfinished work returns to running and can be retried locally. At or after the deadline, original recovery is rejected. The dispatcher/poll path settles the old attempt, increments generation, and assigns the eligible dibber; if they are no longer eligible or available, the work is requeued instead. Preserved checkpoints remain available.

On another device, the inherited branch and diff are available as `co4-handover` and `.co4-private/handover.diff`. A fresh baseline and fresh red/green evidence are required. This is **not** native Claude/Codex/Copilot session migration. Within one device, Co4 persists phase progress, checkpoint references, local traces and exposed session IDs, then retries an unfinished phase after explicit recovery. Native `--resume`/session restoration is a future adapter feature.

## Failures and operator response

Failed authentication or permissions, a nonzero harness exit, process idleness, absolute timeout, token boundary, control-plane loss, revoked membership, paused device, or obsolete generation stops work. The process group is terminated on supported platforms and encrypted state is retained. Repair the local cause, inspect the checkpoint and diagnostic trace, explicitly recover the lease, then restart `co4 worker`. Co4 does not loop indefinitely spending tokens against a broken provider account.

A changed issue requirement cancels its active attempt and requires revalidation. Removing the App, removing the repository from its installation, suspending the installation, closing/deleting the issue, or excluding its labels prevents continued allocation. Policy changes revoke active attempts rather than silently changing the rules mid-run. Completed PRs remain actual GitHub artifacts and must be handled explicitly by maintainers.

## Public receipt

The PR body contains the contributor's reviewed summary and `co4.receipt.v1` metadata: run reference, approved commit, test results/hashes, observed token counts, cache count where exposed, reported cost or null, execution duration, completeness/source indicators, descriptive files/lines-changed metrics (not a guessed difficulty score), and an evidence disclaimer. A receipt covers one allocation; earlier attempts remain separate history and project quota accounting. No raw prompt, private transcript, device name, local path, authentication token, or provider session ID is included by the receipt generator. The actual changed files remain part of the PR and need secret review independently.
