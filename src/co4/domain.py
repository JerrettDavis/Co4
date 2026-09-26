from __future__ import annotations
import time
from fastapi import HTTPException
from sqlalchemy import select
from co4.models import Audit, Decline, Device, Lease, Member, Outbox, Project, User, Work
from co4.schemas import Policy
from co4.security import canonical, digest, redact

ACTIVE = {"offered", "running", "blocked", "awaiting_review", "dibbed", "publishing"}
STALEABLE = {"running", "blocked", "awaiting_review"}
ROLES = {"owner", "maintainer", "triager", "contributor"}

def fail(status: int, message: str):
    raise HTTPException(status, message)

def get(s, model, key):
    item = s.get(model, key)
    if item is None:
        fail(404, "Not found")
    return item

def member(s, project: Project, user_id: str, roles: set[str] | None = None) -> Member:
    m = s.get(Member, (project.id, user_id))
    if m is None or m.role == "suspended" or (roles and m.role not in roles):
        fail(403, "Project permission required")
    return m

def visible(s, project: Project, user_id: str) -> bool:
    m = s.get(Member, (project.id, user_id))
    return (not project.private and (not m or m.role != "suspended")) or bool(m and m.role in ROLES)

def project_data(p: Project) -> dict:
    return {k: getattr(p, k) for k in ("id", "repository", "repository_id", "installation_id", "default_branch", "private", "active", "policy", "budget_tokens", "reserved_tokens", "spent_tokens")}

def work_data(w: Work) -> dict:
    return {k: getattr(w, k) for k in ("id", "project_id", "number", "title", "body", "labels", "priority", "state", "generation", "active_lease", "checkpoint", "created", "updated")}

def lease_data(s, lease: Lease, *, detail: bool = False) -> dict:
    result = {k: getattr(lease, k) for k in ("id", "work_id", "user_id", "device_id", "generation", "state", "phase", "last_contact", "created", "token_reservation", "usage", "evidence", "checkpoint", "approved_sha", "maintainer_sha", "review_digest", "dib_user", "dib_expires", "pr_url", "error")}
    result["contributor"] = get(s, User, lease.user_id).login
    if detail:
        result.update(summary=lease.summary, diff=lease.diff)
    return result

def audit(s, project_id: str, actor: str, action: str, **detail):
    s.add(Audit(project_id=project_id, actor=actor, action=action, detail=detail))

def status(s, w: Work, text: str):
    # A single status task per work item, replaced with the latest state until delivery.
    key = "status:" + w.id
    job = s.scalar(select(Outbox).where(Outbox.key == key))
    data = {"work_id": w.id, "number": w.number, "body": f"**Co4 · {w.state.replace('_', ' ')}**\n\n" + text}
    if not job:
        s.add(Outbox(key=key, project_id=w.project_id, kind="status", payload=data))
    else:
        job.payload = data
        job.state = "pending"
        job.available = time.time()
        # Invalidates a dispatcher claim. Remote side effects are reconciled on next run.
        job.lock_token = ""
        job.locked_until = 0

class Coordinator:
    def __init__(self, db, settings, clock=time.time):
        self.db, self.settings, self.clock = db, settings, clock

    def eligible(self, s, device: Device, w: Work) -> bool:
        p = get(s, Project, w.project_id)
        m = s.get(Member, (p.id, device.user_id))
        policy = Policy(**p.policy)
        if not p.active or not device.enabled or not m or m.role not in ROLES or not m.watching:
            return False
        if s.get(Decline, (device.user_id, w.id)):
            return False
        if policy.access == "verified" and not (m.verified or m.role in {"owner", "maintainer"}):
            return False
        if policy.access == "maintainers" and m.role not in {"owner", "maintainer"}:
            return False
        if set(w.labels) & set(policy.excluded_labels):
            return False
        if set(w.labels) & set(policy.maintainer_labels) and m.role not in {"owner", "maintainer"}:
            return False
        if device.labels and not set(device.labels) & set(w.labels):
            return False
        return True

    def allocate(self, s, device: Device, w: Work) -> Lease | None:
        if w.state != "queued" or w.active_lease or not self.eligible(s, device, w):
            return None
        if s.scalar(select(Lease).where(Lease.user_id == device.user_id, Lease.state.in_(ACTIVE))):
            return None
        p = get(s, Project, w.project_id)
        amount = min(device.max_tokens, Policy(**p.policy).max_task_tokens)
        if p.spent_tokens + p.reserved_tokens + amount > p.budget_tokens:
            return None
        p.reserved_tokens += amount
        w.generation += 1
        lease = Lease(work_id=w.id, user_id=device.user_id, device_id=device.id, generation=w.generation,
            state="running" if device.autonomy == "automatic" else "offered",
            token_reservation=amount, created=self.clock(), last_contact=self.clock())
        s.add(lease)
        s.flush()
        w.active_lease = lease.id
        w.state = "in_progress"
        audit(s, p.id, device.user_id, "allocation.created", lease_id=lease.id, generation=lease.generation)
        status(s, w, f"Work allocated. Execution mode: {device.autonomy}. Human approval is required before a draft PR is created.")
        return lease

    def orphaned(self, lease: Lease) -> bool:
        # Scoped to `running` only. Unlike STALEABLE (used by the unrelated 12-hour cross-user dib
        # flow), this is a same-user, same-heartbeat-cadence liveness check, and heartbeat cadence is
        # only a meaningful liveness signal while a device is actively executing. `blocked` already has
        # its own no-time-pressure recovery path (`recover()`); `awaiting_review` is entered once,
        # deliberately, after a device stops heartbeating by design (see `complete()`), and must only
        # ever be resolved by a human review action, never auto-reclaimed by a sibling device's routine
        # poll within the grace period.
        return lease.state == "running" and self.clock() - lease.last_contact >= self.settings.orphan_seconds

    def poll(self, s, device: Device, repositories: list[str]) -> Lease | None:
        device.last_seen = self.clock()
        if not device.enabled:
            return None
        current = s.scalar(select(Lease).where(Lease.device_id == device.id, Lease.state.in_(ACTIVE)))
        if current:
            return current
        blocking = s.scalar(select(Lease).where(Lease.user_id == device.user_id, Lease.state.in_(ACTIVE)))
        if blocking:
            # `blocking.device_id` cannot equal `device.id` here (that case is `current`, above), so this
            # lease belongs to a different device row of the same person. This device is live right now
            # (it just polled). If the lease holder went silent past a reasonable heartbeat grace period,
            # it is very likely the device row from a dead/replaced worker process (e.g. re-enrollment
            # after a crash) rather than a device that is merely mid-task, and it can never check in again.
            # Reclaim it instead of deadlocking every device this person owns until a human intervenes.
            if self.orphaned(blocking):
                w = get(s, Work, blocking.work_id)
                self.release(s, blocking, terminal="reclaimed")
                status(s, w, "The previous device went silent past the heartbeat grace period. "
                              "The allocation was reclaimed so another of your devices is not blocked.")
            else:
                return None
        candidates = list(s.scalars(select(Work).where(Work.state == "queued")))
        def score(w):
            policy = Policy(**get(s, Project, w.project_id).policy)
            return (-w.created if policy.strategy == "fifo" else w.priority * 3600 + self.clock() - w.created)
        candidates.sort(key=lambda w: (score(w), w.id), reverse=True)
        for w in candidates:
            if get(s, Project, w.project_id).repository not in repositories:
                continue
            lease = self.allocate(s, device, w)
            if lease:
                return lease
        return None

    def fence(self, s, lease_id: str, device: Device, generation: int, states=None) -> tuple[Lease, Work]:
        lease = get(s, Lease, lease_id)
        w = get(s, Work, lease.work_id)
        if lease.device_id != device.id:
            fail(403, "This device does not own the lease")
        if not device.enabled:
            fail(409, "Device is paused or revoked")
        if w.active_lease != lease.id or generation != w.generation or lease.generation != generation:
            fail(409, "Lease fencing token is obsolete; stop the process")
        if not get(s, Project, w.project_id).active:
            fail(409, "Project is disabled")
        if lease.state not in (states or {"running"}):
            fail(409, "Lease does not allow this operation: " + lease.state)
        member(s, get(s, Project, w.project_id), device.user_id)
        return lease, w

    def settle(self, s, lease: Lease):
        if lease.settled:
            return
        p = get(s, Project, get(s, Work, lease.work_id).project_id)
        usage = lease.usage or {}
        known = usage.get("complete") and usage.get("input_tokens") is not None and usage.get("output_tokens") is not None
        # Unknown consumption is NOT zero. Retain the entire reservation in quota accounting.
        observed = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        amount = observed if known else (0 if lease.state == "offered" else max(observed, lease.token_reservation))
        p.reserved_tokens = max(0, p.reserved_tokens - lease.token_reservation)
        p.spent_tokens += amount
        lease.settled = True

    def release(self, s, lease: Lease, *, terminal="released", requeue=True):
        w = get(s, Work, lease.work_id)
        self.settle(s, lease)
        lease.state = terminal
        if w.active_lease == lease.id:
            w.active_lease = None
            w.state = "queued" if requeue else "validation_pending"
        audit(s, w.project_id, lease.user_id, "allocation." + terminal, lease_id=lease.id)

    def sweep(self, s):
        now = self.clock()
        leases = list(s.scalars(select(Lease).where(Lease.state.in_(ACTIVE))))
        for lease in leases:
            w = get(s, Work, lease.work_id)
            if lease.state == "offered" and now - lease.created >= 1800:
                self.release(s, lease)
                if not s.get(Decline, (lease.user_id, w.id)):
                    s.add(Decline(user_id=lease.user_id, work_id=w.id))
                status(s, w, "The allocation offer expired without acceptance. Work is available again.")
            elif lease.state == "dibbed" and lease.dib_expires <= now:
                candidate = s.get(Device, lease.dib_device)
                self.release(s, lease, terminal="reassigned")
                if candidate:
                    self.allocate(s, candidate, w)
                status(s, w, "The 12-hour recovery window expired. The old lease is fenced out; checkpoint history is preserved.")

    def stale(self, lease: Lease) -> bool:
        return lease.state in STALEABLE and self.clock() - lease.last_contact >= self.settings.stale_seconds

    def dib(self, s, lease: Lease, device: Device):
        w = get(s, Work, lease.work_id)
        if not self.stale(lease):
            fail(409, "Work becomes available for dibs after 12 hours without communication")
        if device.user_id == lease.user_id or not self.eligible(s, device, w):
            fail(403, "An eligible different contributor is required")
        if s.scalar(select(Lease).where(Lease.user_id == device.user_id, Lease.state.in_(ACTIVE))):
            fail(409, "Finish or release your current allocation before requesting a handover")
        lease.state = "dibbed"
        lease.dib_user = device.user_id
        lease.dib_device = device.id
        lease.dib_expires = self.clock() + self.settings.recovery_seconds
        audit(s, w.project_id, device.user_id, "allocation.dibbed", lease_id=lease.id, recovery_deadline=lease.dib_expires)
        status(s, w, "Another contributor requested this stale task. The original contributor has 12 hours to explicitly recover it.")

    def recover(self, s, lease: Lease, user_id: str):
        w = get(s, Work, lease.work_id)
        if lease.user_id != user_id:
            fail(403, "Only the original contributor can recover this allocation")
        if lease.state == "dibbed" and self.clock() >= lease.dib_expires:
            fail(409, "The recovery window expired")
        if lease.state not in {"dibbed", "blocked"} and not self.stale(lease):
            fail(409, "This allocation does not need recovery")
        d = get(s, Device, lease.device_id)
        if not self.eligible(s, d, w):
            fail(403, "Contributor or device is no longer eligible")
        lease.state = "awaiting_review" if lease.review_digest else "running"
        lease.last_contact = self.clock()
        lease.dib_user = lease.dib_device = lease.dib_expires = None
        lease.error = ""
        audit(s, w.project_id, user_id, "allocation.recovered", lease_id=lease.id)
        status(s, w, "The original contributor explicitly recovered this allocation.")

    def complete(self, s, lease: Lease, w: Work, payload, diff: str):
        if lease.phase != "ready":
            fail(409, "Complete all workflow phases before requesting review")
        e = payload.evidence
        if (e.baseline_exit, e.red_exit, e.green_exit, e.verify_exit) != (0, 1, 0, 0):
            fail(422, "Required evidence: passing baseline, failing test, passing implementation, passing verification")
        if e.profile != Policy(**get(s, Project, w.project_id).policy).test_profile:
            fail(409, "Test profile changed; rerun using the current policy")
        if payload.checkpoint.sha != e.green_commit:
            fail(422, "Final tested commit must equal the checkpoint SHA")
        lease.checkpoint = payload.checkpoint.model_dump()
        w.checkpoint = lease.checkpoint
        lease.evidence = e.model_dump()
        lease.usage = payload.usage.model_dump()
        lease.summary = redact(payload.summary)
        lease.diff = diff
        lease.review_digest = digest(canonical({"checkpoint": lease.checkpoint, "evidence": lease.evidence,
            "summary": lease.summary, "diff": lease.diff, "usage": lease.usage}))
        lease.approved_sha = lease.maintainer_sha = ""
        lease.state = "awaiting_review"
        lease.last_contact = self.clock()
        w.state = "review"
        self.settle(s, lease)
        audit(s, w.project_id, lease.user_id, "review.requested", lease_id=lease.id, sha=payload.checkpoint.sha)
        status(s, w, "Implementation and test evidence are ready. No PR has been created. The contributor must review and approve the exact commit.")

    def approve(self, s, lease: Lease, user_id: str, payload):
        w = get(s, Work, lease.work_id)
        p = get(s, Project, w.project_id)
        m = member(s, p, user_id)
        if not p.active or w.active_lease != lease.id or lease.state != "awaiting_review":
            fail(409, "This review is not current")
        if payload.sha != lease.checkpoint.get("sha") or payload.review_digest != lease.review_digest:
            fail(409, "The commit or review changed; reload the review")
        if user_id == lease.user_id:
            lease.approved_sha = payload.sha
        elif m.role in {"owner", "maintainer"}:
            lease.maintainer_sha = payload.sha
        else:
            fail(403, "Only the contributor or a maintainer may approve this review")
        audit(s, p.id, user_id, "review.approved", lease_id=lease.id, sha=payload.sha, digest=payload.review_digest)
        policy = Policy(**p.policy)
        if lease.approved_sha and (not policy.require_maintainer_approval or lease.maintainer_sha):
            lease.state = "publishing"
            w.state = "publishing"
            key = f"publish:{lease.id}:{payload.sha}"
            if not s.scalar(select(Outbox).where(Outbox.key == key)):
                s.add(Outbox(key=key, project_id=p.id, kind="publish", payload={"lease_id": lease.id, "sha": payload.sha}))


def public_receipt(lease: Lease) -> dict:
    """Strict allowlist. Prompts, paths, device identity and transcript text never enter a PR receipt."""
    u = lease.usage or {}
    lines = (lease.diff or "").splitlines()
    evidence_keys = ("execution_mode", "interaction_capture", "profile", "baseline_exit", "red_exit", "green_exit", "verify_exit", "spec_sha256",
                     "behavior_sha256", "tests_sha256", "baseline_commit", "red_commit", "green_commit")
    return {"schema": "co4.receipt.v1", "run": lease.id, "commit": lease.checkpoint.get("sha"),
        "allocation_generation": lease.generation, "accounting_scope": "this allocation only",
        "change_metrics": {"files_changed": sum(line.startswith("diff --git ") for line in lines),
            "lines_added": sum(line.startswith("+") and not line.startswith("+++") for line in lines),
            "lines_deleted": sum(line.startswith("-") and not line.startswith("---") for line in lines),
            "scope": "reviewed diff, including specification artifacts; not a difficulty score"},
        "verification": {key: (lease.evidence or {}).get(key) for key in evidence_keys},
        "duration_seconds": u.get("duration_seconds"), "input_tokens": u.get("input_tokens"),
        "cached_input_tokens": u.get("cached_input_tokens"), "output_tokens": u.get("output_tokens"),
        "reported_cost_usd": u.get("cost_usd"), "usage_complete": u.get("complete", False),
        "usage_source": u.get("source", "unavailable"), "evidence": "worker-reported; independent CI required",
        "workflow": "specification → behavior → red → green → verify → human approval"}


def pr_body(lease: Lease, number: int) -> str:
    summary = lease.summary.replace("@", "@\u200b")
    return (f"Closes #{number}\n\n## Contribution\n\n{summary}\n\n"
        "## Execution receipt\n\nThe contributor approved this exact commit and review package. "
        "This is a draft PR, not permission to merge. Usage is reported by the harness, not independently billed. "
        "Null means unavailable, never free. No prompts or transcripts are included.\n\n"
        f"```json\n{__import__('json').dumps(public_receipt(lease), indent=2)}\n```\n\n"
        f"<!-- co4:submission:{lease.id} -->")
