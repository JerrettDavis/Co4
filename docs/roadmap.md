# Roadmap and release gates

## 0.1 alpha: the delivered vertical slice

GitHub App-facing web/API, contributor device enrollment, role and validation policies, durable allocation, 12h/12h handover, three CLI adapters, fixed SDD/BDD/TDD stages, trace and quota accounting, human review, draft publication, outbox reliability, offline demo, tests and deployment source. It is installable and the offline path is exercised. External authentication remains an operator setup and release verification gate.

## First pilot gate

Complete real App installation and three low-risk authenticated harness runs. Validate fork permissions and snapshot refs, provider error schemas, native platform behavior, denied tool handling, reported usage, and publication retry under actual GitHub rate limits. Pin tested CLI versions in a compatibility record. Check provider entitlements and account policies. Review dependency advisories, secrets, CSRF, privileges, data retention, and backup restore. Start with a handful of known contributors and no money.

## Public-contributor safety gate

Implement and validate a hardened execution sandbox or VM executor with disposable home directories, credential brokers, scoped short-lived Git grants, network policy, dependency isolation and tamper-resistant supervisor state. Add independent CI linkage to the approved SHA, malicious-workload tests, human escalation, abuse controls/rate limits and a threat-model review. Do not market the native worker as a safe public compute-donation agent before this gate.

## Governance and extensibility

Versioned workflow templates and a constrained editor; audit-preserving opt-back-in after decline; organization teams, invitations and delegated roles; contributor notification/acceptance improvements; explicit test-exemption policy for genuinely non-testable work; immutable package revisions; granular budget scopes; trace retention/export/erasure workflows; repository-role revocation reconciliation; native harness-session resume after adapter verification. Current roles are Co4 memberships; periodic synchronization with externally changed collaborator privileges needs further design.

## Scalability

Reviewed schema migrations, PostgreSQL integration tests, conditional/per-project claims instead of a global mutex, lease expiry index tuning, quota partitions, paginated dashboards, separated dispatcher process, object storage for large artifacts, operational metrics, load and chaos tests. Establish measurable throughput and failure-recovery objectives from pilot traffic before choosing infrastructure. An optional database URL alone is not the finished scalability work.

## Additional surfaces and incentives

GitLab MR and issue support, Azure DevOps, Jira/ServiceNow-to-code-host mappings, OpenCode/Antigravity harness adapters, signed external events, explainable quality-aware allocation with fairness limits, and external bounty integrations. Add payment participation only after acceptance, identity, disputes, authorization, and idempotent settlement boundaries are designed and tested. A native marketplace/escrow system is a separate product, not a minor endpoint.
