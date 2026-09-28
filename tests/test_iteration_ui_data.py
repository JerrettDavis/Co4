"""The dashboard and work-detail APIs expose what static/app.js renders for PR review
iteration: the submission's round, state (incl. escalation), and latest feedback excerpt."""

from __future__ import annotations

from test_bdd_iteration import _permit, review_payload
from test_integrations import send_hook
from test_iteration_routing import _submission
from test_submission_lookup import _publish


def test_dashboard_and_detail_expose_submission_round_state_and_feedback(
    app, clients, tmp_path, monkeypatch
):
    job, lease = _publish(app, clients, tmp_path, monkeypatch)
    sub = _submission(app, job["work"]["id"])

    dashboard = clients["maintainer"].get("/api/dashboard").json()
    (row,) = [x for x in dashboard["submissions"] if x["work_id"] == job["work"]["id"]]
    assert row["pr_number"] == sub.pr_number
    assert row["round"] == 1 and row["revisions"] == 0
    assert row["state"] == "open" and row["feedback"] is None

    _permit(app, monkeypatch, {"trusted-reviewer"})
    send_hook(
        app,
        review_payload(
            sub.pr_number, sub.expected_head_sha, "changes_requested", "rv-ui", "trusted-reviewer",
            "Handle negative numbers.\n\n" + "x" * 400,
        ),
        kind="pull_request_review",
        delivery="review-ui",
    )
    app.state.settings.demo = True

    detail = clients["maintainer"].get("/api/work/" + job["work"]["id"]).json()
    submission = detail["submission"]
    assert submission["state"] == "changes_requested"
    feedback = submission["feedback"]
    assert feedback["reviewer"] == "trusted-reviewer"
    assert feedback["trigger"] == "changes_requested"
    assert feedback["excerpt"].startswith("Handle negative numbers. xxx")
    assert len(feedback["excerpt"]) <= 281


def test_work_without_a_pr_has_no_submission(app, clients):
    work = clients["maintainer"].get("/api/dashboard").json()["work"][0]
    assert clients["maintainer"].get("/api/work/" + work["id"]).json()["submission"] is None
