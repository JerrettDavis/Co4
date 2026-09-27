"""Covers iteration.route()'s three-tier affinity ordering, consulted from
Coordinator.poll()/allocate() for "revision_requested" work, and the sweep()-driven
escalation when that window fully expires with nobody eligible to pick the revision up."""

from __future__ import annotations

from conftest import device_client, poll
from sqlalchemy import select
from test_bdd_iteration import _permit, review_payload
from test_integrations import send_hook
from test_submission_lookup import _publish

from co4.models import Device, Lease, Submission, Work


def _request_revision(app, monkeypatch, number, sha, *, review_id="rv-route", login, body=""):
    _permit(app, monkeypatch, {login})
    response = send_hook(
        app,
        review_payload(number, sha, "changes_requested", review_id, login, body),
        kind="pull_request_review",
        delivery=f"delivery-{review_id}",
    )
    assert response.status_code == 200
    # _permit flips demo off (matching the existing can_manage-gating convention); restore it
    # so later device_client() calls in these routing tests can keep using the mock harness.
    app.state.settings.demo = True


def _submission(app, work_id):
    with app.state.db.read() as s:
        return s.scalar(select(Submission).where(Submission.work_id == work_id))


def test_original_contributor_can_claim_immediately_within_the_affinity_window(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    _request_revision(
        app, monkeypatch, sub.pr_number, sub.expected_head_sha, login="trusted-reviewer"
    )
    # The original contributor is whoever held the (only) prior lease -- "contributor" here.
    second_device, _ = device_client(app, clients["contributor"])
    result = poll(second_device)
    assert result["lease"] is not None
    with app.state.db.read() as s:
        new_lease = s.get(Lease, result["lease"]["id"])
        assert new_lease.kind == "revision"
        assert new_lease.round == 2
        assert new_lease.parent_lease_id == lease["id"]
        assert new_lease.feedback.get("trigger") == "changes_requested"
        w = s.get(Work, job["work"]["id"])
        assert w.checkpoint["sha"] == sub.expected_head_sha
        assert w.checkpoint["branch"] == sub.head_branch
        reloaded = s.get(Submission, sub.id)
        assert reloaded.state == "revising"


def test_an_unrelated_eligible_contributor_is_blocked_during_the_window(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    _request_revision(
        app, monkeypatch, sub.pr_number, sub.expected_head_sha, login="trusted-reviewer"
    )
    backup_device, _ = device_client(app, clients["backup"])
    assert poll(backup_device)["lease"] is None


def test_an_at_mentioned_member_with_a_device_can_claim_during_the_window(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    _request_revision(
        app,
        monkeypatch,
        sub.pr_number,
        sub.expected_head_sha,
        login="trusted-reviewer",
        body="Please loop in @backup for a second opinion.",
    )
    backup_device, _ = device_client(app, clients["backup"])
    result = poll(backup_device)
    assert result["lease"] is not None
    with app.state.db.read() as s:
        new_lease = s.get(Lease, result["lease"]["id"])
        assert new_lease.kind == "revision"


def test_anyone_eligible_can_claim_once_the_window_has_fully_elapsed(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    _request_revision(
        app, monkeypatch, sub.pr_number, sub.expected_head_sha, login="trusted-reviewer"
    )
    backup_device, _ = device_client(app, clients["backup"])
    assert poll(backup_device)["lease"] is None  # Not yet -- window has not elapsed.
    app.state.coordinator.clock = lambda: sub.updated + 43200 + 1
    result = poll(backup_device)
    assert result["lease"] is not None
    with app.state.db.read() as s:
        new_lease = s.get(Lease, result["lease"]["id"])
        assert new_lease.kind == "revision"
        assert new_lease.user_id != lease["user_id"]


def test_declined_revision_lease_reopens_as_revision_requested_not_queued(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    _request_revision(
        app, monkeypatch, sub.pr_number, sub.expected_head_sha, login="trusted-reviewer"
    )
    second_device, _ = device_client(app, clients["contributor"])
    revision = poll(second_device)["lease"]
    response = clients["contributor"].post(f"/api/leases/{revision['id']}/deny", json={})
    assert response.status_code == 200
    with app.state.db.read() as s:
        w = s.get(Work, job["work"]["id"])
        assert w.state == "revision_requested"
        reloaded = s.get(Submission, sub.id)
        assert reloaded.state == "changes_requested"


def test_sweep_escalates_when_the_window_expires_with_nobody_eligible(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    _request_revision(
        app, monkeypatch, sub.pr_number, sub.expected_head_sha, login="trusted-reviewer"
    )
    # Nobody is left who could ever pick this up: disable the only device in the project.
    with app.state.db.read() as s:
        device = s.scalar(select(Device))
    assert (
        clients["contributor"].put(f"/api/devices/{device.id}", json={"enabled": False}).status_code
        == 200
    )
    app.state.coordinator.clock = lambda: sub.updated + 43200 + 1
    with app.state.db.transaction() as s:
        app.state.coordinator.sweep(s)
    reloaded = _submission(app, job["work"]["id"])
    assert reloaded.state == "escalated"
    app.state.dispatcher.tick()
    assert (job["work"]["number"], "co4:needs-human") in app.state.github.labels_added
    # Escalation blocks further auto-routing even if someone re-enables a device later.
    assert (
        clients["contributor"].put(f"/api/devices/{device.id}", json={"enabled": True}).status_code
        == 200
    )
    third_device, _ = device_client(app, clients["contributor"])
    assert poll(third_device)["lease"] is None
