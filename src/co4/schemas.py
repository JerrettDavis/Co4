from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Policy(Strict):
    access: Literal["open", "verified", "maintainers"] = "verified"
    require_validation: bool = True
    ready_label: str = "co4:ready"
    excluded_labels: list[str] = Field(default_factory=lambda: ["security", "co4:hold"])
    maintainer_labels: list[str] = Field(default_factory=lambda: ["breaking-change"])
    max_task_tokens: int = Field(default=100_000, ge=1000, le=10_000_000)
    require_maintainer_approval: bool = False
    test_profile: str = Field(default="default", pattern=r"^[A-Za-z0-9_-]{1,50}$")
    prompt_notes: str = Field(default="", max_length=10_000)
    strategy: Literal["priority_age", "fifo"] = "priority_age"

class EnrollProject(Strict):
    installation_id: int = Field(gt=0)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

class ProjectUpdate(Strict):
    policy: Policy
    budget_tokens: int = Field(ge=1000, le=2_000_000_000)
    active: bool = True

class MemberUpdate(Strict):
    login: str = Field(min_length=1, max_length=100)
    role: Literal["maintainer", "triager", "contributor", "suspended"] = "contributor"
    verified: bool = False

class DeviceCreate(Strict):
    name: str = Field(min_length=1, max_length=100)
    harness: Literal["claude", "codex", "copilot", "mock"]
    autonomy: Literal["approve_each", "automatic"] = "approve_each"
    max_tokens: int = Field(default=100_000, ge=1000, le=10_000_000)
    labels: list[str] = Field(default_factory=list, max_length=30)

class DeviceUpdate(Strict):
    enabled: bool

class Poll(Strict):
    repositories: list[str] = Field(max_length=100)

class Fence(Strict):
    generation: int = Field(ge=1)

class Usage(Strict):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    duration_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    complete: bool = False
    source: Literal["reported", "unavailable", "fixture"] = "unavailable"

class Heartbeat(Fence):
    phase: Literal["baseline", "spec", "red", "green", "verify", "ready"]
    usage: Usage | None = None

class WorkerEvent(Fence):
    sequence: int = Field(ge=1)
    kind: str = Field(pattern=r"^[a-z_]{1,50}$")
    text: str = Field(max_length=262144)

class Checkpoint(Strict):
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    branch: str = Field(pattern=r"^co4/work/[a-z0-9/-]{1,150}$")
    sha: str = Field(pattern=r"^[a-f0-9]{40}$")

class CheckpointRequest(Fence):
    checkpoint: Checkpoint

class Evidence(Strict):
    execution_mode: Literal["interactive", "noninteractive", "mixed"] = "noninteractive"
    interaction_capture: Literal["terminal_output", "structured_events", "mixed"] = "structured_events"
    baseline_exit: int = 0
    red_exit: int = 1
    green_exit: int = 0
    verify_exit: int = 0
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    behavior_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tests_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile: str = Field(min_length=1, max_length=50)
    baseline_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    red_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    green_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    notes: str = Field(default="Worker-reported evidence; independent CI remains required.", max_length=2000)

class Complete(Fence):
    checkpoint: Checkpoint
    evidence: Evidence
    usage: Usage
    summary: str = Field(min_length=1, max_length=20_000)
    diff: str = Field(min_length=1, max_length=2_000_000)

class Approval(Strict):
    sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    review_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_reviewed: Literal[True]

class Failure(Fence):
    reason: str = Field(min_length=1, max_length=2000)

class Dib(Strict):
    device_id: str

class Watch(Strict):
    enabled: bool = True
