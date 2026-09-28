"""Unit tests for the PR review iteration data model additions: the new ``Submission`` model,
the new ``Lease`` revision columns, and the new ``Policy`` fields (schemas.py). These exercise
the model/schema layer directly, independent of the (not yet implemented) routing behavior."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from co4.models import Device, Lease, Project, Submission, Work
from co4.schemas import Policy


def _project_and_work(app):
    with app.state.db.read() as s:
        p = s.scalar(select(Project))
        w = s.scalar(select(Work).where(Work.number == 41))
        return p.id, w.id


def _device(s, user_id):
    d = Device(user_id=user_id, name="test device", token_hash="x" * 64, harness="mock")
    s.add(d)
    s.flush()
    return d.id


class TestSubmissionModel:
    def test_defaults(self, app):
        project_id, work_id = _project_and_work(app)
        with app.state.db.transaction() as s:
            sub = Submission(
                work_id=work_id,
                project_id=project_id,
                pr_number=101,
                head_branch="co4/submission/abc123/def456789012",
                expected_head_sha="a" * 40,
            )
            s.add(sub)
            s.flush()
            sub_id = sub.id
            assert sub.round == 1
            assert sub.state == "open"
            assert sub.last_review_id is None
            assert sub.created > 0 and sub.updated > 0
        with app.state.db.read() as s:
            row = s.get(Submission, sub_id)
            assert row is not None
            assert row.pr_number == 101
            assert row.head_branch == "co4/submission/abc123/def456789012"
            assert row.expected_head_sha == "a" * 40

    def test_unique_on_project_and_pr_number(self, app):
        project_id, work_id = _project_and_work(app)
        with app.state.db.transaction() as s:
            s.add(
                Submission(
                    work_id=work_id,
                    project_id=project_id,
                    pr_number=202,
                    head_branch="co4/submission/x/y",
                    expected_head_sha="b" * 40,
                )
            )
        with pytest.raises(IntegrityError):
            with app.state.db.transaction() as s:
                s.add(
                    Submission(
                        work_id=work_id,
                        project_id=project_id,
                        pr_number=202,
                        head_branch="co4/submission/other/branch",
                        expected_head_sha="c" * 40,
                    )
                )

    def test_same_pr_number_allowed_across_projects(self, app):
        # A second project may reuse the same PR number; the constraint is per-project.
        project_id, work_id = _project_and_work(app)
        with app.state.db.transaction() as s:
            other = Project(
                repository="co4-demo/other-repo",
                repository_id=2002,
                installation_id=3002,
                owner_id=s.scalar(select(Project)).owner_id,
                policy=Policy().model_dump(),
            )
            s.add(other)
            s.flush()
            other_id = other.id
            s.add(
                Submission(
                    work_id=work_id,
                    project_id=project_id,
                    pr_number=303,
                    head_branch="co4/submission/p1/aaa",
                    expected_head_sha="d" * 40,
                )
            )
            s.add(
                Submission(
                    work_id=work_id,
                    project_id=other_id,
                    pr_number=303,
                    head_branch="co4/submission/p2/bbb",
                    expected_head_sha="e" * 40,
                )
            )


class TestLeaseRevisionColumns:
    def test_defaults(self, app):
        project_id, work_id = _project_and_work(app)
        with app.state.db.transaction() as s:
            owner_id = s.scalar(select(Project)).owner_id
            lease = Lease(
                work_id=work_id,
                user_id=owner_id,
                device_id=_device(s, owner_id),
                generation=1,
                token_reservation=1000,
            )
            s.add(lease)
            s.flush()
            assert lease.kind == "initial"
            assert lease.parent_lease_id is None
            assert lease.round == 1
            assert lease.feedback == {}

    def test_revision_lease_references_parent(self, app):
        project_id, work_id = _project_and_work(app)
        with app.state.db.transaction() as s:
            owner_id = s.scalar(select(Project)).owner_id
            device_id = _device(s, owner_id)
            parent = Lease(
                work_id=work_id,
                user_id=owner_id,
                device_id=device_id,
                generation=1,
                token_reservation=1000,
            )
            s.add(parent)
            s.flush()
            revision = Lease(
                work_id=work_id,
                user_id=owner_id,
                device_id=device_id,
                generation=2,
                token_reservation=1000,
                kind="revision",
                parent_lease_id=parent.id,
                round=2,
                feedback={"body": "please add a test"},
            )
            s.add(revision)
            s.flush()
            revision_id = revision.id
        with app.state.db.read() as s:
            row = s.get(Lease, revision_id)
            assert row.kind == "revision"
            assert row.parent_lease_id == parent.id
            assert row.round == 2
            assert row.feedback == {"body": "please add a test"}


class TestPolicyIterationFields:
    def test_defaults(self):
        policy = Policy()
        assert policy.max_revision_rounds == 3
        assert policy.revision_affinity_seconds == 43200
        assert policy.revision_triggers == ["changes_requested", "revise_comment"]

    def test_round_trips_through_dict(self):
        policy = Policy(max_revision_rounds=5, revision_affinity_seconds=3600)
        dumped = policy.model_dump()
        restored = Policy(**dumped)
        assert restored.max_revision_rounds == 5
        assert restored.revision_affinity_seconds == 3600

    def test_rejects_out_of_range_max_revision_rounds(self):
        with pytest.raises(ValueError):
            Policy(max_revision_rounds=0)
        with pytest.raises(ValueError):
            Policy(max_revision_rounds=21)

    def test_rejects_unknown_revision_trigger(self):
        with pytest.raises(ValueError):
            Policy(revision_triggers=["not_a_real_trigger"])

    def test_accepts_ci_failure_trigger(self):
        policy = Policy(revision_triggers=["ci_failure"])
        assert policy.revision_triggers == ["ci_failure"]
