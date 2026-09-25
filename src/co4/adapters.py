from __future__ import annotations
from dataclasses import dataclass, field
import json
from typing import Any

HARNESS_NAMES = ("claude", "codex", "copilot")

@dataclass
class Invocation:
    argv: list[str]
    stdin: str | None
    execution_mode: str = "noninteractive"


def invocation(harness: str, prompt: str, model: str | None = None, *,
               execution_mode: str = "noninteractive") -> Invocation:
    if execution_mode not in {"interactive", "noninteractive"}:
        raise ValueError("Unsupported execution mode: " + execution_mode)
    if execution_mode == "interactive":
        if harness == "claude":
            args = ["claude", "--permission-mode", "default", "--tools", "Read,Write,Edit,Glob,Grep"]
        elif harness == "codex":
            args = ["codex", "--ask-for-approval", "on-request", "--sandbox", "workspace-write", "--no-alt-screen"]
        elif harness == "copilot":
            args = ["copilot", "--no-auto-update", "--no-custom-instructions", "--deny-tool", "shell"]
        else:
            raise ValueError("Unsupported harness: " + harness)
        if model:
            args += ["--model", model]
        # No print/JSON/stdin protocol flags: the child must remain the native TUI.
        args += ["--interactive", prompt] if harness == "copilot" else ["--", prompt]
        return Invocation(args, None, execution_mode="interactive")
    if harness == "claude":
        args = ["claude", "-p", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "dontAsk", "--allowedTools", "Read,Write,Edit,Glob,Grep"]
        if model:
            args += ["--model", model]
        return Invocation(args, prompt)
    if harness == "codex":
        args = ["codex", "--ask-for-approval", "never", "exec", "--sandbox", "workspace-write", "--json"]
        if model:
            args += ["--model", model]
        return Invocation(args + ["-"], prompt)
    if harness == "copilot":
        args = ["copilot", "--output-format", "json", "--no-ask-user", "--no-auto-update",
                "--no-custom-instructions", "--allow-tool", "write", "--deny-tool", "shell", "-p", prompt]
        if model:
            args += ["--model", model]
        return Invocation(args, None)
    raise ValueError("Unsupported harness: " + harness)

@dataclass
class UsageMeter:
    """One invocation. Never add repeated cumulative snapshots or invent unavailable counters."""
    harness: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cost_usd: float | None = None
    complete: bool = False
    session_ids: set[str] = field(default_factory=set)
    error: str | None = None
    _seen: set[str] = field(default_factory=set)

    def feed(self, text: str):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        kind = data.get("type", "")
        sid = data.get("session_id") or data.get("thread_id")
        if isinstance(sid, str):
            self.session_ids.add(sid)
        if kind in {"error", "turn.failed"} or data.get("is_error"):
            self.error = str(data.get("error") or data.get("result") or "Harness reported an error")[:1000]
        if self.harness == "claude" and kind == "result":
            u = data.get("usage") or {}
            if isinstance(u.get("input_tokens"), int) and isinstance(u.get("output_tokens"), int):
                cached = u.get("cache_read_input_tokens", 0)
                self.input_tokens = u["input_tokens"] + cached + u.get("cache_creation_input_tokens", 0)
                self.output_tokens, self.cached_input_tokens = u["output_tokens"], cached
                self.complete = not data.get("is_error", False)
            self.cost_usd = data.get("total_cost_usd")
        elif self.harness == "codex" and kind == "turn.completed":
            # codex exec has one terminal turn; repeated terminal events are cumulative snapshots.
            u = data.get("usage") or {}
            if isinstance(u.get("input_tokens"), int) and isinstance(u.get("output_tokens"), int):
                self.input_tokens = u["input_tokens"]
                self.output_tokens = u["output_tokens"]
                self.cached_input_tokens = u.get("cached_input_tokens")
                self.complete = True
        elif self.harness == "copilot":
            # CLI JSONL schemas evolve. Preserve every event, accept only explicit terminal counters.
            # Unknown Copilot telemetry remains null and reserves the conservative quota amount.
            if kind in {"session.usage", "session.shutdown", "result"}:
                u = data.get("usage") or (data.get("data") or {}).get("usage") or {}
                if isinstance(u.get("input_tokens"), int) and isinstance(u.get("output_tokens"), int):
                    self.input_tokens, self.output_tokens = u["input_tokens"], u["output_tokens"]
                    self.cached_input_tokens = u.get("cached_input_tokens")
                    self.cost_usd = u.get("cost_usd")
                    self.complete = kind in {"session.shutdown", "result"}

    def data(self, seconds: float = 0) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cached_input_tokens": self.cached_input_tokens, "cost_usd": self.cost_usd,
                "duration_seconds": max(0, seconds), "complete": self.complete,
                "source": "reported" if self.input_tokens is not None else "unavailable"}


def aggregate(usages: list[dict], seconds: float, fixture=False) -> dict:
    def total(key):
        values = [u.get(key) for u in usages]
        return sum(v for v in values if v is not None) if any(v is not None for v in values) else None
    return {"input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
        "cached_input_tokens": total("cached_input_tokens"), "cost_usd": total("cost_usd") if usages and all(u.get("cost_usd") is not None for u in usages) else None,
        "duration_seconds": max(0, seconds), "complete": bool(usages) and all(u.get("complete") for u in usages),
        "source": "fixture" if fixture else ("reported" if total("input_tokens") is not None else "unavailable")}
