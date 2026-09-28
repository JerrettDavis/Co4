"""Covers evidence and approval for revision leases (task 5): complete() accepts a revision
evidence shape (baseline=0, an optional red phase, green=0, verify=0), and approve() queues an
'update_submission' outbox job -- fast-forwarding the existing, frozen PR branch -- instead of
'publish' when the lease being approved is a revision."""

from __future__ import annotations

from conftest import device_client, poll
from sqlalchemy import select
from test_bdd_iteration import _permit, review_payload
from test_integrations import send_hook
from test_submission_lookup import _publish

from co4.models import Lease, Submission, Work

PHASES = ["baseline", "spec", "red", "green", "verify", "ready"]


def _submission(app, work_id):
    with app.state.db.read() as s:
        return s.scalar(select(Submission).where(Submission.work_id == work_id))


def _open_revision_round(app, clients, tmp_path, monkeypatch):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    _permit(app, monkeypatch, {"trusted-reviewer"})
    response = send_hook(
        app,
        review_payload(
            sub.pr_number, sub.expected_head_sha, "changes_requested", "rv-ev", "trusted-reviewer"
        ),
        kind="pull_request_review",
        delivery="review-evidence",
    )
    assert response.status_code == 200
    app.state.settings.demo = True
    second_device, _ = device_client(app, clients["contributor"])
    revision_job = poll(second_device)
    assert revision_job["lease"]["kind"] == "revision"
    return job, lease, sub, second_device, revision_job


def _heartbeat_through(worker, lease_id, generation, phases):
    for phase in phases:
        response = worker.post(
            f"/api/worker/leases/{lease_id}/heartbeat",
            json={"generation": generation, "phase": phase},
        )
        assert response.status_code == 200, response.text


def _complete(worker, lease_id, generation, number, *, red_exit):
    branch = f"co4/work/{number}/{lease_id}"
    sha = "c" * 40
    response = worker.post(
        f"/api/worker/leases/{lease_id}/complete",
        json={
            "generation": generation,
            "checkpoint": {
                "repository": "co4-demo/tiny-library",
                "branch": branch,
                "sha": sha,
            },
            "evidence": {
                "spec_sha256": "a" * 64,
                "behavior_sha256": "b" * 64,
                "tests_sha256": "c" * 64,
                "profile": "default",
                "baseline_exit": 0,
                "red_exit": red_exit,
                "green_exit": 0,
                "verify_exit": 0,
                "baseline_commit": "a" * 40,
                "red_commit": "b" * 40,
                "green_commit": sha,
            },
            "usage": {"complete": True, "source": "fixture", "duration_seconds": 1},
            "summary": "Addressed review feedback.",
            "diff": "diff --git a/x b/x\n+++ b/x\n+fix\n",
        },
    )
    return response


def test_revision_complete_accepts_a_skipped_red_phase(app, clients, tmp_path, monkeypatch):
    job, lease, sub, worker, revision_job = _open_revision_round(
        app, clients, tmp_path, monkeypatch
    )
    lease_id = revision_job["lease"]["id"]
    generation = revision_job["lease"]["generation"]
    _heartbeat_through(worker, lease_id, generation, PHASES)
    number = revision_job["work"]["number"]
    response = _complete(worker, lease_id, generation, number, red_exit=None)
    assert response.status_code == 200, response.text
    with app.state.db.read() as s:
        assert s.get(Lease, lease_id).state == "awaiting_review"


def test_revision_complete_accepts_a_red_phase_that_still_ran(app, clients, tmp_path, monkeypatch):
    job, lease, sub, worker, revision_job = _open_revision_round(
        app, clients, tmp_path, monkeypatch
    )
    lease_id = revision_job["lease"]["id"]
    generation = revision_job["lease"]["generation"]
    _heartbeat_through(worker, lease_id, generation, PHASES)
    response = _complete(worker, lease_id, generation, revision_job["work"]["number"], red_exit=1)
    assert response.status_code == 200, response.text


def test_revision_complete_rejects_a_weakened_red_exit(app, clients, tmp_path, monkeypatch):
    job, lease, sub, worker, revision_job = _open_revision_round(
        app, clients, tmp_path, monkeypatch
    )
    lease_id = revision_job["lease"]["id"]
    generation = revision_job["lease"]["generation"]
    _heartbeat_through(worker, lease_id, generation, PHASES)
    response = _complete(worker, lease_id, generation, revision_job["work"]["number"], red_exit=0)
    assert response.status_code == 422


def test_revision_complete_rejects_nonzero_baseline_or_verify(app, clients, tmp_path, monkeypatch):
    job, lease, sub, worker, revision_job = _open_revision_round(
        app, clients, tmp_path, monkeypatch
    )
    lease_id = revision_job["lease"]["id"]
    generation = revision_job["lease"]["generation"]
    _heartbeat_through(worker, lease_id, generation, PHASES)
    # Reuse _complete's shape but corrupt baseline_exit directly.
    response = worker.post(
        f"/api/worker/leases/{lease_id}/complete",
        json={
            "generation": generation,
            "checkpoint": {
                "repository": "co4-demo/tiny-library",
                "branch": f"co4/work/{revision_job['work']['number']}/{lease_id}",
                "sha": "c" * 40,
            },
            "evidence": {
                "spec_sha256": "a" * 64,
                "behavior_sha256": "b" * 64,
                "tests_sha256": "c" * 64,
                "profile": "default",
                "baseline_exit": 1,
                "red_exit": None,
                "green_exit": 0,
                "verify_exit": 0,
                "baseline_commit": "a" * 40,
                "red_commit": "b" * 40,
                "green_commit": "c" * 40,
            },
            "usage": {"complete": True, "source": "fixture", "duration_seconds": 1},
            "summary": "Addressed review feedback.",
            "diff": "diff --git a/x b/x\n+++ b/x\n+fix\n",
        },
    )
    assert response.status_code == 422


def test_approving_a_revision_lease_queues_update_submission_not_publish(
    app, clients, tmp_path, monkeypatch
):
    job, lease, sub, worker, revision_job = _open_revision_round(
        app, clients, tmp_path, monkeypatch
    )
    lease_id = revision_job["lease"]["id"]
    generation = revision_job["lease"]["generation"]
    _heartbeat_through(worker, lease_id, generation, PHASES)
    complete_response = _complete(
        worker, lease_id, generation, revision_job["work"]["number"], red_exit=None
    )
    assert complete_response.status_code == 200, complete_response.text
    review_digest = complete_response.json()["review_digest"]
    approved = {
        "sha": "c" * 40,
        "review_digest": review_digest,
        "confirm_reviewed": True,
    }
    response = clients["contributor"].post(f"/api/leases/{lease_id}/approve", json=approved)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "publishing"

    before_publications = len(app.state.github.publications)
    app.state.dispatcher.tick()
    assert len(app.state.github.publications) == before_publications  # No new PR.
    assert app.state.github.branch_updates == [{"branch": sub.head_branch, "sha": "c" * 40}]

    with app.state.db.read() as s:
        reloaded_lease = s.get(Lease, lease_id)
        assert reloaded_lease.state == "submitted"
        w = s.get(Work, job["work"]["id"])
        assert w.state == "submitted"
        assert w.active_lease is None
        reloaded_sub = s.scalar(select(Submission).where(Submission.work_id == job["work"]["id"]))
        assert reloaded_sub.round == 2
        assert reloaded_sub.expected_head_sha == "c" * 40
        assert reloaded_sub.state == "awaiting_rereview"
        # The original PR identity (number and PR-owned branch name) never changes.
        assert reloaded_sub.pr_number == sub.pr_number
        assert reloaded_sub.head_branch == sub.head_branch
