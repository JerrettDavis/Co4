from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
from pathlib import Path
import time
from urllib.parse import urlencode, urlparse
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from co4.config import Settings
from co4.db import Database
from co4.domain import (ACTIVE, Coordinator, audit, fail, get, lease_data, member, project_data,
    public_receipt, status, visible, work_data)
from co4.github import DemoGitHub, GitHub, GitHubError
from co4.models import Audit, Decline, Delivery, Device, DeviceFlow, Event, Lease, Member, OAuthState, Outbox, Project, Session, User, Work
from co4.outbox import Dispatcher
from co4.schemas import (Approval, CheckpointRequest, Complete, DeviceCodeResponse, DeviceCreate,
    DevicePollRequest, DevicePollResponse, DeviceUpdate, Dib, EnrollProject, Failure, Heartbeat,
    MemberUpdate, Policy, Poll, ProjectUpdate, Watch, WorkerEvent)
from co4.security import Vault, digest, redact, token
from co4.seed import seed

LOG = logging.getLogger("co4")

class DemoLogin(BaseModel):
    login: str

class BodyLimit:
    def __init__(self, app, maximum=3_000_000):
        self.app, self.maximum = app, maximum
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.maximum:
                return await JSONResponse({"detail": "Request body too large"}, 413)(scope, receive, send)
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)


def _origins_match(origin: str, public_url: str, demo: bool) -> bool:
    """Return True when `origin` should be accepted alongside `public_url`.

    Acceptance rules:
      1. Exact match after trailing-slash strip and case folding.
      2. In demo mode only, both sides resolve to a loopback host
         (localhost, 127.0.0.1, or ::1) sharing the same scheme and port.
    """
    a = origin.rstrip("/").lower()
    b = public_url.rstrip("/").lower()
    if a == b:
        return True
    if not demo:
        return False
    pa, pb = urlparse(origin), urlparse(public_url)
    loopback = {"localhost", "127.0.0.1", "::1"}
    return (pa.scheme == pb.scheme and pa.port == pb.port
            and pa.hostname in loopback and pb.hostname in loopback)


def create_app(settings: Settings | None = None, github=None, db=None) -> FastAPI:
    cfg = settings or Settings()
    cfg.validate()
    database = db or Database(cfg.database_url)
    vault = Vault(cfg.data_key)
    coordinator = Coordinator(database, cfg)
    gateway = github or (DemoGitHub() if cfg.demo else GitHub(cfg))
    dispatcher = Dispatcher(database, coordinator, gateway)
    if cfg.demo:
        seed(database)

    DEVICE_SCOPES = "read:user user:email"
    DEVICE_INTERVAL_DEFAULT = 5
    DEVICE_EXPIRES_IN_DEFAULT = 600  # 10 minutes
    DEVICE_FLOW_POST_PATHS = {"/auth/github/device/code", "/auth/github/device/poll"}

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        async def loop():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(dispatcher.tick)
                except Exception:
                    LOG.exception("Dispatcher tick failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except TimeoutError:
                    pass
        task = asyncio.create_task(loop()) if cfg.background else None
        yield
        stop.set()
        if task:
            await task

    app = FastAPI(title="Co4 control plane", version="0.1.0a2", lifespan=lifespan,
                  docs_url=None, openapi_url="/api/openapi.json", redoc_url=None)
    app.state.db, app.state.coordinator, app.state.github = database, coordinator, gateway
    app.state.settings, app.state.dispatcher, app.state.vault = cfg, dispatcher, vault
    app.add_middleware(BodyLimit)

    @app.exception_handler(GitHubError)
    async def github_error(_, exc):
        return JSONResponse({"detail": str(exc)}, 502)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.startswith("/webhooks/") and request.url.path not in DEVICE_FLOW_POST_PATHS:
            if request.cookies.get("co4_session"):
                if request.headers.get("x-co4-csrf") != "1":
                    return JSONResponse({"detail": "CSRF header required"}, 403)
                origin = request.headers.get("origin")
                if origin and not _origins_match(origin, cfg.public_url, cfg.demo):
                    return JSONResponse({"detail": "Origin is not allowed"}, 403)
        response = await call_next(request)
        response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "same-origin", "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://github.com"})
        return response

    def credentials(request):
        header = request.headers.get("authorization", "")
        return header[7:] if header.startswith("Bearer ") else request.cookies.get("co4_session", "")

    def user(request: Request) -> User:
        raw = credentials(request)
        with database.read() as s:
            auth = s.get(Session, digest(raw)) if raw else None
            if not auth or auth.expires <= time.time():
                fail(401, "Sign in to continue")
            return get(s, User, auth.user_id)

    def device(request: Request) -> Device:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            fail(401, "A device bearer token is required")
        with database.read() as s:
            d = s.scalar(select(Device).where(Device.token_hash == digest(header[7:])))
            if not d:
                fail(401, "Device token is invalid or revoked")
            return d

    def gh_user_token(request: Request) -> str:
        with database.read() as s:
            auth = get(s, Session, digest(credentials(request)))
            if not auth.github_token:
                fail(409, "GitHub authentication is not available in offline demo mode")
            return vault.open(auth.github_token)

    def create_session(s, u, access=""):
        raw = token("co4_session")
        s.add(Session(token_hash=digest(raw), user_id=u.id, expires=time.time() + 7 * 86400,
                      github_token=vault.seal(access) if access else ""))
        return raw

    def session_cookie(response, raw):
        response.set_cookie("co4_session", raw, httponly=True, secure=cfg.secure_cookies,
                            samesite="lax", max_age=7*86400, path="/")
        return response

    @app.get("/healthz")
    def health():
        with database.read() as s:
            s.execute(select(1))
        return {"status": "ok", "mode": "demo" if cfg.demo else "github", "version": "0.1.0a2"}

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        try:
            u = user(request)
            identity = {"id": u.id, "login": u.login}
        except HTTPException:
            identity = None
        return {"demo": cfg.demo, "public_url": cfg.public_url, "user": identity, "install_url": f"https://github.com/apps/{cfg.app_slug}/installations/new" if cfg.app_slug else None}

    @app.post("/auth/demo")
    def demo_login(payload: DemoLogin):
        if not cfg.demo:
            fail(404, "Not found")
        with database.transaction() as s:
            u = s.scalar(select(User).where(User.login == payload.login))
            if not u or payload.login not in {"maintainer", "contributor", "backup"}:
                fail(400, "Choose an offline demo identity")
            raw = create_session(s, u)
        return session_cookie(JSONResponse({"ok": True}), raw)

    @app.get("/auth/github")
    def login():
        if cfg.demo:
            return RedirectResponse("/")
        state = token("state")
        with database.transaction() as s:
            s.add(OAuthState(state_hash=digest(state), expires=time.time() + 600))
        response = RedirectResponse("https://github.com/login/oauth/authorize?" + urlencode({
            "client_id": cfg.client_id, "state": state, "redirect_uri": cfg.public_url + "/auth/github/callback"}))
        response.set_cookie("co4_oauth_state", state, httponly=True, secure=cfg.secure_cookies,
                            samesite="lax", max_age=600, path="/auth/github/callback")
        return response

    @app.get("/auth/github/callback")
    def callback(request: Request, code: str = "", state: str = ""):
        if cfg.demo:
            fail(404, "Not found")
        cookie = request.cookies.get("co4_oauth_state", "")
        if not code or not state or not hmac.compare_digest(state, cookie):
            fail(400, "OAuth state did not match")
        with database.transaction() as s:
            saved = s.get(OAuthState, digest(state))
            if not saved or saved.expires <= time.time():
                fail(400, "OAuth state expired or has already been used")
            s.delete(saved)
        access = gateway.exchange(code)
        identity = gateway.user(access)
        with database.transaction() as s:
            u = s.scalar(select(User).where(User.github_id == identity["id"]))
            if not u:
                u = User(github_id=identity["id"], login=identity["login"])
                s.add(u); s.flush()
            u.login = identity["login"]
            raw = create_session(s, u, access)
        response = session_cookie(RedirectResponse("/"), raw)
        response.delete_cookie("co4_oauth_state", path="/auth/github/callback")
        return response

    @app.post("/auth/github/device/code")
    def device_flow_init():
        """Initiate device flow. Returns DeviceCodeResponse."""
        if cfg.demo:
            fail(404, "Not found")
        data = gateway.request_device_code(DEVICE_SCOPES)
        interval = int(data.get("interval") or DEVICE_INTERVAL_DEFAULT)
        expires_at = time.time() + int(data.get("expires_in") or DEVICE_EXPIRES_IN_DEFAULT)
        with database.transaction() as s:
            s.add(DeviceFlow(
                device_code=data["device_code"],
                user_code=data["user_code"],
                scope=DEVICE_SCOPES,
                expires_at=expires_at,
                interval=interval,
            ))
        return JSONResponse({
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": data["verification_uri"],
            "expires_in": int(data.get("expires_in") or DEVICE_EXPIRES_IN_DEFAULT),
            "interval": interval,
        })

    @app.post("/auth/github/device/poll")
    def device_flow_poll(body: DevicePollRequest):
        """Poll device flow. Returns DevicePollResponse (or close shape)."""
        if cfg.demo:
            fail(404, "Not found")
        with database.read() as s:
            record = s.get(DeviceFlow, body.device_code)
            if not record:
                fail(404, "Unknown device code")
            if record.completed_at:
                fail(409, "Device code already consumed")
            if record.expires_at <= time.time():
                fail(400, "Device code expired")
        outcome = gateway.poll_device_token(body.device_code)
        status = outcome.get("status")
        if status == "authorized":
            access = outcome["access_token"]
            identity = gateway.user(access)
            with database.transaction() as s:
                record = s.get(DeviceFlow, body.device_code)
                if record.completed_at:
                    fail(409, "Device code already consumed")
                u = s.scalar(select(User).where(User.github_id == identity["id"]))
                if not u:
                    u = User(github_id=identity["id"], login=identity["login"])
                    s.add(u); s.flush()
                u.login = identity["login"]
                raw = create_session(s, u, access)
                record.completed_at = time.time()
                record.user_id = u.id
                s.flush()
                user_dict = {"id": u.id, "login": u.login, "github_id": u.github_id}
            return JSONResponse({
                "status": "authorized",
                "session_token": raw,
                "user": user_dict,
            })
        if status == "slow_down":
            return JSONResponse({"status": "slow_down", "interval": int(outcome.get("interval", DEVICE_INTERVAL_DEFAULT))})
        if status == "pending":
            return JSONResponse({"status": "pending"})
        if status == "expired":
            return JSONResponse({"status": "expired"})
        if status == "denied":
            return JSONResponse({"status": "denied"})
        fail(502, "Unexpected device flow outcome")

    @app.get("/auth/github/device")
    def device_flow_page(code: str = ""):
        """HTML page showing the user_code and a link to GitHub's verification URL.
        The Co4 app's frontend does the actual polling — this page is a static fallback
        that lets the user view their code in a new tab."""
        import html as html_mod
        safe_code = html_mod.escape(code)
        body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Co4 · Device sign-in</title>
<style>body{{font-family:system-ui,sans-serif;max-width:560px;margin:3rem auto;padding:0 1rem;color:#123e54}}
code{{font-family:ui-monospace,Menlo,monospace;background:#f3f6f8;padding:.15rem .4rem;border-radius:4px}}
.btn{{display:inline-block;padding:.6rem 1rem;background:#123e54;color:#fff;border:0;border-radius:6px;font:inherit;cursor:pointer;text-decoration:none}}
.muted{{color:#5a6e7e}}
#user_code{{font-size:2.5rem;letter-spacing:.15em;background:#f3f6f8;padding:1rem;border-radius:8px;text-align:center;font-weight:bold}}
</style></head>
<body>
<h1>Sign in with GitHub device flow</h1>
<p>1. Open <a href="https://github.com/login/device" target="_blank" rel="noopener">https://github.com/login/device</a> in any browser and sign in to GitHub.</p>
<p>2. Enter this code when prompted:</p>
<p id="user_code">{safe_code or '—'}</p>
<p class="muted">The Co4 app polls GitHub automatically while you complete authorization. This page is a fallback view of the code.</p>
<p><a class="btn" href="https://github.com/login/device" target="_blank" rel="noopener">Open GitHub device page</a></p>
</body></html>"""
        return HTMLResponse(body)

    @app.post("/auth/logout")
    def logout(request: Request, u=Depends(user)):
        with database.transaction() as s:
            s.delete(get(s, Session, digest(credentials(request))))
        response = JSONResponse({"ok": True})
        response.delete_cookie("co4_session")
        return response

    @app.get("/api/github/installations")
    def installations(request: Request, u=Depends(user)):
        return [{"id": x["id"], "account": x["account"]["login"]} for x in gateway.installations(gh_user_token(request))]

    @app.get("/api/github/repositories/{installation_id}")
    def repositories(installation_id: int, request: Request, u=Depends(user)):
        return [{"repository": x["full_name"], "admin": x.get("permissions", {}).get("admin", False)}
                for x in gateway.repositories(gh_user_token(request), installation_id)]

    @app.post("/api/projects")
    def enroll(payload: EnrollProject, request: Request, u=Depends(user)):
        if cfg.demo:
            fail(409, "Offline demo uses a fixture project; production enrollment requires GitHub")
        repositories = gateway.repositories(gh_user_token(request), payload.installation_id)
        repo = next((r for r in repositories if r["full_name"].lower() == payload.repository.lower()), None)
        if not repo or not repo.get("permissions", {}).get("admin"):
            fail(403, "Repository admin permission and access to this installation are required")
        with database.transaction() as s:
            if s.scalar(select(Project).where(Project.repository_id == repo["id"])):
                fail(409, "Repository is already enrolled")
            p = Project(repository=repo["full_name"], repository_id=repo["id"], installation_id=payload.installation_id,
                owner_id=u.id, private=repo["private"], default_branch=repo["default_branch"], policy=Policy().model_dump())
            s.add(p); s.flush()
            s.add(Member(project_id=p.id, user_id=u.id, role="owner", verified=True, watching=True))
            audit(s, p.id, u.id, "project.enrolled")
            return project_data(p)

    @app.get("/api/dashboard")
    def dashboard(u=Depends(user)):
        with database.read() as s:
            projects, works, leases = [], [], []
            for p in s.scalars(select(Project).order_by(Project.repository)):
                if not visible(s, p, u.id):
                    continue
                m = s.get(Member, (p.id, u.id))
                row = project_data(p)
                row.update(role=m.role if m else "visitor", watching=bool(m and m.watching), verified=bool(m and m.verified))
                projects.append(row)
                for w in s.scalars(select(Work).where(Work.project_id == p.id).order_by(Work.created)):
                    works.append(work_data(w))
                    if w.active_lease:
                        l = get(s, Lease, w.active_lease)
                        data = lease_data(s, l)
                        data["stale"] = coordinator.stale(l)
                        leases.append(data)
            devices = [{k: getattr(d, k) for k in ("id", "name", "harness", "autonomy", "enabled", "max_tokens", "labels", "last_seen")}
                for d in s.scalars(select(Device).where(Device.user_id == u.id))]
            return {"projects": projects, "work": works, "leases": leases, "devices": devices, "user": {"id": u.id, "login": u.login}}

    @app.put("/api/projects/{project_id}")
    def update_project(project_id: str, payload: ProjectUpdate, u=Depends(user)):
        with database.transaction() as s:
            p = get(s, Project, project_id)
            member(s, p, u.id, {"owner", "maintainer"})
            if payload.budget_tokens < p.spent_tokens + p.reserved_tokens:
                fail(409, "Budget cannot be lower than already-accounted usage and reservations")
            changed = payload.policy.model_dump() != p.policy or not payload.active
            p.policy, p.budget_tokens, p.active = payload.policy.model_dump(), payload.budget_tokens, payload.active
            if changed:
                for w in s.scalars(select(Work).where(Work.project_id == p.id, Work.active_lease.is_not(None))):
                    coordinator.release(s, get(s, Lease, w.active_lease), terminal="cancelled", requeue=False)
                    status(s, w, "Project policy changed. The old allocation is revoked; revalidation is required.")
            audit(s, p.id, u.id, "project.policy_updated")
            return project_data(p)

    @app.post("/api/projects/{project_id}/watch")
    def watch(project_id: str, payload: Watch, u=Depends(user)):
        with database.transaction() as s:
            p = get(s, Project, project_id)
            if not visible(s, p, u.id):
                fail(403, "A maintainer must enroll you in this private project first")
            m = s.get(Member, (p.id, u.id))
            if not m:
                m = Member(project_id=p.id, user_id=u.id, role="contributor", verified=False)
                s.add(m)
            m.watching = payload.enabled
            return {"watching": m.watching, "verified": m.verified}

    @app.get("/api/projects/{project_id}/members")
    def members(project_id: str, u=Depends(user)):
        with database.read() as s:
            member(s, get(s, Project, project_id), u.id, {"owner", "maintainer", "triager"})
            return [{"login": get(s, User, m.user_id).login, "role": m.role, "verified": m.verified}
                for m in s.scalars(select(Member).where(Member.project_id == project_id))]

    @app.put("/api/projects/{project_id}/members")
    def update_member(project_id: str, payload: MemberUpdate, u=Depends(user)):
        with database.transaction() as s:
            p = get(s, Project, project_id)
            actor = member(s, p, u.id, {"owner", "maintainer"})
            target = s.scalar(select(User).where(User.login == payload.login))
            if not target:
                fail(404, "That contributor must sign in once before they can be enrolled")
            if target.id == p.owner_id:
                fail(409, "The project owner role cannot be changed here")
            m = s.get(Member, (p.id, target.id))
            if actor.role != "owner" and (payload.role == "maintainer" or (m and m.role == "maintainer")):
                fail(403, "Only the project owner can grant or change maintainer access")
            if not m:
                m = Member(project_id=p.id, user_id=target.id)
                s.add(m)
            m.role, m.verified = payload.role, payload.verified
            s.flush()
            for lease in s.scalars(select(Lease).where(Lease.user_id == target.id, Lease.state.in_(ACTIVE))):
                w = get(s, Work, lease.work_id)
                if w.project_id == p.id and not coordinator.eligible(s, get(s, Device, lease.device_id), w):
                    coordinator.release(s, lease, terminal="cancelled")
                    status(s, w, "Contributor access changed; the old allocation is revoked.")
            audit(s, p.id, u.id, "member.updated", login=target.login, role=m.role, verified=m.verified)
            return {"ok": True}

    @app.post("/api/projects/{project_id}/sync")
    def sync(project_id: str, u=Depends(user)):
        with database.read() as s:
            p = get(s, Project, project_id)
            member(s, p, u.id, {"owner", "maintainer", "triager"})
            data = project_data(p)
        if cfg.demo:
            return {"imported": 0, "mode": "demo"}
        issues = gateway.issues(data)
        with database.transaction() as s:
            p = get(s, Project, project_id)
            for issue in issues:
                ingest_issue(s, p, issue, trusted_ready=False, action="import")
        return {"imported": len(issues)}

    @app.get("/api/projects/{project_id}/audit")
    def project_audit(project_id: str, u=Depends(user)):
        with database.read() as s:
            member(s, get(s, Project, project_id), u.id, {"owner", "maintainer", "triager"})
            events = list(s.scalars(select(Audit).where(Audit.project_id == project_id).order_by(Audit.created.desc()).limit(200)))
            jobs = list(s.scalars(select(Outbox).where(Outbox.project_id == project_id, Outbox.state != "done")))
            return {"audit": [{"action": e.action, "actor": e.actor, "detail": e.detail, "created": e.created} for e in events],
                "outbox": [{"id": j.id, "kind": j.kind, "state": j.state, "attempts": j.attempts, "error": j.error} for j in jobs]}

    @app.post("/api/outbox/{job_id}/retry")
    def retry_job(job_id: str, u=Depends(user)):
        with database.transaction() as s:
            j = get(s, Outbox, job_id)
            member(s, get(s, Project, j.project_id), u.id, {"owner", "maintainer"})
            if j.state not in {"failed", "pending"}:
                fail(409, "Only failed or pending deliveries can be retried")
            j.state, j.attempts, j.available = "pending", 0, time.time()
        return {"ok": True}

    @app.get("/api/work/{work_id}")
    def work_detail(work_id: str, u=Depends(user)):
        with database.read() as s:
            w = get(s, Work, work_id)
            p = get(s, Project, w.project_id)
            if not visible(s, p, u.id):
                fail(403, "Project permission required")
            history = []
            for l in s.scalars(select(Lease).where(Lease.work_id == w.id).order_by(Lease.created)):
                data = lease_data(s, l, detail=True)
                data["stale"] = coordinator.stale(l)
                data["receipt"] = public_receipt(l)
                history.append(data)
            return {"work": work_data(w), "project": project_data(p), "leases": history}

    @app.post("/api/work/{work_id}/validate")
    def validate_work(work_id: str, u=Depends(user)):
        with database.transaction() as s:
            w = get(s, Work, work_id)
            p = get(s, Project, w.project_id)
            member(s, p, u.id, {"owner", "maintainer", "triager"})
            if w.state != "validation_pending" or not p.active:
                fail(409, "Only pending work in an active project may be validated")
            w.state = "queued"
            audit(s, p.id, u.id, "work.validated", work_id=w.id)
            status(s, w, "A project reviewer validated this request. It is eligible for matching.")
            return work_data(w)

    @app.post("/api/devices")
    def create_device(payload: DeviceCreate, u=Depends(user)):
        if payload.harness == "mock" and not cfg.demo:
            fail(422, "Mock execution is allowed only in offline demo mode")
        raw = token("co4_device")
        with database.transaction() as s:
            d = Device(user_id=u.id, token_hash=digest(raw), **payload.model_dump())
            s.add(d); s.flush()
            return {"id": d.id, "token": raw, "name": d.name}

    @app.put("/api/devices/{device_id}")
    def toggle_device(device_id: str, payload: DeviceUpdate, u=Depends(user)):
        with database.transaction() as s:
            d = get(s, Device, device_id)
            if d.user_id != u.id:
                fail(403, "This is not your device")
            d.enabled = payload.enabled
            if not d.enabled:
                for l in s.scalars(select(Lease).where(Lease.device_id == d.id, Lease.state == "running")):
                    l.state, l.error = "blocked", "Device paused by contributor"
            return {"enabled": d.enabled}

    @app.delete("/api/devices/{device_id}")
    def revoke_device(device_id: str, u=Depends(user)):
        with database.transaction() as s:
            d = get(s, Device, device_id)
            if d.user_id != u.id:
                fail(403, "This is not your device")
            d.enabled, d.token_hash = False, digest(token("revoked"))
            for l in s.scalars(select(Lease).where(Lease.device_id == d.id, Lease.state.in_(ACTIVE))):
                coordinator.release(s, l, terminal="cancelled")
            return {"revoked": True}

    @app.post("/api/leases/{lease_id}/accept")
    def accept(lease_id: str, u=Depends(user)):
        with database.transaction() as s:
            l = get(s, Lease, lease_id)
            w = get(s, Work, l.work_id)
            if l.user_id != u.id:
                fail(403, "Only the contributor can accept this allocation")
            if l.created + 1800 <= time.time() or l.state != "offered" or not coordinator.eligible(s, get(s, Device, l.device_id), w):
                fail(409, "Offer is no longer available")
            l.state, l.last_contact = "running", time.time()
            audit(s, w.project_id, u.id, "allocation.accepted", lease_id=l.id)
            return {"state": l.state}

    @app.post("/api/leases/{lease_id}/deny")
    def deny(lease_id: str, u=Depends(user)):
        with database.transaction() as s:
            l = get(s, Lease, lease_id)
            w = get(s, Work, l.work_id)
            if l.user_id != u.id:
                fail(403, "Only the contributor can decline their allocation")
            if l.state not in ACTIVE - {"publishing"}:
                fail(409, "This allocation cannot be declined")
            coordinator.release(s, l)
            if not s.get(Decline, (u.id, w.id)):
                s.add(Decline(user_id=u.id, work_id=w.id))
            status(s, w, "The contributor declined or released this task. It will not be offered to them again.")
            return {"state": l.state}

    @app.post("/api/leases/{lease_id}/dib")
    def dib_work(lease_id: str, payload: Dib, u=Depends(user)):
        with database.transaction() as s:
            d = get(s, Device, payload.device_id)
            if d.user_id != u.id:
                fail(403, "Choose one of your own devices")
            l = get(s, Lease, lease_id)
            coordinator.dib(s, l, d)
            return {"recovery_deadline": l.dib_expires}

    @app.post("/api/leases/{lease_id}/recover")
    def recover_work(lease_id: str, u=Depends(user)):
        with database.transaction() as s:
            l = get(s, Lease, lease_id)
            coordinator.recover(s, l, u.id)
            return {"state": l.state}

    @app.post("/api/leases/{lease_id}/approve")
    def approve(lease_id: str, payload: Approval, u=Depends(user)):
        with database.transaction() as s:
            l = get(s, Lease, lease_id)
            coordinator.approve(s, l, u.id, payload)
            return {"state": l.state}

    @app.get("/api/leases/{lease_id}/events")
    def events(lease_id: str, after: int = 0, u=Depends(user)):
        with database.read() as s:
            l = get(s, Lease, lease_id)
            if l.user_id != u.id:
                fail(403, "Private execution traces are visible only to the contributing user")
            rows = list(s.scalars(select(Event).where(Event.lease_id == l.id, Event.sequence > after).order_by(Event.sequence).limit(200)))
            return [{"sequence": e.sequence, "kind": e.kind, "text": vault.open(e.payload), "created": e.created} for e in rows]

    @app.post("/api/worker/poll")
    def poll(payload: Poll, d=Depends(device)):
        with database.transaction() as s:
            d = get(s, Device, d.id)
            coordinator.sweep(s)
            l = coordinator.poll(s, d, payload.repositories)
            if not l:
                return {"lease": None, "enabled": d.enabled, "harness": d.harness}
            w = get(s, Work, l.work_id)
            return {"lease": lease_data(s, l, detail=True), "work": work_data(w),
                "project": project_data(get(s, Project, w.project_id)), "harness": d.harness, "enabled": d.enabled}

    @app.post("/api/worker/leases/{lease_id}/heartbeat")
    def heartbeat(lease_id: str, payload: Heartbeat, d=Depends(device)):
        with database.transaction() as s:
            d = get(s, Device, d.id)
            l, w = coordinator.fence(s, lease_id, d, payload.generation)
            phases = ["baseline", "spec", "red", "green", "verify", "ready"]
            if phases.index(payload.phase) not in {phases.index(l.phase), phases.index(l.phase) + 1}:
                fail(409, "Workflow phases must advance one step at a time")
            changed = l.phase != payload.phase
            l.phase, l.last_contact = payload.phase, time.time()
            if payload.usage:
                new = payload.usage.model_dump()
                for k in ("input_tokens", "output_tokens"):
                    old = (l.usage or {}).get(k)
                    if old is not None and (new[k] is None or new[k] < old):
                        fail(409, "Cumulative usage cannot decrease")
                l.usage = new
                used = (new["input_tokens"] or 0) + (new["output_tokens"] or 0)
                if used >= l.token_reservation:
                    l.state, l.error = "blocked", "Token reservation reached; stop execution"
            if changed:
                audit(s, w.project_id, d.user_id, "workflow.phase", lease_id=l.id, phase=l.phase)
                status(s, w, f"Current workflow stage: **{l.phase}**. Checkpoints are pushed by the contributor device; no PR exists before approval.")
            return {"state": l.state, "stop": l.state != "running"}

    @app.post("/api/worker/leases/{lease_id}/events")
    def event(lease_id: str, payload: WorkerEvent, d=Depends(device)):
        with database.transaction() as s:
            l, _ = coordinator.fence(s, lease_id, get(s, Device, d.id), payload.generation)
            existing = s.scalar(select(Event).where(Event.lease_id == l.id, Event.sequence == payload.sequence))
            redacted = redact(payload.text)
            if existing:
                if existing.kind != payload.kind or vault.open(existing.payload) != redacted:
                    fail(409, "Event sequence already exists with different content")
                return {"duplicate": True}
            s.add(Event(lease_id=l.id, sequence=payload.sequence, kind=payload.kind, payload=vault.seal(redacted)))
            return {"accepted": True}

    @app.post("/api/worker/leases/{lease_id}/checkpoint")
    def checkpoint(lease_id: str, payload: CheckpointRequest, d=Depends(device)):
        with database.read() as s:
            _, w = coordinator.fence(s, lease_id, get(s, Device, d.id), payload.generation)
            if payload.checkpoint.branch != f"co4/work/{w.number}/{lease_id}":
                fail(422, "Checkpoint branch does not match the allocated lease")
            p = project_data(get(s, Project, w.project_id))
        gateway.inspect_checkpoint(p, payload.checkpoint.model_dump())
        with database.transaction() as s:
            l, w = coordinator.fence(s, lease_id, get(s, Device, d.id), payload.generation)
            l.checkpoint, w.checkpoint = payload.checkpoint.model_dump(), payload.checkpoint.model_dump()
            l.last_contact = time.time()
            audit(s, w.project_id, d.user_id, "checkpoint.pushed", lease_id=l.id, sha=payload.checkpoint.sha)
            return {"accepted": True}

    @app.post("/api/worker/leases/{lease_id}/complete")
    def complete(lease_id: str, payload: Complete, d=Depends(device)):
        with database.read() as s:
            _, w = coordinator.fence(s, lease_id, get(s, Device, d.id), payload.generation)
            if payload.checkpoint.branch != f"co4/work/{w.number}/{lease_id}":
                fail(422, "Checkpoint branch does not match the allocated lease")
            p = project_data(get(s, Project, w.project_id))
        diff = gateway.inspect_checkpoint(p, payload.checkpoint.model_dump(), with_diff=True)
        if not cfg.demo and not diff.strip():
            fail(422, "GitHub reports no changes for this checkpoint")
        with database.transaction() as s:
            l, w = coordinator.fence(s, lease_id, get(s, Device, d.id), payload.generation)
            coordinator.complete(s, l, w, payload, diff if not cfg.demo else payload.diff)
            return {"state": l.state, "review_digest": l.review_digest}

    @app.post("/api/worker/leases/{lease_id}/blocked")
    def blocked(lease_id: str, payload: Failure, d=Depends(device)):
        with database.transaction() as s:
            l, w = coordinator.fence(s, lease_id, get(s, Device, d.id), payload.generation)
            l.state, l.error, l.last_contact = "blocked", redact(payload.reason), time.time()
            audit(s, w.project_id, d.user_id, "execution.blocked", lease_id=l.id)
            status(s, w, "Execution stopped and requires contributor attention. Private diagnostics are available in Co4. No PR was created.")
            return {"state": "blocked"}

    def ingest_issue(s, p, issue, trusted_ready, action):
        w = s.scalar(select(Work).where(Work.project_id == p.id, Work.number == issue["number"]))
        labels = [x["name"] if isinstance(x, dict) else x for x in issue.get("labels", [])]
        policy = Policy(**p.policy)
        changed = bool(w and (w.title != issue["title"] or w.body != (issue.get("body") or "")))
        if not w:
            w = Work(project_id=p.id, number=issue["number"], title=issue["title"][:1000], body=(issue.get("body") or "")[:100_000], labels=labels)
            s.add(w); s.flush()
        if changed and w.active_lease:
            coordinator.release(s, get(s, Lease, w.active_lease), terminal="cancelled", requeue=False)
        w.title, w.body, w.labels, w.updated = issue["title"][:1000], (issue.get("body") or "")[:100_000], labels, time.time()
        if issue.get("state") == "closed" or action == "deleted":
            if w.active_lease:
                coordinator.release(s, get(s, Lease, w.active_lease), terminal="cancelled", requeue=False)
            w.state = "closed"
        elif set(labels) & set(policy.excluded_labels):
            if w.active_lease:
                coordinator.release(s, get(s, Lease, w.active_lease), terminal="cancelled", requeue=False)
            w.state = "validation_pending"
        elif changed or w.state == "closed":
            w.state = "validation_pending"
        if w.state == "validation_pending" and p.active and not set(labels) & set(policy.excluded_labels):
            if not policy.require_validation or (trusted_ready and policy.ready_label in labels):
                w.state = "queued"
        audit(s, p.id, "github", "issue." + action, work_id=w.id)
        return w

    @app.post("/webhooks/github")
    async def webhook(request: Request):
        raw = await request.body()
        secret = cfg.webhook_secret
        if not secret:
            fail(503, "Webhook secret is not configured")
        signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, request.headers.get("x-hub-signature-256", "")):
            fail(401, "Webhook signature is invalid")
        delivery = request.headers.get("x-github-delivery", "")
        kind = request.headers.get("x-github-event", "")
        if not delivery or len(delivery) > 100:
            fail(400, "Webhook delivery ID is required")
        try:
            payload = json.loads(raw)
        except ValueError:
            fail(400, "Invalid JSON")
        with database.read() as s:
            if s.get(Delivery, delivery):
                return {"duplicate": True}
        installation = payload.get("installation", {}).get("id")
        repo = payload.get("repository", {})
        action = payload.get("action", "")
        trusted_ready = False
        with database.read() as s:
            p = s.scalar(select(Project).where(Project.repository_id == repo.get("id", -1), Project.installation_id == installation))
            if p:
                pd = project_data(p)
            else:
                pd = None
        if pd and kind == "issues" and action in {"opened", "labeled", "reopened"} and not cfg.demo:
            if Policy(**pd["policy"]).ready_label in [x["name"] for x in payload.get("issue", {}).get("labels", [])]:
                trusted_ready = await asyncio.to_thread(gateway.can_manage, installation, pd["repository_id"], pd["repository"], payload["sender"]["login"])
        with database.transaction() as s:
            if s.get(Delivery, delivery):
                return {"duplicate": True}
            s.add(Delivery(id=delivery))
            if kind == "installation" and action in {"deleted", "suspend"}:
                projects = list(s.scalars(select(Project).where(Project.installation_id == installation)))
            elif kind == "installation_repositories" and action == "removed":
                ids = [r["id"] for r in payload.get("repositories_removed", [])]
                projects = list(s.scalars(select(Project).where(Project.repository_id.in_(ids), Project.installation_id == installation)))
            else:
                projects = []
            for project in projects:
                project.active = False
                for w in s.scalars(select(Work).where(Work.project_id == project.id, Work.active_lease.is_not(None))):
                    coordinator.release(s, get(s, Lease, w.active_lease), terminal="cancelled", requeue=False)
                audit(s, project.id, "github", "installation.revoked")
            if not pd:
                return {"accepted": True, "matched": False}
            p = get(s, Project, pd["id"])
            if kind == "issues" and "pull_request" not in payload.get("issue", {}):
                issue = payload.get("issue", {})
                if not isinstance(issue.get("number"), int) or not isinstance(issue.get("title"), str):
                    fail(400, "Issue payload is incomplete")
                ingest_issue(s, p, issue, trusted_ready, action)
            elif kind == "issue_comment" and action == "created":
                text = payload.get("comment", {}).get("body", "").strip()
                if text.startswith("/co4 "):
                    identity = s.scalar(select(User).where(User.github_id == payload.get("sender", {}).get("id")))
                    w = s.scalar(select(Work).where(Work.project_id == p.id, Work.number == payload.get("issue", {}).get("number")))
                    if identity and w:
                        parts = text.split()
                        try:
                            if parts[1] == "validate":
                                member(s, p, identity.id, {"owner", "maintainer", "triager"})
                                if w.state == "validation_pending" and p.active:
                                    w.state = "queued"
                                    audit(s, p.id, identity.id, "work.validated", work_id=w.id)
                                    status(s, w, "Request validated from GitHub. It is eligible for matching.")
                            elif w.active_lease and parts[1] == "recover":
                                coordinator.recover(s, get(s, Lease, w.active_lease), identity.id)
                            elif w.active_lease and len(parts) == 4 and parts[1] == "approve":
                                coordinator.approve(s, get(s, Lease, w.active_lease), identity.id,
                                    Approval(sha=parts[2], review_digest=parts[3], confirm_reviewed=True))
                        except (HTTPException, ValueError):
                            audit(s, p.id, identity.id, "github_command.denied", work_id=w.id)
            elif kind == "pull_request":
                pr = payload.get("pull_request", {})
                branch = pr.get("head", {}).get("ref", "")
                for l in s.scalars(select(Lease).join(Work, Lease.work_id == Work.id).where(Work.project_id == p.id)):
                    if branch == f"co4/submission/{l.id}/{l.approved_sha[:12]}":
                        w = get(s, Work, l.work_id)
                        if action == "synchronize" and pr.get("head", {}).get("sha") != l.approved_sha:
                            l.error = "PR head changed after approval. Fresh review is required."
                            l.state, w.state = "review_invalidated", "review_invalidated"
                            audit(s, p.id, "github", "review.invalidated", lease_id=l.id)
                            status(s, w, "The PR head changed after approval. The previous execution receipt no longer attests to the PR head; a fresh review is required.")
                        elif action == "closed":
                            l.state = "merged" if pr.get("merged") else "closed"
                            w.state = l.state
                            audit(s, p.id, "github", "pull_request." + l.state, lease_id=l.id)
            return {"accepted": True}

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/demo/submissions/{lease_id}")
    def demo_submission(lease_id: str):
        if not cfg.demo:
            fail(404, "Not found")
        return RedirectResponse("/#work")

    return app
