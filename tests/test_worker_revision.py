"""Worker revision mode (task 7): a revision lease starts from the human-approved submission
commit, carries the reviewer feedback into the harness prompt, may skip a genuine red phase,
and produces a commit that fast-forwards the existing PR branch."""

from __future__ import annotations

from conftest import poll
from sqlalchemy import select
from test_bdd_iteration import _permit, review_payload
from test_integrations import send_hook
from test_worker import configured

from co4.models import Submission

FEEDBACK = "Please document what average() returns for an empty collection."


def _approve(clients, work_id):
    lease = clients["contributor"].get("/api/work/" + work_id).json()["leases"][-1]
    approved = {
        "sha": lease["checkpoint"]["sha"],
        "review_digest": lease["review_digest"],
        "confirm_reviewed": True,
    }
    response = clients["contributor"].post(f"/api/leases/{lease['id']}/approve", json=approved)
    assert response.status_code == 200, response.text
    return lease


def published_with_feedback(app, clients, tmp_path, monkeypatch):
    worker, device = configured(app, clients, tmp_path, monkeypatch)
    job = poll(device)
    worker.execute(job)
    _approve(clients, job["work"]["id"])
    app.state.dispatcher.tick()
    with app.state.db.read() as s:
        sub = s.scalar(select(Submission).where(Submission.work_id == job["work"]["id"]))
    _permit(app, monkeypatch, {"trusted-reviewer"})
    response = send_hook(
        app,
        review_payload(
            sub.pr_number, sub.expected_head_sha, "changes_requested", "rv-w", "trusted-reviewer",
            FEEDBACK,
        ),
        kind="pull_request_review",
        delivery="review-worker",
    )
    assert response.status_code == 200
    app.state.settings.demo = True
    return worker, device, job, sub


def test_revision_lease_builds_on_the_approved_commit_and_fast_forwards_the_pr(
    app, clients, tmp_path, monkeypatch
):
    worker, device, job, sub = published_with_feedback(app, clients, tmp_path, monkeypatch)
    revision_job = poll(device)
    assert revision_job["lease"]["kind"] == "revision"
    worker.execute(revision_job)

    detail = clients["contributor"].get("/api/work/" + job["work"]["id"]).json()
    lease = detail["leases"][-1]
    assert lease["id"] == revision_job["lease"]["id"]
    assert lease["state"] == "awaiting_review"
    evidence = lease["evidence"]
    assert evidence["baseline_commit"] == sub.expected_head_sha  # started at the approved commit
    assert evidence["red_exit"] is None  # documentation feedback: red phase skipped
    assert evidence["red_commit"] == evidence["baseline_commit"]
    # The review diff is this round's delta only, not the cumulative change since main.
    assert "Arithmetic mean" in lease["diff"]
    assert "+    return sum(values) / len(values) if values else 0" not in lease["diff"]
    # The new commit descends from the approved commit, so the App can fast-forward.
    worker.repository.command("merge-base", "--is-ancestor", sub.expected_head_sha, "HEAD")

    events = clients["contributor"].get(f"/api/leases/{lease['id']}/events").json()
    prompts = [e["text"] for e in events if e["kind"] == "prompt"]
    assert len(prompts) == 2  # spec is re-attested, not regenerated
    assert all(FEEDBACK in p and "revision phase" in p for p in prompts)
    assert any(e["kind"] == "red_skipped" for e in events)

    _approve(clients, job["work"]["id"])
    app.state.dispatcher.tick()
    assert app.state.github.branch_updates == [
        {"branch": sub.head_branch, "sha": lease["checkpoint"]["sha"]}
    ]
    with app.state.db.read() as s:
        reloaded = s.get(Submission, sub.id)
        assert reloaded.round == 2
        assert reloaded.state == "awaiting_rereview"
        assert reloaded.expected_head_sha == lease["checkpoint"]["sha"]


def test_revision_red_phase_still_requires_a_genuine_failure_when_tests_change(
    app, clients, tmp_path, monkeypatch
):
    worker, device, job, sub = published_with_feedback(app, clients, tmp_path, monkeypatch)
    original = worker.mock

    def mock(phase):
        # Reviewer asked for missing values to be ignored: a genuine behavioral defect.
        path = worker.repository.path
        original(phase)
        if phase == "red":
            tests = (path / "test_average.py").read_text(encoding="utf-8")
            (path / "test_average.py").write_text(
                tests
                + "    def test_ignores_missing(self):\n"
                + "        self.assertEqual(average([1, None, 3]),2)\n",
                encoding="utf-8",
            )
        elif phase == "green":
            (path / "average.py").write_text(
                "def average(values):\n"
                "    values = [v for v in values or [] if v is not None]\n"
                "    return sum(values) / len(values) if values else 0\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(worker, "mock", mock)
    worker.execute(poll(device))
    lease = clients["contributor"].get("/api/work/" + job["work"]["id"]).json()["leases"][-1]
    assert lease["state"] == "awaiting_review"
    assert lease["evidence"]["red_exit"] == 1
    assert lease["evidence"]["red_commit"] != lease["evidence"]["baseline_commit"]
    assert "test_ignores_missing" in lease["diff"]
