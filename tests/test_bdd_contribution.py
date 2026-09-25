"""Executable steps for specs/contribution.feature.

Steps reuse the same fixtures and helpers as the unit-level suites (``app``/``clients`` from
conftest, ``configured`` from test_worker, ``setup_job`` from test_handover) so that the
Gherkin contract and the pytest assertions exercise one and the same code path.
"""
from __future__ import annotations
import pytest
from pytest_bdd import given, scenarios, then, when
from sqlalchemy import select
from co4.adapters import UsageMeter, aggregate
from co4.models import Lease, Member, Project, User, Work
from co4.worker import WorkerError
from conftest import device_client, poll
from test_handover import setup_job
from test_worker import configured

scenarios("contribution.feature")


@pytest.fixture
def ctx():
    return {}


def _project(app):
    with app.state.db.read() as s:
        return s.scalar(select(Project)).id


def _lease(clients, work_id, who="contributor"):
    return clients[who].get(f"/api/work/{work_id}").json()["leases"][-1]


# --- Scenario: Only validated and permitted work can be allocated -------------------------

@given("a project requires validation and verified contributors")
def project_requires_validation(app, clients, ctx):
    project = clients["maintainer"].get("/api/dashboard").json()["projects"][0]
    assert project["policy"]["require_validation"] is True
    assert project["policy"]["access"] == "verified"
    ctx["project_id"] = project["id"]


@given("an unverified contributor watches the project")
def unverified_contributor(app, ctx):
    with app.state.db.transaction() as s:
        contributor = s.scalar(select(User).where(User.login == "contributor"))
        member = s.get(Member, (ctx["project_id"], contributor.id))
        member.verified, member.watching = False, True
        # Make the only contributor-eligible request unvalidated as well (#44 is maintainer-only).
        work = s.scalar(select(Work).where(Work.number == 41))
        work.state = "validation_pending"
        ctx["work_id"] = work.id


@when("their device polls for an unvalidated issue")
def device_polls_unvalidated(app, clients, ctx):
    ctx["worker"], _ = device_client(app, clients["contributor"])
    ctx["job"] = poll(ctx["worker"])


@then("no lease is allocated")
def no_lease(ctx):
    assert ctx["job"]["lease"] is None


@when("a maintainer validates the issue and verifies the contributor")
def maintainer_validates_and_verifies(app, clients, ctx):
    maintainer = clients["maintainer"]
    assert maintainer.post(f"/api/work/{ctx['work_id']}/validate", json={}).status_code == 200
    # Validation alone is not enough: the contributor is still unverified.
    assert poll(ctx["worker"])["lease"] is None
    response = maintainer.put(f"/api/projects/{ctx['project_id']}/members",
                              json={"login": "contributor", "role": "contributor", "verified": True})
    assert response.status_code == 200, response.text


@then("a matching enabled device may receive one lease")
def one_lease(app, clients, ctx):
    job = poll(ctx["worker"])
    assert job["lease"] is not None and job["lease"]["state"] == "running"
    assert job["work"]["number"] == 41
    second_device, _ = device_client(app, clients["contributor"])
    assert poll(second_device)["lease"] is None  # One active allocation per person.


# --- Scenario: Automatic execution is not permission to publish ---------------------------

@given("an automatically allocated contribution passes baseline, red, green and verification")
def contribution_passes(app, clients, tmp_path, monkeypatch, ctx):
    worker, device = configured(app, clients, tmp_path, monkeypatch)
    job = poll(device)
    assert job["lease"]["state"] == "running"  # automatic autonomy: no human acceptance step
    worker.execute(job)
    ctx.update(worker=worker, device=device, job=job)
    lease = _lease(clients, job["work"]["id"])
    evidence = lease["evidence"]
    assert (evidence["baseline_exit"], evidence["red_exit"], evidence["green_exit"], evidence["verify_exit"]) == (0, 1, 0, 0)


@when("the worker submits its review package")
def worker_submits(clients, ctx):
    lease = _lease(clients, ctx["job"]["work"]["id"])
    assert lease["state"] == "awaiting_review"
    assert lease["review_digest"] and lease["checkpoint"]["sha"]
    ctx["lease"] = lease


@then("no pull request exists")
def no_pull_request(app, ctx):
    assert ctx["lease"]["pr_url"] == ""
    app.state.dispatcher.tick()
    assert app.state.github.publications == []


@then("a device token cannot authorize publication")
def device_cannot_approve(app, ctx):
    lease = ctx["lease"]
    response = ctx["device"].post(f"/api/leases/{lease['id']}/approve", json={
        "sha": lease["checkpoint"]["sha"], "review_digest": lease["review_digest"], "confirm_reviewed": True})
    assert response.status_code == 401
    app.state.dispatcher.tick()
    assert app.state.github.publications == []


@when("the contributor approves the exact tested SHA and package digest")
def contributor_approves(clients, ctx):
    lease = ctx["lease"]
    response = clients["contributor"].post(f"/api/leases/{lease['id']}/approve", json={
        "sha": lease["checkpoint"]["sha"], "review_digest": lease["review_digest"], "confirm_reviewed": True})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "publishing"


@then("the App may create a draft pull request from that SHA")
def app_creates_draft(app, clients, ctx):
    app.state.dispatcher.tick()
    publications = app.state.github.publications
    assert len(publications) == 1 and publications[0]["sha"] == ctx["lease"]["checkpoint"]["sha"]
    lease = _lease(clients, ctx["job"]["work"]["id"])
    assert lease["state"] == "submitted" and lease["pr_url"]


# --- Scenario: A recovery window protects the original contributor ------------------------

@given("an allocation has had no communication for 12 hours")
def stale_allocation(app, clients, ctx):
    worker, other, other_info, job, now = setup_job(app, clients)
    checkpoint = {"repository": "co4-demo/tiny-library", "branch": "co4/work/41/prior", "sha": "a" * 40}
    with app.state.db.transaction() as s:
        s.get(Work, job["work"]["id"]).checkpoint = checkpoint
    ctx.update(worker=worker, other=other, other_info=other_info, job=job, now=now, checkpoint=checkpoint)


@when("another eligible contributor requests dibs")
def request_dibs(clients, ctx):
    response = clients["backup"].post(f"/api/leases/{ctx['job']['lease']['id']}/dib",
                                      json={"device_id": ctx["other_info"]["id"]})
    assert response.status_code == 200, response.text
    ctx["dib"] = response.json()


@then("the original contributor has 12 hours to explicitly recover")
def twelve_hour_window(ctx):
    assert ctx["dib"]["recovery_deadline"] == ctx["now"] + 12 * 3600


@then("a heartbeat alone does not cancel the dibs")
def heartbeat_does_not_cancel(app, ctx):
    lease_id = ctx["job"]["lease"]["id"]
    response = ctx["worker"].post(f"/api/worker/leases/{lease_id}/heartbeat", json={"generation": 1, "phase": "baseline"})
    assert response.status_code == 409
    with app.state.db.read() as s:
        lease = s.get(Lease, lease_id)
        assert lease.dib_user is not None and lease.state == "dibbed"


@when("that window expires without recovery")
def window_expires(app, clients, ctx):
    app.state.coordinator.clock = lambda: ctx["now"] + 12 * 3600
    late = clients["contributor"].post(f"/api/leases/{ctx['job']['lease']['id']}/recover", json={})
    assert late.status_code == 409


@then("a new generation may be allocated")
def new_generation(ctx):
    ctx["new"] = poll(ctx["other"])
    assert ctx["new"]["lease"]["generation"] == 2
    assert ctx["new"]["lease"]["user_id"] != ctx["job"]["lease"]["user_id"]


@then("the original worker cannot update the old lease")
def old_worker_fenced(app, ctx):
    lease_id = ctx["job"]["lease"]["id"]
    response = ctx["worker"].post(f"/api/worker/leases/{lease_id}/heartbeat", json={"generation": 1, "phase": "baseline"})
    assert response.status_code == 409
    with app.state.db.read() as s:
        assert s.get(Lease, lease_id).state == "reassigned"


@then("the old checkpoint is retained")
def checkpoint_retained(ctx):
    assert ctx["new"]["work"]["checkpoint"] == ctx["checkpoint"]


# --- Scenario: Tests cannot be weakened to manufacture a pass -----------------------------

@given("the red phase changed regression tests and they failed")
def red_phase_failed(app, clients, tmp_path, monkeypatch, ctx):
    worker, device = configured(app, clients, tmp_path, monkeypatch)
    exits = {}
    original_test = worker.test
    def recording_test(phase):
        exits[phase] = original_test(phase)
        return exits[phase]
    worker.test = recording_test
    ctx.update(worker=worker, device=device, exits=exits)


@when("the green phase modifies those regression tests")
def green_modifies_tests(app, clients, ctx):
    worker = ctx["worker"]
    original = worker.mock
    def tamper(phase):
        original(phase)
        if phase == "green":
            (worker.repository.path / "test_average.py").write_text("import unittest\n")
    worker.mock = tamper
    ctx["job"] = poll(ctx["device"])
    with pytest.raises(WorkerError) as error:
        worker.execute(ctx["job"])
    ctx["error"] = str(error.value)
    # The Given precondition, observed during the same run: red added tests and they failed.
    assert ctx["exits"]["red"] == 1
    assert worker.state["tests_sha256"] != worker.state["baseline_tests"]


@then("the worker blocks completion")
def worker_blocks(app, ctx):
    assert "red-phase tests" in ctx["error"]
    assert "green" not in ctx["exits"]  # Tests never ran against the weakened suite.
    with app.state.db.read() as s:
        lease = s.get(Lease, ctx["job"]["lease"]["id"])
        assert lease.state == "blocked" and not lease.review_digest


@then("no review approval or PR can follow that attempt")
def no_approval_or_pr(app, clients, ctx):
    lease_id = ctx["job"]["lease"]["id"]
    sha = ctx["worker"].state["checkpoint"]["sha"]
    response = clients["contributor"].post(f"/api/leases/{lease_id}/approve",
                                           json={"sha": sha, "review_digest": "b" * 64, "confirm_reviewed": True})
    assert response.status_code == 409
    app.state.dispatcher.tick()
    assert app.state.github.publications == []


# --- Scenario: Unknown provider usage is not free work ------------------------------------

@given("a harness does not expose complete token usage")
def harness_without_usage(app, clients, ctx):
    worker, info = device_client(app, clients["contributor"], harness="copilot", max_tokens=100_000)
    job = poll(worker)
    lease = job["lease"]
    meter = UsageMeter("copilot")
    meter.feed('{"type":"assistant.message","data":{"content":"done"}}')  # No usage counters at all.
    usage = aggregate([meter.data(30)], 30)
    assert usage["input_tokens"] is None and not usage["complete"] and usage["source"] == "unavailable"
    response = worker.post(f"/api/worker/leases/{lease['id']}/heartbeat",
                           json={"generation": lease["generation"], "phase": "baseline", "usage": usage})
    assert response.status_code == 200, response.text
    ctx.update(worker=worker, job=job, reservation=lease["token_reservation"])


@when("its allocation is settled")
def settle(clients, ctx):
    response = clients["contributor"].post(f"/api/leases/{ctx['job']['lease']['id']}/deny", json={})
    assert response.status_code == 200, response.text


@then("the quota ledger accounts for at least its reservation")
def ledger_accounts(clients, ctx):
    project = clients["maintainer"].get("/api/dashboard").json()["projects"][0]
    assert ctx["reservation"] > 0
    assert project["spent_tokens"] >= ctx["reservation"]
    assert project["reserved_tokens"] == 0


@then("public cost and unavailable counters are not fabricated")
def counters_not_fabricated(clients, ctx):
    lease = _lease(clients, ctx["job"]["work"]["id"])
    usage = lease["usage"]
    assert usage["input_tokens"] is None and usage["output_tokens"] is None
    assert usage["cost_usd"] is None and usage["complete"] is False
    receipt = lease["receipt"]
    assert receipt["input_tokens"] is None and receipt["output_tokens"] is None
    assert receipt["reported_cost_usd"] is None
    assert receipt["usage_complete"] is False and receipt["usage_source"] == "unavailable"
