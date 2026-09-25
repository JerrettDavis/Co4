from __future__ import annotations
import time
import uuid
from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def uid() -> str:
    return uuid.uuid4().hex

class Base(DeclarativeBase):
    pass

class Mutex(Base):
    __tablename__ = "mutex"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(default=0)

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    github_id: Mapped[int] = mapped_column(unique=True)
    login: Mapped[str] = mapped_column(String(100), unique=True)
    created: Mapped[float] = mapped_column(default=time.time)

class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    github_token: Mapped[str] = mapped_column(Text, default="")
    expires: Mapped[float] = mapped_column()

class OAuthState(Base):
    __tablename__ = "oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires: Mapped[float] = mapped_column()

class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    repository: Mapped[str] = mapped_column(String(250), unique=True)
    repository_id: Mapped[int] = mapped_column(unique=True)
    installation_id: Mapped[int] = mapped_column()
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    budget_tokens: Mapped[int] = mapped_column(default=10_000_000)
    spent_tokens: Mapped[int] = mapped_column(default=0)
    reserved_tokens: Mapped[int] = mapped_column(default=0)
    created: Mapped[float] = mapped_column(default=time.time)

class Member(Base):
    __tablename__ = "members"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(30), default="contributor")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    watching: Mapped[bool] = mapped_column(Boolean, default=True)
    quality: Mapped[int] = mapped_column(default=0)

class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    harness: Mapped[str] = mapped_column(String(30))
    autonomy: Mapped[str] = mapped_column(String(30), default="approve_each")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_tokens: Mapped[int] = mapped_column(default=100_000)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    last_seen: Mapped[float] = mapped_column(default=0)

class Work(Base):
    __tablename__ = "work"
    __table_args__ = (UniqueConstraint("project_id", "number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    number: Mapped[int] = mapped_column()
    title: Mapped[str] = mapped_column(String(1000))
    body: Mapped[str] = mapped_column(Text, default="")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(default=0)
    state: Mapped[str] = mapped_column(String(40), default="validation_pending")
    generation: Mapped[int] = mapped_column(default=0)
    active_lease: Mapped[str | None] = mapped_column(String(32), nullable=True)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[float] = mapped_column(default=time.time)
    updated: Mapped[float] = mapped_column(default=time.time)

class Lease(Base):
    __tablename__ = "leases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    work_id: Mapped[str] = mapped_column(ForeignKey("work.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    generation: Mapped[int] = mapped_column()
    state: Mapped[str] = mapped_column(String(40), default="offered")
    phase: Mapped[str] = mapped_column(String(40), default="baseline")
    last_contact: Mapped[float] = mapped_column(default=time.time)
    created: Mapped[float] = mapped_column(default=time.time)
    token_reservation: Mapped[int] = mapped_column()
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    diff: Mapped[str] = mapped_column(Text, default="")
    approved_sha: Mapped[str] = mapped_column(String(64), default="")
    maintainer_sha: Mapped[str] = mapped_column(String(64), default="")
    review_digest: Mapped[str] = mapped_column(String(64), default="")
    dib_user: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dib_device: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dib_expires: Mapped[float | None] = mapped_column(Float, nullable=True)
    pr_url: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(Text, default="")

class Decline(Base):
    __tablename__ = "declines"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("work.id"), primary_key=True)

class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("lease_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    lease_id: Mapped[str] = mapped_column(ForeignKey("leases.id"), index=True)
    sequence: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[str] = mapped_column(Text)
    created: Mapped[float] = mapped_column(default=time.time)

class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[float] = mapped_column(default=time.time)

class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    created: Mapped[float] = mapped_column(default=time.time)

class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    available: Mapped[float] = mapped_column(default=time.time)
    locked_until: Mapped[float] = mapped_column(default=0)
    lock_token: Mapped[str] = mapped_column(String(32), default="")
    error: Mapped[str] = mapped_column(Text, default="")

class DeviceFlow(Base):
    __tablename__ = "device_flow"
    device_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_code: Mapped[str] = mapped_column(String(20), index=True)
    scope: Mapped[str] = mapped_column(String(500), default="")
    expires_at: Mapped[float] = mapped_column()
    interval: Mapped[int] = mapped_column(default=5)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=True)
    completed_at: Mapped[float] = mapped_column(nullable=True)
    created_at: Mapped[float] = mapped_column(default=time.time)
