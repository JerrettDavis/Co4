# Extension contracts and future integrations

The seams below are architectural contracts and example payloads. Only the GitHub gateway and three CLI adapters are implemented. This file does not claim an ADO/Jira/ServiceNow, bounty, or external webhook delivery connector exists.

## Tracker and delivery gateway

Normalize external work into a project-scoped issue key with provider, tenant/installation, immutable repository/project identifier, external item ID, title/body, labels, revision, state, and a verified actor. Authenticate and deduplicate the provider's event before domain mutation. Map status and delivery through the transactional outbox. Intake authentication must not automatically imply actor authorization.

Separate issue tracker from code host: a Jira issue can eventually target a GitHub PR without pretending the two use the same identity or permission model. Use explicit mappings and consent. Store credentials per tenant/installation, never in a workflow prompt. An external status write does not grant permission to merge, payout, or execute code.

## Versioned event envelope

A prospective connector envelope:

```json
{
  "schema": "co4.integration-event.v1",
  "id": "stable-outbox-event-id",
  "project_id": "internal-project-id",
  "type": "contribution.accepted",
  "occurred_at": "2026-09-24T12:00:00Z",
  "subject": {"work_id": "work-id", "lease_id": "lease-id", "commit": "approved-sha"},
  "data": {"provider": "github", "pull_request_number": 123}
}
```

A real outbound webhook adapter must add delivery signatures, replay protection, timeouts, exponential backoff, idempotency keys, per-tenant egress allowlists and SSRF defenses. It must not let an issue's arbitrary URL become a privileged internal HTTP request. Raw private traces must never be a default event payload.

## Bounty provider

A bounty connector needs at least `GetOffer`, `CheckEligibility`, `ReserveOffer`, `RecordSubmission`, `RecordAcceptance`, `ReleaseReservation`, and `GetSettlementStatus`. A payout attempt belongs in an independently idempotent ledger with immutable currency/amount, provider reference, payee reference, acceptance authority and dispute state. Contributor participation and payee enrollment are separate consents.

PR creation is not acceptance and passing tests is not a payment authorization. An accepted/merged event can satisfy only the acceptance policy agreed to for the bounty. Require independent authoritative acceptance, explicit payout authorization, a provider idempotency key and reconciliation. Keep regulated identity, tax, banking and escrow duties with a suitable external provider until separately designed. No credentials, money, or mock payouts are processed by this alpha.

## Allocator strategy

Future strategies should return an explainable ranking of eligible work/contributor pairs plus a stable allocation decision record. Permission, verification, user consent, concurrency and budget are non-negotiable predicates, not weights. Score priority, wait time, relevant verified expertise and reliability only after eligibility. Add exploration capacity, caps, aging, appeals and anti-gaming controls. Preserve a deterministic test harness for policies; do not deploy an opaque popularity score.

## Workflow templates

The current source-controlled policy presets configure instructions, access, labels, caps and test profile. Next, introduce immutable `TemplateVersion` records, an explicit migration/approval step, per-project bindings, declared artifact schemas and a visual editor that cannot bypass required safety gates. Any execution hook must refer to a locally approved capability, not a shell command downloaded from a project label.

## Worker and harness transport

The current transport is a subprocess with structured/text streams. ACP or provider SDK adapters can implement the same phase boundary, event journal and capability contract. Native-session resume requires version-specific compatibility and fail-closed recovery. Cross-user handover must not copy auth, personal memory, private instructions or unrelated sessions. Commit/diff/spec transfer is the safe default until a more detailed protocol exists.
