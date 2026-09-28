"""Covers the maintainer-facing side of a revision round (per-round PR status comment,
escalation comment + PR label), the next-round re-review loop, and the race where GitHub
delivers the App's own fast-forward `synchronize` before the outbox records the new head."""

from __future__ import annotations

from sqlalchemy import select
from test_bdd_iteration import _permit, review_payload
from test_integrations import send_hook
from test_iteration_evidence import PHASES, _complete, _heartbeat_through, _open_revision_round
from test_submission_lookup import pr_payload

from co4.iteration import NEEDS_HUMAN_LABEL
from co4.models import Lease, Submission, Work


def _submission(app, work_id):
    with app.state.db.read() as s:
        return s.scalar(select(Submission).where(Submission.work_id == work_id))


def _approved_revision(app, clients, tmp_path, monkeypatch):
    """Drive one revision round up to (but not including) the outbox fast-forward."""
    job, lease, sub, worker, revision_job = _open_revision_round(
        app, clients, tmp_path, monkeypatch
    )
    lease_id = revision_job["lease"]["id"]
    generation = revision_job["lease"]["generation"]
    _heartbeat_through(worker, lease_id, generation, PHASES)
    completed = _complete(worker, lease_id, generation, revision_job["work"]["number"], red_exit=None)
    assert completed.status_code == 200, completed.text
    approved = {
        "sha": "c" * 40,
        "review_digest": completed.json()["review_digest"],
        "confirm_reviewed": True,
    }
    response = clients["contributor"].post(f"/api/leases/{lease_id}/approve", json=approved)
    assert response.status_code == 200, response.text
    return job, sub, lease_id


def test_round_update_posts_a_maintainer_comment_on_the_pr(app, clients, tmp_path, monkeypatch):
    job, sub, lease_id = _approved_revision(app, clients, tmp_path, monkeypatch)
    app.state.dispatcher.tick()
    app.state.dispatcher.tick()  # The round comment is queued by the fast-forward delivery.
    comments = app.state.github.pr_comments
    marker = f"co4:submission:{sub.id}:round:2"
    assert marker in comments
    number, body = comments[marker]
    assert number == sub.pr_number
    assert "revision 1 of 3 ready for re-review" in body
    assert "c" * 40 in body
    assert "skipped" in body  # red_exit None is reported honestly
    assert '"schema": "co4.receipt.v1"' in body


def test_a_new_review_after_rereview_opens_the_next_round(app, clients, tmp_path, monkeypatch):
    job, sub, lease_id = _approved_revision(app, clients, tmp_path, monkeypatch)
    app.state.dispatcher.tick()
    assert _submission(app, job["work"]["id"]).state == "awaiting_rereview"
    _permit(app, monkeypatch, {"trusted-reviewer"})
    response = send_hook(
        app,
        review_payload(sub.pr_number, "c" * 40, "changes_requested", "rv-next", "trusted-reviewer"),
        kind="pull_request_review",
        delivery="review-next-round",
    )
    assert response.status_code == 200
    reloaded = _submission(app, job["work"]["id"])
    assert reloaded.state == "changes_requested"
    assert reloaded.round == 2
    with app.state.db.read() as s:
        assert s.get(Work, job["work"]["id"]).state == "revision_requested"


def test_app_fast_forward_synchronize_racing_the_outbox_is_not_escalated(
    app, clients, tmp_path, monkeypatch
):
    job, sub, lease_id = _approved_revision(app, clients, tmp_path, monkeypatch)
    # GitHub delivers the synchronize for the App's own PATCH before the dispatcher's
    # post-delivery transaction has recorded the new expected head.
    with app.state.db.read() as s:
        assert s.get(Lease, lease_id).state == "publishing"
    response = send_hook(
        app, pr_payload(sub.pr_number, "c" * 40), kind="pull_request", delivery="sync-race"
    )
    assert response.status_code == 200
    assert _submission(app, job["work"]["id"]).state != "escalated"
    app.state.dispatcher.tick()
    assert _submission(app, job["work"]["id"]).state == "awaiting_rereview"


def test_unexpected_head_escalates_with_label_and_pr_comment(app, clients, tmp_path, monkeypatch):
    job, sub, lease_id = _approved_revision(app, clients, tmp_path, monkeypatch)
    app.state.dispatcher.tick()
    response = send_hook(
        app, pr_payload(sub.pr_number, "e" * 40), kind="pull_request", delivery="sync-foreign"
    )
    assert response.status_code == 200
    assert _submission(app, job["work"]["id"]).state == "escalated"
    app.state.dispatcher.tick()
    labels = app.state.github.labels_added
    assert (sub.pr_number, NEEDS_HUMAN_LABEL) in labels
    assert (job["work"]["number"], NEEDS_HUMAN_LABEL) in labels
    number, body = app.state.github.pr_comments[f"co4:submission:{sub.id}:escalated"]
    assert number == sub.pr_number and "needs a maintainer" in body


def test_a_permitted_approval_of_the_current_head_marks_the_submission_approved(
    app, clients, tmp_path, monkeypatch
):
    job, sub, lease_id = _approved_revision(app, clients, tmp_path, monkeypatch)
    app.state.dispatcher.tick()
    _permit(app, monkeypatch, {"trusted-reviewer"})
    stale = review_payload(sub.pr_number, "a" * 40, "approved", "rv-ok-stale", "trusted-reviewer")
    send_hook(app, stale, kind="pull_request_review", delivery="approve-stale")
    assert _submission(app, job["work"]["id"]).state == "awaiting_rereview"
    current = review_payload(sub.pr_number, "c" * 40, "approved", "rv-ok", "trusted-reviewer")
    send_hook(app, current, kind="pull_request_review", delivery="approve-current")
    assert _submission(app, job["work"]["id"]).state == "approved"
