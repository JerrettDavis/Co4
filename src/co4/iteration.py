"""PR review iteration: routes reviewer feedback on a Co4-submitted PR back to a
contributor/agent as a new revision round, without ever changing the PR itself.

The PR's head branch is App-owned and frozen at a human-approved commit (see
``GitHub.publish`` / ``Submission``). A round of review fixes is therefore a brand-new
*revision* lease, built on top of that approved commit, requiring its own human approval
before the App fast-forwards the existing submission branch (see co4.outbox / co4.domain for
the complete()/approve() side of this, and the "update_submission" outbox job).

This module owns the state machine on ``Submission.state``:
    open -> changes_requested -> revising -> awaiting_rereview -> merged|closed
                                                                 \\-> escalated

and the loop guards that keep it from spinning forever:
  - only one open revision per submission at a time
  - each review triggers at most once (Submission.last_review_id)
  - reviews on an outdated head commit are ignored
  - a revision trigger the project's policy has not enabled is ignored
  - reaching Policy.max_revision_rounds escalates to a human instead of looping

Round semantics: ``Submission.round`` is the *version* of the PR head -- 1 for the initial
publish, incremented each time a revision is fast-forwarded onto it. The number of revision
rounds already spent is therefore ``round - 1``, and ``max_revision_rounds`` counts revision
rounds (not versions): with the default of 3 a PR may be revised 3 times, and the next
changes-requested review escalates instead.
"""

from __future__ import annotations

import json
import re
import time

from sqlalchemy import select

from co4.domain import ROLES, audit, get, public_receipt, status
from co4.models import Device, Lease, Member, Outbox, Project, Submission, User, Work
from co4.schemas import Policy
from co4.security import redact

# Submission states in which a revision round is already in flight; a new review/comment
# trigger while one of these is active must not start a second, concurrent round.
# `awaiting_rereview` is deliberately NOT here: once a round has been fast-forwarded onto the PR,
# a fresh changes-requested review on that new head is exactly how the next round starts.
OPEN_REVISION_STATES = {"changes_requested", "revising"}
# States in which the PR itself is already settled; nothing more to revise.
SETTLED_STATES = {"merged", "closed"}

NEEDS_HUMAN_LABEL = "co4:needs-human"


def request_revision(
    s,
    coordinator,
    project,
    work: Work,
    submission: Submission,
    *,
    review_id: str,
    trigger: str,
    feedback_text: str,
    reviewer_login: str,
    head_sha: str | None = None,
) -> str:
    """Record reviewer feedback and mark `work`/`submission` for a new revision round.

    Returns a short outcome tag describing what happened: "requested", "duplicate",
    "already_open", "outdated_head", "trigger_disabled", "settled", or "escalated". Callers
    (the webhook handler, tests) can use this to decide what else to log/assert; every outcome
    other than "requested" and "escalated" is a deliberate, silent no-op -- these are exactly
    the loop guards this module exists to enforce.
    """
    policy = Policy(**project.policy)
    if trigger not in policy.revision_triggers:
        return "trigger_disabled"
    if submission.state in SETTLED_STATES:
        return "settled"
    # A review of a diff that is no longer the PR's actual head is reviewing stale content;
    # never let it drive a new round the reviewer never saw.
    if head_sha is not None and head_sha != submission.expected_head_sha:
        return "outdated_head"
    if review_id and submission.last_review_id == review_id:
        return "duplicate"
    if submission.state in OPEN_REVISION_STATES:
        return "already_open"
    if revisions_spent(submission) >= policy.max_revision_rounds:
        escalate(
            s,
            project,
            work,
            submission,
            reason=f"Revision round limit reached (all {policy.max_revision_rounds} allowed "
            "revision rounds are used)",
        )
        return "escalated"
    # The feedback is stashed on the most recent lease for this work (the one whose
    # publish/round this review is about). The revision lease created for this round (see
    # iteration.route(), a later task) copies it forward into its own `feedback` column.
    base_lease = s.scalar(
        select(Lease).where(Lease.work_id == work.id).order_by(Lease.created.desc())
    )
    if base_lease is not None:
        base_lease.feedback = {
            "body": redact(feedback_text)[:20_000],
            "trigger": trigger,
            "review_id": review_id,
            "reviewer": reviewer_login,
        }
    submission.state = "changes_requested"
    submission.last_review_id = review_id
    submission.updated = coordinator.clock()
    work.state = "revision_requested"
    audit(
        s,
        work.project_id,
        "github",
        "revision.requested",
        work_id=work.id,
        review_id=review_id,
        trigger=trigger,
        reviewer=reviewer_login,
        round=submission.round + 1,
    )
    status(
        s,
        work,
        "A reviewer requested changes. This is queued for a new revision round "
        f"(revision {revisions_spent(submission) + 1} of {policy.max_revision_rounds}).",
    )
    return "requested"


def revisions_spent(submission: Submission) -> int:
    """Revision rounds already fast-forwarded onto the PR (see module docstring)."""
    return max(0, submission.round - 1)


def _no_mentions(text: str) -> str:
    # Never let quoted reviewer text or summaries ping people from an App comment.
    return text.replace("@", "@​")


def pr_comment(s, project_id: str, submission: Submission, tag: str, body: str) -> None:
    """Queue a maintainer-facing comment on the submission's PR (not the originating issue).

    One comment per (submission, tag): redelivery or a retried outbox job edits the same
    comment rather than posting a duplicate (see GitHub.pr_comment's marker reconciliation).
    """
    key = f"pr_comment:{submission.id}:{tag}"
    marker = f"co4:submission:{submission.id}:{tag}"
    payload = {"number": submission.pr_number, "marker": marker, "body": body}
    job = s.scalar(select(Outbox).where(Outbox.key == key))
    if job is None:
        s.add(Outbox(key=key, project_id=project_id, kind="pr_comment", payload=payload))
    else:
        job.payload = payload
        job.state = "pending"
        job.available = time.time()
        job.lock_token = ""
        job.locked_until = 0


def round_update_comment(s, project: Project, submission: Submission, lease: Lease) -> None:
    """Queue the per-round status/receipt comment on the PR once a revision has been
    fast-forwarded onto it and is waiting for a fresh review."""
    policy = Policy(**project.policy)
    feedback = lease.feedback or {}
    quoted_text = _no_mentions(feedback.get("body") or "(no review text)")[:2000]
    quoted = "\n".join("> " + line for line in quoted_text.splitlines() or [""])
    summary = _no_mentions(lease.summary or "")[:4000]
    red = (
        "skipped (existing tests already cover the change)"
        if (lease.evidence or {}).get("red_exit") is None
        else "failing test added first"
    )
    reviewer = _no_mentions(feedback.get("reviewer") or "a reviewer")
    body = (
        f"**Co4 · revision {revisions_spent(submission)} of {policy.max_revision_rounds} "
        "ready for re-review**\n\n"
        f"The App fast-forwarded this PR to `{lease.checkpoint.get('sha', '')}` after the "
        "contributor approved that exact commit. Nothing else about the PR changed.\n\n"
        f"Feedback addressed (from {reviewer}):\n\n{quoted}\n\n"
        f"### What changed this round\n\n{summary}\n\n"
        f"Evidence: baseline passed · regression test {red} · implementation passed · "
        "verification passed. Worker-reported; independent CI is still required.\n\n"
        "<details><summary>Execution receipt for this round</summary>\n\n"
        f"```json\n{json.dumps(public_receipt(lease), indent=2)}\n```\n</details>"
    )
    pr_comment(s, project.id, submission, f"round:{submission.round}", body)


def review_approved(s, work: Work, submission: Submission, *, head_sha: str | None) -> bool:
    """Record a permitted reviewer's approval of the current PR head. Display/state only --
    Co4 never merges. Returns True when the submission state changed."""
    if submission.state not in {"open", "awaiting_rereview"}:
        return False
    if head_sha is not None and head_sha != submission.expected_head_sha:
        return False
    submission.state = "approved"
    submission.updated = time.time()
    audit(s, work.project_id, "github", "review.approved_on_github", work_id=work.id)
    return True


def escalate(s, project, work: Work, submission: Submission, *, reason: str) -> None:
    """Move a submission to `escalated` and ask a human to intervene: a `co4:needs-human`
    label plus a maintainer-facing status comment. Never auto-recovers -- unlike the
    same-user orphan reclaim in Coordinator.poll, there is no safe automatic next step once a
    project's own round budget (or affinity window, see co4.domain.Coordinator.sweep) is
    exhausted."""
    submission.state = "escalated"
    submission.updated = time.time()
    audit(s, work.project_id, "co4", "revision.escalated", work_id=work.id, reason=reason)
    # Label both the originating issue and the PR itself: maintainers triage from either.
    for target, number in (("issue", work.number), ("pr", submission.pr_number)):
        key = f"label:{work.id}:{target}:{NEEDS_HUMAN_LABEL}"
        if not s.scalar(select(Outbox).where(Outbox.key == key)):
            s.add(
                Outbox(
                    key=key,
                    project_id=project.id,
                    kind="label",
                    payload={"work_id": work.id, "number": number, "label": NEEDS_HUMAN_LABEL},
                )
            )
    policy = Policy(**project.policy)
    pr_comment(
        s,
        project.id,
        submission,
        "escalated",
        f"**Co4 · needs a maintainer**\n\n{reason}.\n\n"
        f"Revision rounds used: {revisions_spent(submission)} of "
        f"{policy.max_revision_rounds}. Co4 will not start another revision round for this PR "
        f"automatically; it has been labelled `{NEEDS_HUMAN_LABEL}`.",
    )
    status(
        s,
        work,
        f"Escalated for maintainer attention: {reason}. A maintainer must intervene "
        "before another revision round can begin.",
    )


MENTION = re.compile(r"@([A-Za-z0-9][A-Za-z0-9-]{0,38})")


def _base_lease(s, work_id: str) -> Lease | None:
    return s.scalar(select(Lease).where(Lease.work_id == work_id).order_by(Lease.created.desc()))


def _mentioned_members_with_devices(s, project: Project, base_lease: Lease | None) -> set[str]:
    """User ids @-mentioned in the feedback that triggered this round, restricted to Co4
    members (any watching, non-suspended role) who have at least one enabled device."""
    if base_lease is None:
        return set()
    text = (base_lease.feedback or {}).get("body", "")
    logins = {m.group(1).lower() for m in MENTION.finditer(text)}
    if not logins:
        return set()
    found = set()
    for login in logins:
        user = s.scalar(select(User).where(User.login == login))
        if not user:
            continue
        member = s.get(Member, (project.id, user.id))
        if not member or member.role not in ROLES or not member.watching:
            continue
        if s.scalar(select(Device).where(Device.user_id == user.id, Device.enabled.is_(True))):
            found.add(user.id)
    return found


def route(s, coordinator, device: Device, w: Work) -> bool:
    """Return True when `device` may currently claim revision-requested work `w`.

    Three-tier affinity order, for `revision_affinity_seconds` measured from the moment the
    revision was requested (Submission.updated):
      (a) the original contributor (the user who held the most recent lease on this work);
      (b) an @-mentioned Co4 member who has an enabled device;
      (c) anyone else normally eligible, once the window has elapsed.
    Devices belonging to a user in tier (a)/(b) are always allowed through immediately -- the
    window only *withholds* the work from everyone else, it does not make (a)/(b) wait.
    """
    project = get(s, Project, w.project_id)
    policy = Policy(**project.policy)
    submission = s.scalar(select(Submission).where(Submission.work_id == w.id))
    if not submission or submission.state == "escalated":
        # Escalated means a human must intervene; never auto-route it back into a device's
        # hands until a maintainer resets it.
        return False
    base_lease = _base_lease(s, w.id)
    original_contributor = base_lease.user_id if base_lease else None
    if device.user_id == original_contributor:
        return True
    if device.user_id in _mentioned_members_with_devices(s, project, base_lease):
        return True
    elapsed = coordinator.clock() - submission.updated
    return elapsed >= policy.revision_affinity_seconds


def any_device_eligible(s, coordinator, w: Work) -> bool:
    """Whether *any* currently enabled device in the project could ever pick up `w` right now,
    ignoring the affinity window. Used by Coordinator.sweep() to decide whether an
    affinity-window expiry with nobody available is a dead end that needs a human."""
    project = get(s, Project, w.project_id)
    for member in s.scalars(select(Member).where(Member.project_id == project.id)):
        for device in s.scalars(
            select(Device).where(Device.user_id == member.user_id, Device.enabled.is_(True))
        ):
            if coordinator.eligible(s, device, w):
                return True
    return False
