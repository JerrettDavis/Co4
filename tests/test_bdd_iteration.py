"""Executable steps for specs/iteration.feature.

Reuses the offline DemoGitHub gateway and the same ``send_hook``/``configured``/``poll``
helpers as the other webhook and worker BDD suites, plus ``_publish`` from
test_submission_lookup (drives a contribution through to a published draft PR).
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when
from sqlalchemy import select
from test_integrations import send_hook
from test_submission_lookup import _publish

from co4.models import Lease, Project, Submission, Work
from co4.schemas import Policy

scenarios("iteration.feature")


@pytest.fixture
def ctx():
    return {}


def _submission(app, work_id):
    with app.state.db.read() as s:
        return s.scalar(select(Submission).where(Submission.work_id == work_id))


def _permit(app, monkeypatch, allowed_logins):
    app.state.settings.demo = False
    monkeypatch.setattr(
        app.state.github,
        "can_manage",
        lambda installation, repo_id, repo, login: login in allowed_logins,
        raising=False,
    )


def review_payload(number, sha, state, review_id, login, body=""):
    return {
        "action": "submitted",
        "installation": {"id": 3001},
        "repository": {"id": 2001},
        "sender": {"id": 9999, "login": login},
        "review": {
            "id": review_id,
            "state": state,
            "commit_id": sha,
            "user": {"login": login},
            "body": body,
        },
        "pull_request": {"number": number, "head": {"sha": sha}},
    }


def comment_payload(number, login, body):
    return {
        "action": "created",
        "installation": {"id": 3001},
        "repository": {"id": 2001},
        "sender": {"id": 9999, "login": login},
        "comment": {"id": 555, "body": body},
        "issue": {"number": number, "pull_request": {"url": "https://example/pulls/1"}},
    }


# --- Given ----------------------------------------------------------------------------------


@given("a published draft PR with an approved commit")
def published_pr(app, clients, tmp_path, monkeypatch, ctx):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])
    ctx.update(job=job, lease=lease, number=sub.pr_number, sha=sub.expected_head_sha)


@given("a submission that has already used every revision round its policy allows")
def submission_at_round_limit(app, clients, tmp_path, monkeypatch, ctx):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    with app.state.db.transaction() as s:
        sub = s.scalar(select(Submission).where(Submission.work_id == job["work"]["id"]))
        project = s.scalar(select(Project))
        policy = Policy(**project.policy)
        # round is the PR head version (1 = initial publish), so max_revision_rounds revisions
        # already spent means round == max_revision_rounds + 1.
        sub.round = policy.max_revision_rounds + 1
        number, sha = sub.pr_number, sub.expected_head_sha
    ctx.update(job=job, lease=lease, number=number, sha=sha)


# --- When -------------------------------------------------------------------------------


@when("a reviewer with write access requests changes on the current head")
def review_changes_requested(app, monkeypatch, ctx):
    _permit(app, monkeypatch, {"trusted-reviewer"})
    response = send_hook(
        app,
        review_payload(
            ctx["number"], ctx["sha"], "changes_requested", "rv-1", "trusted-reviewer",
            "Please add a regression test",
        ),
        kind="pull_request_review",
        delivery="review-1",
    )
    assert response.status_code == 200
    ctx["review_id"] = "rv-1"


@when("a reviewer without write access requests changes on the current head")
def review_changes_requested_denied(app, monkeypatch, ctx):
    _permit(app, monkeypatch, set())
    response = send_hook(
        app,
        review_payload(ctx["number"], ctx["sha"], "changes_requested", "rv-2", "random-user"),
        kind="pull_request_review",
        delivery="review-denied",
    )
    assert response.status_code == 200


@when("a maintainer comments slash co4 revise on the pull request")
def slash_revise(app, monkeypatch, ctx):
    _permit(app, monkeypatch, {"maintainer"})
    response = send_hook(
        app,
        comment_payload(ctx["number"], "maintainer", "/co4 revise please handle the edge case"),
        kind="issue_comment",
        delivery="revise-comment",
    )
    assert response.status_code == 200


@when("a reviewer with write access requests changes on a stale head")
def review_stale_head(app, monkeypatch, ctx):
    _permit(app, monkeypatch, {"trusted-reviewer"})
    response = send_hook(
        app,
        review_payload(
            ctx["number"], "f" * 40, "changes_requested", "rv-stale", "trusted-reviewer"
        ),
        kind="pull_request_review",
        delivery="review-stale",
    )
    assert response.status_code == 200


@when("that same review is redelivered")
def redeliver_same_review(app, monkeypatch, ctx):
    _permit(app, monkeypatch, {"trusted-reviewer"})
    response = send_hook(
        app,
        review_payload(
            ctx["number"], ctx["sha"], "changes_requested", ctx["review_id"], "trusted-reviewer"
        ),
        kind="pull_request_review",
        delivery="review-1-redelivered",
    )
    assert response.status_code == 200


@when("a second reviewer with write access requests changes on the current head")
def second_reviewer_review(app, monkeypatch, ctx):
    _permit(app, monkeypatch, {"second-reviewer"})
    response = send_hook(
        app,
        review_payload(
            ctx["number"], ctx["sha"], "changes_requested", "rv-second", "second-reviewer"
        ),
        kind="pull_request_review",
        delivery="review-second",
    )
    assert response.status_code == 200


# --- Then -------------------------------------------------------------------------------


@then("the submission moves to changes requested")
def submission_changes_requested(app, ctx):
    sub = _submission(app, ctx["job"]["work"]["id"])
    assert sub.state == "changes_requested"


@then("the work item is queued for revision")
def work_queued_for_revision(app, ctx):
    with app.state.db.read() as s:
        w = s.get(Work, ctx["job"]["work"]["id"])
        assert w.state == "revision_requested"


@then("the submission is still open")
def submission_still_open(app, ctx):
    sub = _submission(app, ctx["job"]["work"]["id"])
    assert sub.state == "open"
    with app.state.db.read() as s:
        w = s.get(Work, ctx["job"]["work"]["id"])
        assert w.state == "submitted"


@then("only one revision round was requested")
def only_one_revision_round(app, ctx):
    sub = _submission(app, ctx["job"]["work"]["id"])
    assert sub.state == "changes_requested"
    with app.state.db.read() as s:
        leases = list(s.scalars(select(Lease).where(Lease.work_id == ctx["job"]["work"]["id"])))
    # Neither guard creates a second lease -- routing (a later task) is what would create a
    # revision lease, and it must never run twice for what is really one round.
    assert len(leases) == 1


@then("the submission is escalated for a maintainer")
def submission_escalated(app, ctx):
    sub = _submission(app, ctx["job"]["work"]["id"])
    assert sub.state == "escalated"


@then("a needs-human label is queued")
def needs_human_label_queued(app, ctx):
    app.state.dispatcher.tick()
    assert (ctx["job"]["work"]["number"], "co4:needs-human") in app.state.github.labels_added
