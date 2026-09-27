"""Executable steps for specs/review-iteration.feature.

Drives the whole loop through the same code paths as production: the real Worker (offline
fixture harness, real Git and real test runs) for both the initial contribution and the
revision round, signed GitHub webhooks for reviews/synchronize/merge, the outbox dispatcher,
and the offline DemoGitHub gateway standing in for the GitHub App.
"""

from __future__ import annotations

import pytest
from conftest import poll
from pytest_bdd import given, scenarios, then, when
from sqlalchemy import select
from test_bdd_iteration import _permit, review_payload
from test_integrations import send_hook
from test_submission_lookup import pr_payload
from test_worker import configured

from co4.iteration import NEEDS_HUMAN_LABEL
from co4.models import Project, Submission, Work

scenarios("review-iteration.feature")

FEEDBACK = "Please document what average() returns for an empty collection."


@pytest.fixture
def ctx():
    return {"reviews": 0}


def _submission(app, ctx):
    with app.state.db.read() as s:
        return s.scalar(select(Submission).where(Submission.work_id == ctx["work_id"]))


def _latest_lease(clients, ctx):
    return clients["contributor"].get(f"/api/work/{ctx['work_id']}").json()["leases"][-1]


def _approve_latest(clients, ctx):
    lease = _latest_lease(clients, ctx)
    response = clients["contributor"].post(
        f"/api/leases/{lease['id']}/approve",
        json={
            "sha": lease["checkpoint"]["sha"],
            "review_digest": lease["review_digest"],
            "confirm_reviewed": True,
        },
    )
    assert response.status_code == 200, response.text
    return lease


def _request_changes(app, monkeypatch, ctx, sha):
    ctx["reviews"] += 1
    _permit(app, monkeypatch, {"maintainer"})
    response = send_hook(
        app,
        review_payload(
            _submission(app, ctx).pr_number,
            sha,
            "changes_requested",
            f"e2e-review-{ctx['reviews']}",
            "maintainer",
            FEEDBACK,
        ),
        kind="pull_request_review",
        delivery=f"e2e-review-{ctx['reviews']}",
    )
    assert response.status_code == 200
    app.state.settings.demo = True  # _permit turns demo off to exercise can_manage


# --- Given ----------------------------------------------------------------------------------


@given("a project that allows only one revision round")
def one_revision_round(app):
    with app.state.db.transaction() as s:
        project = s.scalar(select(Project))
        project.policy = {**project.policy, "max_revision_rounds": 1}


@given("a contributor's device published a human-approved draft PR")
def published_pr(app, clients, tmp_path, monkeypatch, ctx):
    worker, device = configured(app, clients, tmp_path, monkeypatch)
    job = poll(device)
    worker.execute(job)
    ctx.update(worker=worker, device=device, work_id=job["work"]["id"], number=job["work"]["number"])
    _approve_latest(clients, ctx)
    app.state.dispatcher.tick()
    sub = _submission(app, ctx)
    assert sub is not None and sub.state == "open" and sub.round == 1
    ctx["approved_sha"] = sub.expected_head_sha


# --- When -----------------------------------------------------------------------------------


@when("a maintainer requests changes on the PR")
def maintainer_requests_changes(app, monkeypatch, ctx):
    _request_changes(app, monkeypatch, ctx, ctx["approved_sha"])


@when("a maintainer requests changes on the revised PR")
def maintainer_requests_changes_again(app, monkeypatch, ctx):
    _request_changes(app, monkeypatch, ctx, _submission(app, ctx).expected_head_sha)


@when("the original contributor's device polls for work")
def original_device_polls(ctx):
    ctx["revision_job"] = poll(ctx["device"])


@when("the device completes the revision on top of the approved commit")
def device_completes_revision(ctx):
    assert ctx["revision_job"]["lease"]["kind"] == "revision"
    ctx["worker"].execute(ctx["revision_job"])


@when("the contributor approves the exact revision commit")
def contributor_approves_revision(app, clients, ctx):
    lease = _approve_latest(clients, ctx)
    assert lease["kind"] == "revision"
    ctx["revision_sha"] = lease["checkpoint"]["sha"]
    app.state.dispatcher.tick()


@when("the maintainer approves the PR on GitHub")
def maintainer_approves_on_github(app, monkeypatch, ctx):
    _permit(app, monkeypatch, {"maintainer"})
    sub = _submission(app, ctx)
    response = send_hook(
        app,
        review_payload(sub.pr_number, sub.expected_head_sha, "approved", "e2e-ok", "maintainer"),
        kind="pull_request_review",
        delivery="e2e-approved",
    )
    assert response.status_code == 200
    app.state.settings.demo = True


@when("the PR is merged")
def pr_merged(app, ctx):
    sub = _submission(app, ctx)
    response = send_hook(
        app,
        pr_payload(sub.pr_number, sub.expected_head_sha, action="closed", merged=True),
        kind="pull_request",
        delivery="e2e-merged",
    )
    assert response.status_code == 200


# --- Then -----------------------------------------------------------------------------------


@then("a revision round is queued for the PR")
def revision_round_queued(app, ctx):
    assert _submission(app, ctx).state == "changes_requested"
    with app.state.db.read() as s:
        assert s.get(Work, ctx["work_id"]).state == "revision_requested"


@then("it receives a revision lease carrying the reviewer feedback")
def revision_lease_with_feedback(ctx):
    lease = ctx["revision_job"]["lease"]
    assert lease["kind"] == "revision"
    assert lease["round"] == 2
    assert lease["feedback"]["body"] == FEEDBACK
    assert lease["feedback"]["reviewer"] == "maintainer"
    # The handover checkpoint is the approved, App-frozen submission commit.
    assert ctx["revision_job"]["work"]["checkpoint"]["sha"] == ctx["approved_sha"]


@then("the App fast-forwards the PR branch to the approved revision commit")
def pr_fast_forwarded(app, ctx):
    sub = _submission(app, ctx)
    assert app.state.github.branch_updates == [
        {"branch": sub.head_branch, "sha": ctx["revision_sha"]}
    ]
    assert len(app.state.github.publications) == 1  # the same PR, never a second one
    assert sub.expected_head_sha == ctx["revision_sha"]
    assert sub.round == 2 and sub.state == "awaiting_rereview"
    # Fast-forward only: the revision commit descends from the approved commit.
    ctx["worker"].repository.command(
        "merge-base", "--is-ancestor", ctx["approved_sha"], ctx["revision_sha"]
    )


@then("the PR receives a round status comment for the maintainer")
def round_status_comment(app, ctx):
    sub = _submission(app, ctx)
    app.state.dispatcher.tick()
    number, body = app.state.github.pr_comments[f"co4:submission:{sub.id}:round:2"]
    assert number == sub.pr_number
    assert "ready for re-review" in body and ctx["revision_sha"] in body
    assert FEEDBACK.replace("@", "@​") in body


@then("GitHub's synchronize event for the new head is accepted")
def synchronize_accepted(app, ctx):
    response = send_hook(
        app,
        pr_payload(_submission(app, ctx).pr_number, ctx["revision_sha"]),
        kind="pull_request",
        delivery="e2e-sync",
    )
    assert response.status_code == 200
    assert _submission(app, ctx).state == "awaiting_rereview"


@then("the submission is approved")
def submission_approved(app, ctx):
    assert _submission(app, ctx).state == "approved"


@then("the work item and the submission are merged")
def merged(app, ctx):
    assert _submission(app, ctx).state == "merged"
    with app.state.db.read() as s:
        assert s.get(Work, ctx["work_id"]).state == "merged"


@then("the submission is escalated for a maintainer")
def escalated(app, ctx):
    assert _submission(app, ctx).state == "escalated"


@then("the issue and the PR are labelled co4:needs-human")
def labelled(app, ctx):
    app.state.dispatcher.tick()
    labels = app.state.github.labels_added
    assert (ctx["number"], NEEDS_HUMAN_LABEL) in labels
    assert (_submission(app, ctx).pr_number, NEEDS_HUMAN_LABEL) in labels


@then("the PR receives an escalation comment for the maintainer")
def escalation_comment(app, ctx):
    sub = _submission(app, ctx)
    number, body = app.state.github.pr_comments[f"co4:submission:{sub.id}:escalated"]
    assert number == sub.pr_number
    assert "Revision round limit reached" in body and "1 of 1" in body


@then("no further revision lease is offered")
def no_further_lease(app, ctx):
    assert poll(ctx["device"])["lease"] is None
    with app.state.db.read() as s:
        assert s.get(Work, ctx["work_id"]).state == "submitted"
