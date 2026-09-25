# Behavioral traceability

The Gherkin file describes the product contract. It is not executed by a Gherkin runner in this alpha; the corresponding assertions live in pytest.

| Contract | Automated coverage |
|---|---|
| Validation, verification, consent and scoped access | `test_governance.py` |
| Simultaneous allocation, reservation and per-user limit | `test_governance.py` |
| 12-hour stale and 12-hour recovery boundaries | `test_handover.py` |
| Real baseline/red/green/verify workflow and human gate | `test_worker.py` |
| Phase file boundaries and immutable regression tests | `test_worker.py` |
| Process timeout, loss of coordination and private traces | `test_worker.py` |
| HMAC, delivery replay, OAuth, issue changes and App removal | `test_integrations.py` |
| App-controlled draft SHA, remote checkpoint and outbox retries | `test_integrations.py` |
| Maintainer and contributor UI workflow | `scripts/browser_smoke.py` |

See `docs/testing.md` for actually executed results and limitations.
