"""End-to-end escalation (task 8): webhook -> routing -> nobody takes it / round budget spent
-> `co4:needs-human` on the issue and the PR, plus a maintainer comment on the PR, and no
further automatic revision rounds."""

from __future__ import annotations

from conftest import device_client, poll
from sqlalchemy import select
from test_bdd_iteration import _permit, review_payload
from test_integrations import send_hook
from test_iteration_evidence import PHASES, _complete, _heartbeat_through
from test_submission_lookup import _publish

from co4.iteration import NEEDS_HUMAN_LABEL
from co4.models import Device, Project, Submission, Work


def _submission(app, work_id):
    with app.state.db.read() as s:
        return s.scalar(select(Submission).where(Submission.work_id == work_id))


def _review(app, monkeypatch, sub, sha, review_id):
    _permit(app, monkeypatch, {"trusted-reviewer"})
    response = send_hook(
        app,
        review_payload(sub.pr_number, sha, "changes_requested", review_id, "trusted-reviewer"),
        kind="pull_request_review",
        delivery="delivery-" + review_id,
    )
    assert response.status_code == 200
    app.state.settings.demo = True


def _set_policy(app, **changes):
    with app.state.db.transaction() as s:
        project = s.scalar(select(Project))
        project.policy = {**project.policy, **changes}


def _assert_escalated(app, job, sub, reason_fragment):
    assert _submission(app, job["work"]["id"]).state == "escalated"
    app.state.dispatcher.tick()
    labels = app.state.github.labels_added
    assert (job["work"]["number"], NEEDS_HUMAN_LABEL) in labels
    assert (sub.pr_number, NEEDS_HUMAN_LABEL) in labels
    number, body = app.state.github.pr_comments[f"co4:submission:{sub.id}:escalated"]
    assert number == sub.pr_number
    assert "needs a maintainer" in body and reason_fragment in body


def test_affinity_window_expiry_with_nobody_eligible_escalates_via_the_dispatcher(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    _review(app, monkeypatch, sub, sub.expected_head_sha, "rv-aff")
    with app.state.db.read() as s:
        device = s.scalar(select(Device))
    clients["contributor"].put(f"/api/devices/{device.id}", json={"enabled": False})

    # Inside the window nothing happens yet, even with nobody available.
    app.state.dispatcher.tick()
    assert _submission(app, job["work"]["id"]).state == "changes_requested"

    requested = _submission(app, job["work"]["id"]).updated
    app.state.coordinator.clock = lambda: requested + 43200 + 1
    app.state.dispatcher.tick()  # The background loop's sweep is what notices the dead end.
    _assert_escalated(app, job, sub, "No eligible device")
    with app.state.db.read() as s:
        assert s.get(Work, job["work"]["id"]).state == "revision_requested"


def test_affinity_window_expiry_with_someone_eligible_opens_the_pool_instead(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    _review(app, monkeypatch, sub, sub.expected_head_sha, "rv-pool")
    backup, _ = device_client(app, clients["backup"])
    assert poll(backup)["lease"] is None  # withheld during the window
    requested = _submission(app, job["work"]["id"]).updated
    app.state.coordinator.clock = lambda: requested + 43200 + 1
    app.state.dispatcher.tick()
    assert _submission(app, job["work"]["id"]).state == "changes_requested"
    assert poll(backup)["lease"]["kind"] == "revision"
    assert f"co4:submission:{sub.id}:escalated" not in app.state.github.pr_comments


def test_round_limit_escalates_after_the_last_allowed_revision(
    app, clients, tmp_path, monkeypatch
):
    _set_policy(app, max_revision_rounds=1)
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])

    # Revision 1 of 1 is allowed and completes normally.
    _review(app, monkeypatch, sub, sub.expected_head_sha, "rv-limit-1")
    worker, _ = device_client(app, clients["contributor"])
    revision = poll(worker)
    lease_id, generation = revision["lease"]["id"], revision["lease"]["generation"]
    _heartbeat_through(worker, lease_id, generation, PHASES)
    done = _complete(worker, lease_id, generation, revision["work"]["number"], red_exit=None)
    assert done.status_code == 200, done.text
    approval = {"sha": "c" * 40, "review_digest": done.json()["review_digest"], "confirm_reviewed": True}
    assert clients["contributor"].post(f"/api/leases/{lease_id}/approve", json=approval).status_code == 200
    app.state.dispatcher.tick()
    assert _submission(app, job["work"]["id"]).round == 2

    # The next changes-requested review would be revision 2 of 1: escalate, don't loop.
    _review(app, monkeypatch, sub, "c" * 40, "rv-limit-2")
    _assert_escalated(app, job, sub, "Revision round limit reached")
    with app.state.db.read() as s:
        assert s.get(Work, job["work"]["id"]).state == "submitted"  # no new round queued
    assert poll(worker)["lease"] is None

    # Escalation is sticky: a further review does not reopen automatic rounds.
    _review(app, monkeypatch, sub, "c" * 40, "rv-limit-3")
    assert _submission(app, job["work"]["id"]).state == "escalated"
    assert poll(worker)["lease"] is None


def test_a_review_after_affinity_escalation_does_not_silently_resume(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    with app.state.db.transaction() as s:
        s.get(Submission, sub.id).state = "escalated"
    _review(app, monkeypatch, sub, sub.expected_head_sha, "rv-after-escalation")
    assert _submission(app, job["work"]["id"]).state == "escalated"
    with app.state.db.read() as s:
        assert s.get(Work, job["work"]["id"]).state == "submitted"
