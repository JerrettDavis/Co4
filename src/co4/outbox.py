from __future__ import annotations
import time
import uuid
from sqlalchemy import delete, or_, select
from co4.domain import audit, get, pr_body, project_data, status
from co4.models import Event, Lease, OAuthState, Outbox, Project, Session, Work
from co4.security import redact

class Dispatcher:
    def __init__(self, db, coordinator, github, clock=time.time):
        self.db, self.coordinator, self.github, self.clock = db, coordinator, github, clock

    def tick(self, limit=10):
        with self.db.transaction() as s:
            self.coordinator.sweep(s)
            s.execute(delete(Session).where(Session.expires < self.clock()))
            s.execute(delete(OAuthState).where(OAuthState.expires < self.clock()))
            s.execute(delete(Event).where(Event.created < self.clock() - self.coordinator.settings.event_retention_days * 86400))
        for _ in range(limit):
            claim = self.claim()
            if not claim:
                break
            job_id, lock = claim
            try:
                self.deliver(job_id, lock)
            except Exception as exc:
                with self.db.transaction() as s:
                    job = get(s, Outbox, job_id)
                    if job.lock_token != lock:
                        continue
                    job.attempts += 1
                    job.state = "failed" if job.attempts >= 10 else "pending"
                    job.available = self.clock() + min(3600, 2 ** job.attempts * 5)
                    job.error = redact(str(exc))[:2000]
                    job.locked_until = 0
                    if job.kind == "publish":
                        lease = get(s, Lease, job.payload["lease_id"])
                        lease.error = "Publication retry pending: " + job.error

    def claim(self):
        with self.db.transaction() as s:
            job = s.scalar(select(Outbox).where(Outbox.state.in_(["pending", "working"]),
                Outbox.available <= self.clock(), Outbox.locked_until < self.clock()).order_by(Outbox.available).limit(1))
            if not job:
                return None
            job.state = "working"
            job.lock_token = uuid.uuid4().hex
            job.locked_until = self.clock() + 300
            return job.id, job.lock_token

    def deliver(self, job_id, lock):
        with self.db.read() as s:
            job = get(s, Outbox, job_id)
            if job.lock_token != lock:
                return
            p = get(s, Project, job.project_id)
            project = project_data(p)
            payload, kind = job.payload, job.kind
            if kind == "publish":
                lease = get(s, Lease, payload["lease_id"])
                w = get(s, Work, lease.work_id)
                valid = (p.active and lease.state == "publishing" and w.active_lease == lease.id
                         and payload["sha"] == lease.approved_sha == lease.checkpoint.get("sha"))
                if p.policy.get("require_maintainer_approval"):
                    valid = valid and lease.maintainer_sha == payload["sha"]
                publish_args = (lease.id, payload["sha"], w.number, w.title, pr_body(lease, w.number))
            else:
                valid = p.active
        result = ""
        if valid:
            if kind == "status":
                link = self.coordinator.settings.public_url + "/#work/" + payload["work_id"]
                body = payload["body"] + f"\n\n[Open allocation, evidence, and human review]({link})"
                self.github.status_comment(project, payload["number"], payload["work_id"], body)
            elif kind == "publish":
                result = self.github.publish(project, *publish_args)
        with self.db.transaction() as s:
            job = get(s, Outbox, job_id)
            if job.lock_token != lock:
                return
            job.state = "done" if valid else "cancelled"
            job.locked_until = 0
            job.error = ""
            if valid and kind == "publish":
                lease = get(s, Lease, payload["lease_id"])
                w = get(s, Work, lease.work_id)
                # Record external reality even if cancellation raced with the HTTP request.
                lease.pr_url = result
                if lease.state == "publishing" and w.active_lease == lease.id:
                    lease.state = "submitted"
                    lease.error = ""
                    w.state = "submitted"
                    w.active_lease = None
                else:
                    lease.error = "PR creation raced with a policy change. Maintainer intervention required."
                audit(s, p.id, "github-app", "pull_request.created", lease_id=lease.id, sha=payload["sha"], url=result)
                status(s, w, "A human-approved draft PR has been created. Independent CI and maintainer review are still required.")
