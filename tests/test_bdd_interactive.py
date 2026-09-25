"""Executable steps for specs/interactive.feature.

Two terminal transports are used, and each scenario says which one it needs:

* Scenarios about the terminal itself (PTY input, resize, Ctrl+], termios restoration) use a
  real POSIX PTY via the ``terminal_pair`` fixture from test_interactive and are skipped on
  Windows, exactly like the unit-level PTY tests.
* Scenarios about governance and accounting (no silent fallback, no invented usage, upgrade
  defaults) replace only ``run_interactive_process`` with a simulated native CLI so they run on
  every platform; everything else (Git, real test commands, the control plane) is real.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys
import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from co4.adapters import invocation
from co4.models import Lease
from co4.process import ExecutionStopped, ProcessResult
from co4.worker import Worker, WorkerError, harness_environment, init_demo_source, load_config
from conftest import device_client, poll
from test_interactive import terminal_pair  # noqa: F401  (pytest fixture, skips off POSIX)

scenarios("interactive.feature")

FORBIDDEN_FLAGS = {"-p", "--print", "exec", "--json", "--output-format", "--no-ask-user"}
FAKE_USAGE = {"type": "result", "session_id": "tui-session", "usage": {"input_tokens": 999, "output_tokens": 999},
              "total_cost_usd": 99}


@pytest.fixture
def ctx():
    return {}


def _config(tmp_path, harness):
    source = tmp_path / "source"
    init_demo_source(source)
    return {"server": "http://testserver", "root": str(tmp_path / "worker"), "demo": True,
            "execution_mode": "interactive", "credential_policy": "native_login", "harnesses": [harness],
            "repositories": {"co4-demo/tiny-library": {"push_repository": "co4-demo/tiny-library",
                "local_source": str(source), "test_command": [sys.executable, "-m", "unittest", "discover", "-v"],
                "test_globs": ["test_*.py"], "test_profile": "default"}}}


def _enroll(app, clients, tmp_path, monkeypatch, ctx, harness):
    client, info = device_client(app, clients["contributor"], harness=harness)
    monkeypatch.setenv("CO4_DEVICE_TOKEN", info["token"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")  # ambient; must never be forwarded
    ctx.update(client=client, config=_config(tmp_path, harness), harness=harness, argvs=[], modes=[],
               confirmations=[])
    real_invocation = invocation
    def recording_invocation(name, prompt, model=None, *, execution_mode="noninteractive"):
        ctx["modes"].append(execution_mode)
        return real_invocation(name, prompt, model, execution_mode=execution_mode)
    monkeypatch.setattr("co4.worker.invocation", recording_invocation)
    def confirm(phase, **_):
        ctx["confirmations"].append(phase)
        return True
    monkeypatch.setattr("co4.worker.confirm_phase", confirm)


def _phase_work(cwd: Path, prompt: str):
    """What a contributor would do in the native CLI for one governed phase."""
    phase = re.search(r"^Phase: (\w+)", prompt, re.M).group(1)
    if phase == "spec":
        folder = cwd / re.search(r"Write (specs/co4/[^/]+)/spec.md", prompt).group(1)
        folder.mkdir(parents=True)
        (folder / "spec.md").write_text("# Average\nReturn zero for an empty input.\n")
        (folder / "behavior.feature").write_text("Feature: Average\n Scenario: Empty\n  Given no values\n  When averaged\n  Then return zero\n")
    elif phase == "red":
        with (cwd / "test_average.py").open("a") as f:
            f.write("    def test_empty(self):\n        self.assertEqual(average([]),0)\n")
    else:
        (cwd / "average.py").write_text("def average(values):\n    return sum(values)/len(values) if values else 0\n")
        folder = next((cwd / "specs" / "co4").iterdir())
        (folder / "summary.md").write_text("Handle an empty collection; simulated interactive fixture only.\n")
    return phase


def _simulated_native_cli(ctx, exit_code=0):
    """Stand-in for run_interactive_process: prints TUI text (including JSON-looking usage)."""
    def run(argv, cwd, *, on_output, on_start=lambda *_: None, on_tick=lambda: None, **_):
        ctx["argvs"].append(list(argv))
        on_start(4242)
        if exit_code:
            on_output("terminal", "claude: not signed in\r\n")
            return ProcessResult(exit_code, 0.1)
        relative = re.search(r"Read (\S+) and execute", argv[-1]).group(1)
        _phase_work(Path(cwd), (Path(cwd) / relative).read_text())
        on_output("terminal", "\x1b[1mWorking…\x1b[0m\r\n" + json.dumps(FAKE_USAGE) + "\r\n")
        on_tick()
        return ProcessResult(0, 0.5)
    return run


def _lease(clients, ctx):
    return clients["contributor"].get("/api/work/" + ctx["job"]["work"]["id"]).json()["leases"][-1]


# --- Scenario Outline: Perform a governed workflow using a native terminal (real PTY) -------

FIXTURE_CLI = '''import json, os, pathlib, re, sys, time
assert os.isatty(0) and os.isatty(1) and os.isatty(2)
assert not set(sys.argv) & {'-p','--print','exec','--output-format','--json','--no-ask-user'}
assert 'CI' not in os.environ and 'ANTHROPIC_API_KEY' not in os.environ
prompt_path = re.search(r'Read ([^ ]+) and execute', sys.argv[-1]).group(1)
prompt = pathlib.Path(prompt_path).read_text()
phase = re.search(r'^Phase: (\\w+)', prompt, re.M).group(1)
print('FIXTURE_READY_' + phase, flush=True)
assert input() == 'proceed'
for _ in range(200):
    if os.get_terminal_size(0).columns == 101:
        break
    time.sleep(.01)
else:
    raise SystemExit(3)
print('RESIZED_101', flush=True)
print(json.dumps({'type':'result','usage':{'input_tokens':999,'output_tokens':999},'total_cost_usd':99}))
if phase == 'spec':
    folder = pathlib.Path(re.search(r'Write (specs/co4/[^/]+)/spec.md', prompt).group(1)); folder.mkdir(parents=True)
    (folder/'spec.md').write_text('# Average\\nReturn zero for an empty input.\\n')
    (folder/'behavior.feature').write_text('Feature: Average\\n Scenario: Empty\\n  Given no values\\n  When averaged\\n  Then return zero\\n')
elif phase == 'red':
    with pathlib.Path('test_average.py').open('a') as f:
        f.write('    def test_empty(self):\\n        self.assertEqual(average([]),0)\\n')
else:
    pathlib.Path('average.py').write_text('def average(values):\\n    return sum(values)/len(values) if values else 0\\n')
    folder = next(pathlib.Path('specs/co4').iterdir())
    (folder/'summary.md').write_text('Handle an empty collection; offline interactive fixture only.\\n')
'''


def _install_cli(tmp_path, monkeypatch, name, body):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / name
    executable.write_text("#!" + sys.executable + "\n" + body)
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))


def _attach_pty(ctx, monkeypatch, terminal_pair, *, on_ready):
    import termios
    from co4.terminal import run_interactive_process
    master, slave = terminal_pair
    ctx["termios_before"] = termios.tcgetattr(slave)
    ctx["output"] = []
    def run_terminal(argv, cwd, **kwargs):
        ctx["argvs"].append(list(argv))
        original_output = kwargs["on_output"]
        observed, sent = [], []
        def relay(channel, text):
            observed.append(text)
            ctx["output"].append(text)
            original_output(channel, text)
            if "FIXTURE_READY_" in "".join(observed) and not sent:
                sent.append(True)
                on_ready(master, slave)
        kwargs["on_output"] = relay
        return run_interactive_process(argv, cwd, input_fd=slave, output_fd=slave, **kwargs)
    monkeypatch.setattr("co4.worker.run_interactive_process", run_terminal)
    monkeypatch.setattr("co4.worker.require_terminal", lambda: (slave, slave))


@given(parsers.parse("a contributor has opted into interactive execution for {harness}"))
def opted_in(app, clients, tmp_path, monkeypatch, ctx, terminal_pair, harness):
    _enroll(app, clients, tmp_path, monkeypatch, ctx, harness)
    _install_cli(tmp_path, monkeypatch, harness, FIXTURE_CLI)


@given("a real terminal is attached and native CLI sign-in is managed locally")
def real_terminal(ctx, monkeypatch, terminal_pair):
    import fcntl
    import struct
    import termios
    assert ctx["config"]["credential_policy"] == "native_login"
    def on_ready(master, slave):
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 101, 0, 0))
        os.write(master, b"proceed\n")
    _attach_pty(ctx, monkeypatch, terminal_pair, on_ready=on_ready)


@when("the contributor is allocated an eligible issue")
def allocated_and_run(ctx):
    ctx["job"] = poll(ctx["client"])
    assert ctx["job"]["lease"]["state"] == "running"
    ctx["worker"] = Worker(ctx["config"], client=ctx["client"])
    ctx["worker"].execute(ctx["job"])


@then("Co4 launches the native UI without print, exec, or JSON-output flags")
def native_ui(ctx):
    assert len(ctx["argvs"]) == 3
    for argv in ctx["argvs"]:
        assert argv[0] == ctx["harness"] and not set(argv) & FORBIDDEN_FLAGS
    assert set(ctx["modes"]) == {"interactive"}
    launches = ctx["worker"].state["launches"]
    assert all(launch["execution_mode"] == "interactive" and launch["exit_code"] == 0 and launch["pid"] for launch in launches)


@then("terminal input and resize events reach the child")
def input_and_resize(ctx):
    text = "".join(ctx["output"])
    # The fixture exits non-zero unless it read "proceed" and observed the 101-column resize.
    assert text.count("RESIZED_101") == 3


@then("heartbeats and checkpoints remain active")
def heartbeats_and_checkpoints(clients, ctx):
    lease = _lease(clients, ctx)
    assert lease["last_contact"] > lease["created"]
    assert lease["checkpoint"]["sha"] == ctx["worker"].state["green_commit"]
    events = clients["contributor"].get(f"/api/leases/{lease['id']}/events").json()
    assert sum(e["kind"] == "session_started" for e in events) == 3
    assert any(e["kind"] == "harness_terminal" for e in events)


@then("the contributor confirms each phase before its validation")
def confirms_each_phase(clients, ctx):
    assert ctx["confirmations"] == ["spec", "red", "green"]
    lease = _lease(clients, ctx)
    events = clients["contributor"].get(f"/api/leases/{lease['id']}/events").json()
    confirmations = [json.loads(e["text"]) for e in events if e["kind"] == "phase_confirmation"]
    assert [c["phase"] for c in confirmations] == ["spec", "red", "green"]
    assert not any(c["publication_approved"] for c in confirmations)


@then("the spec, behavior, red, green, and verify gates are enforced")
def gates_enforced(clients, ctx):
    lease = _lease(clients, ctx)
    evidence = lease["evidence"]
    assert (evidence["baseline_exit"], evidence["red_exit"], evidence["green_exit"], evidence["verify_exit"]) == (0, 1, 0, 0)
    assert evidence["spec_sha256"] and evidence["behavior_sha256"] and evidence["tests_sha256"]
    detail = clients["contributor"].get("/api/work/" + ctx["job"]["work"]["id"]).json()["leases"][-1]
    assert "behavior.feature" in detail["diff"] and "test_empty" in detail["diff"]


@then("final draft PR creation still requires exact-commit human review")
def still_requires_review(app, clients, ctx):
    lease = _lease(clients, ctx)
    assert lease["state"] == "awaiting_review" and lease["pr_url"] == ""
    app.state.dispatcher.tick()
    assert app.state.github.publications == []


# --- Scenario: Do not silently switch execution or billing modes (portable) ----------------

@given("interactive execution is selected")
def interactive_selected(app, clients, tmp_path, monkeypatch, ctx):
    _enroll(app, clients, tmp_path, monkeypatch, ctx, "claude")
    assert ctx["config"]["execution_mode"] == "interactive"


@when("no terminal is attached or the native invocation fails")
def no_terminal_or_failure(app, clients, monkeypatch, ctx):
    from co4.terminal import require_terminal
    # (1) No terminal: the worker must refuse before it polls for (and acquires) any work.
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setattr("co4.worker.require_terminal", lambda: require_terminal(read_fd, write_fd))
        worker = Worker(dict(ctx["config"]), client=ctx["client"])
        polled = []
        monkeypatch.setattr(worker, "api", lambda *args: polled.append(args))
        with pytest.raises(RuntimeError, match="terminal") as refused:
            worker.run(once=True)
        ctx["refusal"], ctx["polled_without_terminal"] = str(refused.value), polled
    finally:
        os.close(read_fd)
        os.close(write_fd)
    # (2) Native invocation fails: the harness exits non-zero after an allocation was running.
    monkeypatch.setattr("co4.worker.require_terminal", lambda: (0, 1))
    monkeypatch.setattr("co4.worker.run_interactive_process", _simulated_native_cli(ctx, exit_code=127))
    harness_processes = []
    import co4.worker as worker_module
    real_run_process = worker_module.run_process
    def watch_run_process(argv, *args, **kwargs):
        if argv and argv[0] == ctx["harness"]:
            harness_processes.append(argv)
        return real_run_process(argv, *args, **kwargs)
    monkeypatch.setattr("co4.worker.run_process", watch_run_process)
    ctx["job"] = poll(ctx["client"])
    ctx["worker"] = Worker(ctx["config"], client=ctx["client"])
    with pytest.raises(WorkerError, match="exited 127") as failed:
        ctx["worker"].execute(ctx["job"])
    ctx["failure"], ctx["noninteractive_harness_runs"] = str(failed.value), harness_processes


@then("Co4 refuses or blocks execution without retrying programmatically")
def refuses_without_fallback(app, ctx):
    assert "No" in ctx["refusal"] and "fallback" in ctx["refusal"]
    assert ctx["polled_without_terminal"] == []
    assert len(ctx["argvs"]) == 1                      # exactly one native attempt, no retry
    assert set(ctx["modes"]) == {"interactive"}         # never re-planned as print/exec mode
    assert ctx["noninteractive_harness_runs"] == []
    with app.state.db.read() as s:
        assert s.get(Lease, ctx["job"]["lease"]["id"]).state == "blocked"


@then("explicitly forwarded provider credentials require separate opt-in")
def credentials_need_opt_in(ctx):
    forwarded = {**ctx["config"], "harness_env": ["ANTHROPIC_API_KEY"]}
    with pytest.raises(WorkerError, match="credential_policy"):
        harness_environment(forwarded)
    assert "ANTHROPIC_API_KEY" not in harness_environment(ctx["config"])  # ambient key not inherited
    opted_in = harness_environment({**forwarded, "credential_policy": "explicit_credentials"})
    assert opted_in["ANTHROPIC_API_KEY"] == "not-a-real-key"


@then("existing local account billing remains unverified")
def billing_unverified(ctx):
    launch = ctx["worker"].state["launches"][0]
    assert launch["credential_policy"] == "native_login"
    assert launch["billing_source"] == "unverified"


# --- Scenario: Do not invent accounting from a terminal (portable) -------------------------

@given("a native session prints JSON-looking usage text")
def json_looking_usage(app, clients, tmp_path, monkeypatch, ctx):
    _enroll(app, clients, tmp_path, monkeypatch, ctx, "claude")
    monkeypatch.setattr("co4.worker.require_terminal", lambda: (0, 1))
    monkeypatch.setattr("co4.worker.run_interactive_process", _simulated_native_cli(ctx))


@when("Co4 prepares the review receipt")
def prepare_receipt(clients, ctx):
    ctx["job"] = poll(ctx["client"])
    ctx["worker"] = Worker(ctx["config"], client=ctx["client"])
    ctx["worker"].execute(ctx["job"])
    ctx["lease"] = _lease(clients, ctx)
    assert ctx["lease"]["state"] == "awaiting_review"
    events = clients["contributor"].get(f"/api/leases/{ctx['lease']['id']}/events").json()
    assert any('"total_cost_usd": 99' in e["text"] for e in events if e["kind"] == "harness_terminal")


@then("interactive capture is explicitly labeled terminal_output")
def capture_labeled(ctx):
    assert ctx["lease"]["evidence"]["interaction_capture"] == "terminal_output"
    assert ctx["lease"]["receipt"]["verification"]["interaction_capture"] == "terminal_output"
    assert ctx["lease"]["receipt"]["verification"]["execution_mode"] == "interactive"


@then("missing token and cost counters remain unknown and incomplete")
def counters_unknown(ctx):
    usage, receipt = ctx["lease"]["usage"], ctx["lease"]["receipt"]
    assert usage["input_tokens"] is None and usage["output_tokens"] is None and usage["cost_usd"] is None
    assert usage["complete"] is False and usage["source"] == "unavailable"
    assert receipt["input_tokens"] is None and receipt["reported_cost_usd"] is None
    assert receipt["usage_complete"] is False and receipt["usage_source"] == "unavailable"
    assert ctx["worker"].state["sessions"] == []  # TUI "session_id" text is not telemetry either


@then("private prompts and account details are not public receipt fields")
def receipt_is_private(ctx):
    receipt = ctx["lease"]["receipt"]
    text = json.dumps(receipt)
    for private in ("prompt", "credential_policy", "billing_source", "launches", "device_id", "user_id",
                    "Untrusted issue JSON", ".co4-private", "not-a-real-key", "tui-session"):
        assert private not in text, private
    assert ".co4-private" not in ctx["lease"]["diff"]


# --- Scenario: Stop and recover without losing governance (real PTY) -----------------------

STALLING_CLI = "import time\nprint('FIXTURE_READY_spec', flush=True)\ntime.sleep(30)\n"


@given("an interactive allocation is active")
def interactive_active(app, clients, tmp_path, monkeypatch, ctx, terminal_pair):
    _enroll(app, clients, tmp_path, monkeypatch, ctx, "claude")
    _install_cli(tmp_path, monkeypatch, "claude", STALLING_CLI)
    ctx["job"] = poll(ctx["client"])
    assert ctx["job"]["lease"]["state"] == "running"


@when("the contributor presses Ctrl+] or a watchdog/lease check stops execution")
def press_ctrl_bracket(ctx, monkeypatch, terminal_pair):
    _attach_pty(ctx, monkeypatch, terminal_pair, on_ready=lambda master, _slave: os.write(master, b"\x1d"))
    ctx["worker"] = Worker(ctx["config"], client=ctx["client"])
    with pytest.raises(ExecutionStopped, match="Ctrl"):
        ctx["worker"].execute(ctx["job"])


@then("the child process group is stopped and terminal settings restored")
def child_stopped(ctx, terminal_pair):
    import termios
    pid = ctx["worker"].state["launches"][-1]["pid"]
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)
    assert termios.tcgetattr(terminal_pair[1]) == ctx["termios_before"]
    assert ctx["worker"].state["launches"][-1]["status"] == "interrupted"


@then("private prompt cleanup is attempted")
def prompt_cleanup(ctx):
    assert not list((ctx["worker"].repository.path / ".co4-private").glob("*-prompt.md"))


@then("saved phase and checkpoint state remain recoverable")
def state_recoverable(clients, ctx):
    saved = json.loads(ctx["worker"].state_file.read_text())
    assert saved["blocked"] is True and saved["completed"] == ["baseline"] and saved["phase"] == "spec"
    assert saved["checkpoint"]["sha"]
    lease = _lease(clients, ctx)
    assert lease["state"] == "blocked"
    recovered = clients["contributor"].post(f"/api/leases/{lease['id']}/recover", json={})
    assert recovered.status_code == 200 and recovered.json()["state"] == "running"


@then("no PR is created")
def no_pr(app, clients, ctx):
    assert _lease(clients, ctx)["pr_url"] == ""
    app.state.dispatcher.tick()
    assert app.state.github.publications == []


# --- Scenario: Preserve existing configurations during upgrade (portable) ------------------

ALPHA1_CONFIG = '''server = "http://localhost:8080"
root = {root}
token_env = "CO4_DEVICE_TOKEN"
acknowledge_code_execution = true
allow_checkpoint_push = true
harnesses = ["claude"]
[repositories."co4-demo/tiny-library"]
push_repository = "co4-demo/tiny-library"
test_command = ["python", "-m", "unittest"]
test_globs = ["test_*.py"]
'''


@given("an alpha.1 worker configuration omits execution_mode")
def alpha1_config(tmp_path, monkeypatch, ctx):
    path = tmp_path / "worker.toml"
    path.write_text(ALPHA1_CONFIG.format(root=json.dumps(str(tmp_path / "root"))))
    assert "execution_mode" not in path.read_text()
    monkeypatch.setenv("CO4_DEVICE_TOKEN", "co4_device_placeholder")
    ctx["path"] = path


@when("the upgraded worker starts")
def upgraded_worker_starts(ctx):
    ctx["config"] = load_config(ctx["path"])
    ctx["worker"] = Worker(ctx["config"])


@then("noninteractive behavior is retained")
def noninteractive_retained(ctx):
    assert ctx["worker"].config["execution_mode"] == "noninteractive"
    assert harness_environment(ctx["config"])["CI"] == "1"
    plan = invocation("claude", "task", execution_mode=ctx["config"]["execution_mode"])
    assert plan.execution_mode == "noninteractive" and "-p" in plan.argv


@then("an explicit --interactive option can override it locally")
def interactive_flag_overrides(ctx, monkeypatch):
    from co4 import cli
    started = []
    class RecordingWorker:
        def __init__(self, config, client=None):
            started.append(config)
        def run(self, once=False):
            started.append({"once": once})
    monkeypatch.setattr("co4.worker.Worker", RecordingWorker)
    monkeypatch.setattr(sys, "argv", ["co4", "worker", "--config", str(ctx["path"]), "--interactive", "--once"])
    cli.main()
    assert started[0]["execution_mode"] == "interactive" and started[1] == {"once": True}
    assert "execution_mode" not in ctx["path"].read_text()  # the local file is not rewritten
