"""Covers the Submission-model-backed replacement for the previous lease-scanning PR lookup:
a Submission row is created on publish (recording the PR number returned by GitHub.publish),
and the pull_request webhook now finds the affected work/lease via a direct (project_id,
pr_number) lookup instead of scanning every lease in the project."""

from __future__ import annotations

from conftest import poll
from sqlalchemy import select
from test_integrations import send_hook
from test_worker import configured

from co4.models import Submission, Work


def _publish(app, clients, tmp_path, monkeypatch):
    worker, c = configured(app, clients, tmp_path, monkeypatch)
    job = poll(c)
    worker.execute(job)
    detail = clients["contributor"].get("/api/work/" + job["work"]["id"]).json()
    lease = detail["leases"][-1]
    approved = {
        "sha": lease["checkpoint"]["sha"],
        "review_digest": lease["review_digest"],
        "confirm_reviewed": True,
    }
    assert clients["contributor"].post(
        f"/api/leases/{lease['id']}/approve", json=approved
    ).status_code == 200
    app.state.dispatcher.tick()
    return job, lease


def pr_payload(number, sha, action="synchronize", merged=False):
    return {
        "action": action,
        "installation": {"id": 3001},
        "repository": {"id": 2001},
        "sender": {"id": 1001, "login": "maintainer"},
        "pull_request": {
            "number": number,
            "merged": merged,
            "head": {"ref": f"co4/submission/x/{sha[:12]}", "sha": sha},
        },
    }


def test_publish_creates_a_submission_row_with_pr_number_and_expected_sha(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    publication = app.state.github.publications[0]
    with app.state.db.read() as s:
        sub = s.scalar(select(Submission).where(Submission.work_id == job["work"]["id"]))
        assert sub is not None
        assert sub.pr_number == publication["number"]
        assert sub.head_branch == publication["branch"]
        assert sub.expected_head_sha == lease["checkpoint"]["sha"]
        assert sub.round == 1
        assert sub.state == "open"


def test_synchronize_at_the_expected_head_is_accepted(app, clients, tmp_path, monkeypatch):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    with app.state.db.read() as s:
        sub = s.scalar(select(Submission).where(Submission.work_id == job["work"]["id"]))
        number, expected = sub.pr_number, sub.expected_head_sha
    response = send_hook(
        app, pr_payload(number, expected), kind="pull_request", delivery="sync-expected"
    )
    assert response.status_code == 200
    with app.state.db.read() as s:
        sub = s.scalar(select(Submission).where(Submission.work_id == job["work"]["id"]))
        assert sub.state == "open"
        w = s.get(Work, job["work"]["id"])
        assert w.state == "submitted"


def test_synchronize_at_an_unexpected_head_escalates(app, clients, tmp_path, monkeypatch):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    with app.state.db.read() as s:
        sub = s.scalar(select(Submission).where(Submission.work_id == job["work"]["id"]))
        number = sub.pr_number
    response = send_hook(
        app, pr_payload(number, "f" * 40), kind="pull_request", delivery="sync-unexpected"
    )
    assert response.status_code == 200
    with app.state.db.read() as s:
        sub = s.scalar(select(Submission).where(Submission.work_id == job["work"]["id"]))
        assert sub.state == "escalated"


def test_closed_merged_updates_submission_and_work_state(app, clients, tmp_path, monkeypatch):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    with app.state.db.read() as s:
        sub = s.scalar(select(Submission).where(Submission.work_id == job["work"]["id"]))
        number, expected = sub.pr_number, sub.expected_head_sha
    response = send_hook(
        app,
        pr_payload(number, expected, action="closed", merged=True),
        kind="pull_request",
        delivery="closed-merged",
    )
    assert response.status_code == 200
    with app.state.db.read() as s:
        sub = s.scalar(select(Submission).where(Submission.work_id == job["work"]["id"]))
        assert sub.state == "merged"
        w = s.get(Work, job["work"]["id"])
        assert w.state == "merged"


def test_unknown_pr_number_is_a_no_op(app, clients):
    response = send_hook(
        app, pr_payload(999999, "a" * 40), kind="pull_request", delivery="unknown-pr"
    )
    assert response.status_code == 200
    with app.state.db.read() as s:
        assert not list(s.scalars(select(Submission)))
