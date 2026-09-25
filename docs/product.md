# Product definition and decisions

## The product

Co4 is a permission-aware dispatch system for development contributions. Maintainers bring repositories and work; contributors bring authorization, attention, devices, and separately licensed coding tools. Existing trackers remain the discussion and delivery surface. The control plane answers four questions: is this work ready, who may take it, what may they do, and has a person approved what will be published?

The allocation is not a promise of a successful fix. It is a revocable lease with an accountable workflow and recoverable checkpoints. A contributor can decline a request, stop their device, or block on ambiguity. A maintainer can withdraw a project or change policy. Neither an idle machine nor a capable model creates an entitlement to modify a repository.

## Operator models

**Maintainer** is the project governance side. A repository owner enrolls the installation, selects access and validation rules, establishes quota reservations, and delegates roles. Maintainers can administer members and policy; triagers can validate requests and inspect operational history without changing governance. An optional second-person approval separates contribution from maintainer authorization.

**Contributor** is the person accepting responsibility for an attempt. They choose watched repositories, allowed issue labels, a harness, autonomy level, token ceiling, local checkout destination, push destination, and test command. Manual mode offers work first; automatic mode starts eligible work. Both stop at the same human review gate. Each person has at most one active allocation across their devices in this alpha.

These are capabilities, not mutually exclusive account types. A maintainer may also contribute. Additional maintainer approval, when required, cannot be supplied by the same person as the contributing user.

Organization-owned repositories are supported through GitHub installations. Co4 currently stores permissions per repository, not an enterprise hierarchy with consolidated billing organizations, SCIM, or organization-wide role inheritance.

## The two primary journeys

### Maintainer journey

Install the App on a narrow repository selection, sign in with GitHub, and enroll a repository for which GitHub reports administrator access. Configure policy before importing issues. Start with verified contributors, explicit validation, excluded security labels, a modest internal token quota, and independent CI requirements. Import open issues or receive signed webhooks. Validate a request in Co4 or comment `/co4 validate` as an enrolled triager/maintainer.

The App maintains a status comment on the original issue while execution proceeds. Work is visible in the queue and dashboard without inventing a second issue tracker. Completion produces a review package, not an immediate PR. After required human approval, the App publishes a draft PR from a fixed snapshot branch with an aggregate receipt. Normal repository review, CI, and merging remain the maintainer's responsibility.

### Contributor journey

Sign in, watch a project, obtain verification where required, and create a revocable device credential. Install Co4 and an already-authenticated harness on a dedicated environment. Locally approve both code execution and early checkpoint publication. Declare repositories and exact test argv; the server cannot turn an issue comment into an arbitrary local shell command.

The worker polls, accepts an automatic lease or waits for manual consent, clones a clean checkout, and records the baseline. It asks the existing harness for a specification and behavior scenarios, then a failing regression test, then an implementation. Co4 runs the configured tests and performs Git operations. Each completed stage produces a checkpoint, with periodic checkpoints during longer activity. A changed specification or weakened red test stops completion. Review happens in the web app; SHA-bound approval can also be sent from the original GitHub issue after inspecting the package.

Only after the draft PR is recorded may a continuously polling worker receive another request. A blocked attempt needs explicit recovery. An intentional decline is remembered for that user and work item. There is no “undo decline” UI in this version; use another contributor or a new issue, or implement an audited opt-back-in operation before broad deployment.

## Autonomy as separate permissions

Allocation consent, repository execution consent, checkpoint push consent, contribution review, optional maintainer approval, and merging are distinct. Co4 exposes only the first five, and never merges. Automatic allocation is not automatic publication. A device token cannot call the human approval endpoint. A project policy cannot silently expand the locally approved repository list or test command.

The threat model still assumes a trusted supervisor installation running on an adequately isolated device. A process with access to a user's home directory or provider credentials is not confined merely because it was launched with a restrictive prompt. The server can reject old leases; it cannot revoke an independently issued Git credential or stop an intentionally modified worker from bypassing Co4.

## Workflow and product judgment

The fixed initial workflow deliberately rejects tasks that cannot demonstrate the required red/green discipline. A purely editorial task needs an appropriate automated behavior contract, such as executable examples or documentation checks, rather than a fabricated failing assertion. This alpha does not contain a broad “skip TDD” escape hatch. A meaningful exemption mechanism would need a separately reviewed policy and an honest receipt that calls the exemption out.

Likewise, a Gherkin file is evidence of a behavior specification, not proof of useful BDD. The supervisor checks required structure and hashes. The maintainer and independent CI must still judge whether scenarios and tests cover the actual request. Worker-reported evidence is never described as a cryptographically trustworthy proof of correctness.

## Prioritization and future incentives

The alpha supports FIFO and deterministic priority-plus-age ordering of work. Eligibility, quotas, per-user concurrency, and contributor preferences are hard constraints. This is not yet reputation-based contributor selection. A dormant quality field is not a scoring product.

For a later allocator, rank eligible candidate pairs rather than simply favoring whoever polls most frequently. Separate confidence, relevant expertise, reliability, project familiarity, verified outcomes, and waiting time. Reserve some capacity for new contributors, cap the effect of historical scores, decay stale signals, and expose why an allocation was made. Do not make acceptance counts or token consumption proxies for quality. Require an appeal path and maintainers' ability to override weights. Measure rejection/rework rates, successful handovers, maintainer review burden, and fairness across new and established contributors before introducing money.

## Compensation

A bounty is an external contract between relevant parties, not a side effect of a passing test. A later connector may attach an offer, eligibility restrictions, settlement events, and provider references. It must not pay based solely on worker claims or PR creation. Payout identity, tax handling, sanctions checks, refunds, disputes, and acceptance rules remain with the payment/bounty provider until a separately designed regulated service exists. This alpha intentionally moves no money.

## Product measures

Track time from validation to allocation, time to first checkpoint, blocked/authentication recovery rate, time awaiting human review, PR acceptance and rework, reported versus unreported usage, handover success, and maintainer effort. Do not advertise throughput measured with the deterministic demo as agent productivity. Capture baseline team behavior before measuring improvement.

## Release strategy

Ship a small, truthful vertical slice first. Complete authenticated, low-risk staging runs on all three required harnesses before inviting outside contributors. Add a defensible execution sandbox and permission revocation checks before any public “donate your laptop” marketplace. Add organization governance and a distributed allocator only after the small-team model has real operational evidence. Keep optional bounties and additional tracker surfaces behind adapter contracts so they do not contaminate the core permission model.
