"""Real terminal transport tests use local processes, never authenticated model calls."""
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest
from co4.adapters import invocation
from co4.worker import WorkerError, harness_environment, load_config


@pytest.mark.parametrize('name', ['claude', 'codex', 'copilot'])
def test_interactive_adapter_is_a_native_tui_not_print_mode(name):
    plan = invocation(name, 'Read the governed task', execution_mode='interactive')
    assert plan.execution_mode == 'interactive'
    assert plan.stdin is None
    assert plan.argv[0] == name
    assert not set(plan.argv) & {'-p', '--print', 'exec', '--json', '--output-format', '--no-ask-user'}
    assert 'Read the governed task' in plan.argv
    assert not any('bypass' in arg or 'danger-full-access' in arg or '--allow-all' in arg for arg in plan.argv)
    if name == 'claude':
        assert plan.argv[plan.argv.index('--permission-mode') + 1] == 'default'
        assert '--tools' in plan.argv
    elif name == 'codex':
        assert 'on-request' in plan.argv and 'workspace-write' in plan.argv
    else:
        assert '--interactive' in plan.argv and '--deny-tool' in plan.argv


def test_invalid_execution_mode_is_rejected():
    with pytest.raises(ValueError, match='execution mode'):
        invocation('claude', 'hello', execution_mode='typo')


@pytest.mark.parametrize('name', ['claude', 'codex', 'copilot'])
def test_noninteractive_remains_explicitly_available(name):
    plan = invocation(name, 'hello', execution_mode='noninteractive')
    assert plan.execution_mode == 'noninteractive'
    assert ('exec' in plan.argv) if name == 'codex' else ('-p' in plan.argv)


def test_interactive_environment_keeps_terminal_and_not_ci(monkeypatch):
    monkeypatch.setenv('CI', 'true')
    monkeypatch.setenv('NO_COLOR', '1')
    monkeypatch.setenv('TERM', 'xterm-256color')
    env = harness_environment({'execution_mode': 'interactive', 'harness_env': ['CI', 'NO_COLOR']})
    assert env['TERM'] == 'xterm-256color'
    assert 'CI' not in env and 'NO_COLOR' not in env
    assert env['GIT_TERMINAL_PROMPT'] == '0'


def test_native_login_refuses_explicit_provider_credentials(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'not-a-real-key')
    with pytest.raises(WorkerError, match='credential_policy'):
        harness_environment({'credential_policy': 'native_login', 'harness_env': ['ANTHROPIC_API_KEY']})


def test_explicit_provider_credentials_require_opt_in(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'not-a-real-key')
    env = harness_environment({'credential_policy': 'explicit_credentials', 'harness_env': ['ANTHROPIC_API_KEY']})
    assert env['ANTHROPIC_API_KEY'] == 'not-a-real-key'


def test_ambient_api_credentials_are_not_inherited(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'not-a-real-key')
    monkeypatch.setenv('OPENAI_API_KEY', 'not-a-real-key')
    env = harness_environment({'execution_mode': 'interactive'})
    assert 'ANTHROPIC_API_KEY' not in env and 'OPENAI_API_KEY' not in env


def test_interactive_requires_a_terminal_not_a_silent_fallback():
    from co4.terminal import require_terminal
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match='terminal'):
            require_terminal(read_fd, write_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.fixture
def terminal_pair():
    if os.name != 'posix':
        pytest.skip('PTY runner is POSIX; use WSL on Windows')
    import pty
    master, slave = pty.openpty()
    try:
        yield master, slave
    finally:
        os.close(master)
        os.close(slave)


def test_real_pty_input_output_controlling_tty_and_restoration(tmp_path, terminal_pair):
    import termios
    from co4.terminal import run_interactive_process
    master, slave = terminal_pair
    before = termios.tcgetattr(slave)
    lines = []
    sent = False
    def output(channel, text):
        nonlocal sent
        lines.append((channel, text))
        if 'READY' in ''.join(t for _, t in lines) and not sent:
            os.write(master, b'hello from contributor\n')
            sent = True
    code = '''import os, sys
assert os.isatty(0) and os.isatty(1) and os.isatty(2)
f = os.open('/dev/tty', os.O_RDWR); os.close(f)
print('READY', flush=True)
text=input()
print('RECEIVED=' + text, flush=True)
sys.stdout.write('tail without newline'); sys.stdout.flush()
'''
    result = run_interactive_process([sys.executable, '-c', code], tmp_path,
        input_fd=slave, output_fd=slave, on_output=output, timeout_seconds=5, idle_seconds=3,
        tick_seconds=.05)
    assert result.exit_code == 0
    assert 'RECEIVED=hello from contributor' in ''.join(t for _, t in lines)
    assert 'tail without newline' in ''.join(t for _, t in lines)
    assert {c for c, _ in lines} == {'terminal'}
    assert termios.tcgetattr(slave) == before


def test_real_pty_idle_watchdog_and_terminal_cleanup(tmp_path, terminal_pair):
    import termios
    from co4.terminal import run_interactive_process
    from co4.process import ExecutionStopped
    _, slave = terminal_pair
    before = termios.tcgetattr(slave)
    ticks = []
    with pytest.raises(ExecutionStopped, match='idle watchdog'):
        run_interactive_process([sys.executable, '-c', 'import time;time.sleep(20)'], tmp_path,
            input_fd=slave, output_fd=slave, on_tick=lambda: ticks.append(1),
            idle_seconds=.3, timeout_seconds=2, tick_seconds=.05)
    assert ticks and termios.tcgetattr(slave) == before


def test_real_pty_heartbeat_failure_cancels_child(tmp_path, terminal_pair):
    from co4.terminal import run_interactive_process
    _, slave = terminal_pair
    def lost_lease():
        raise WorkerError('lease fenced')
    with pytest.raises(WorkerError, match='lease fenced'):
        run_interactive_process([sys.executable, '-c', 'import time;time.sleep(20)'], tmp_path,
            input_fd=slave, output_fd=slave, on_tick=lost_lease, tick_seconds=.05)


def test_ctrl_bracket_stops_the_entire_allocation(tmp_path, terminal_pair):
    from co4.terminal import run_interactive_process
    from co4.process import ExecutionStopped
    master, slave = terminal_pair
    def output(_, text):
        if 'READY' in text:
            os.write(master, b'\x1d')
    with pytest.raises(ExecutionStopped, match='contributor'):
        run_interactive_process([sys.executable, '-c', 'import time;print("READY",flush=True);time.sleep(20)'],
            tmp_path, input_fd=slave, output_fd=slave, on_output=output, timeout_seconds=3)


@pytest.mark.parametrize('field,value', [
    ('execution_mode', 'headless-ish'), ('credential_policy', 'free_forever'),
    ('interactive_idle_seconds', 0), ('harness_env', 'ANTHROPIC_API_KEY')])
def test_invalid_worker_execution_settings_are_rejected(tmp_path, field, value):
    from co4.worker import validate_execution_settings
    with pytest.raises(WorkerError):
        validate_execution_settings({field: value})


def test_noninteractive_environment_remains_noninteractive(monkeypatch):
    assert harness_environment({})['CI'] == '1'
    assert harness_environment({})['NO_COLOR'] == '1'


def test_interactive_worker_checks_terminal_before_polling(app, clients, tmp_path, monkeypatch):
    from test_worker import configured
    worker, client = configured(app, clients, tmp_path, monkeypatch)
    worker.config['execution_mode'] = 'interactive'
    def fail_terminal():
        raise RuntimeError('attached terminal required')
    monkeypatch.setattr('co4.worker.require_terminal', fail_terminal)
    monkeypatch.setattr(worker, 'api', lambda *_: pytest.fail('Must not allocate before checking terminal'))
    with pytest.raises(RuntimeError, match='terminal'):
        worker.run(once=True)


@pytest.mark.parametrize('harness,finish', [('claude',True), ('codex',True), ('copilot',True), ('claude',False)])
def test_interactive_worker_full_workflow_using_real_pty_fixture(app, clients, tmp_path, monkeypatch, terminal_pair, harness, finish):
    """Exercise actual native-mode argv + PTY + Git + tests + review; no paid CLI calls."""
    from conftest import device_client, poll
    from co4.worker import Worker, init_demo_source
    from co4.terminal import run_interactive_process
    master, slave = terminal_pair
    client, info = device_client(app, clients['contributor'], harness=harness)
    source = tmp_path / 'source'
    init_demo_source(source)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    executable = bin_dir / harness
    executable.write_text('#!' + sys.executable + '\n' + '''import json, os, pathlib, re, sys
assert os.isatty(0) and os.isatty(1) and os.isatty(2)
assert not set(sys.argv) & {'-p','exec','--output-format','--json','--no-ask-user'}
assert 'CI' not in os.environ
prompt_path = re.search(r'Read ([^ ]+) and execute', sys.argv[-1]).group(1)
prompt = pathlib.Path(prompt_path).read_text()
phase = re.search(r'^Phase: (\\w+)', prompt, re.M).group(1)
print('FIXTURE_READY_' + phase, flush=True)
assert input() == 'proceed'
# A JSON-looking TUI message MUST NOT become provider telemetry.
print(json.dumps({'type':'result','usage':{'input_tokens':999,'output_tokens':999},'total_cost_usd':99}))
if phase == 'spec':
    prefix = re.search(r'Write (specs/co4/[^/]+)/spec.md', prompt).group(1)
    folder = pathlib.Path(prefix); folder.mkdir(parents=True)
    (folder/'spec.md').write_text('# Average\\nReturn zero for an empty input.\\n')
    (folder/'behavior.feature').write_text('Feature: Average\\n Scenario: Empty\\n  Given no values\\n  When averaged\\n  Then return zero\\n')
elif phase == 'red':
    with pathlib.Path('test_average.py').open('a') as f:
        f.write('    def test_empty(self):\\n        self.assertEqual(average([]),0)\\n')
else:
    pathlib.Path('average.py').write_text('def average(values):\\n    return sum(values)/len(values) if values else 0\\n')
    folder = next(pathlib.Path('specs/co4').iterdir())
    (folder/'summary.md').write_text('Handle an empty collection; offline interactive fixture only.\\n')
''')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir) + os.pathsep + os.environ.get('PATH', ''))
    monkeypatch.setenv('CO4_DEVICE_TOKEN', info['token'])
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'not-a-real-key')
    config = {'server':'http://testserver','root':str(tmp_path/'worker'), 'demo':True,
        'execution_mode':'interactive','credential_policy':'native_login','harnesses':[harness],
        'repositories':{'co4-demo/tiny-library':{'push_repository':'co4-demo/tiny-library',
            'local_source':str(source),'test_command':[sys.executable,'-m','unittest','discover','-v'],
            'test_globs':['test_*.py'],'test_profile':'default'}}}
    confirmations = []
    def run_terminal(argv, cwd, **kwargs):
        original_output = kwargs['on_output']
        observed = []
        sent = False
        def relay(channel, text):
            nonlocal sent
            observed.append(text)
            original_output(channel, text)
            if 'FIXTURE_READY_' in ''.join(observed) and not sent:
                os.write(master, b'proceed\n')
                sent = True
        kwargs['on_output'] = relay
        return run_interactive_process(argv, cwd, input_fd=slave, output_fd=slave, **kwargs)
    monkeypatch.setattr('co4.worker.run_interactive_process', run_terminal)
    monkeypatch.setattr('co4.worker.require_terminal', lambda: (slave, slave))
    monkeypatch.setattr('co4.worker.confirm_phase', lambda phase, **_: confirmations.append(phase) or finish)
    worker = Worker(config, client=client)
    job = poll(client)
    if not finish:
        from co4.process import ExecutionStopped
        with pytest.raises(ExecutionStopped, match='Contributor paused'):
            worker.execute(job)
        lease = clients['contributor'].get('/api/work/' + job['work']['id']).json()['leases'][-1]
        assert lease['state'] == 'blocked' and not lease['pr_url']
        assert not list((worker.repository.path/'.co4-private').glob('*-prompt.md'))
        assert worker.state['completed'] == ['baseline']
        client.close()
        return
    worker.execute(job)
    lease = clients['contributor'].get('/api/work/' + job['work']['id']).json()['leases'][-1]
    assert lease['state'] == 'awaiting_review' and not lease['pr_url']
    assert confirmations == ['spec', 'red', 'green']
    assert lease['usage']['source'] == 'unavailable'
    assert lease['usage']['input_tokens'] is None and lease['usage']['cost_usd'] is None
    assert not lease['usage']['complete']
    assert lease['evidence']['execution_mode'] == 'interactive'
    assert lease['evidence']['interaction_capture'] == 'terminal_output'
    assert len(worker.state['launches']) == 3
    assert all(launch['exit_code'] == 0 and launch['pid'] for launch in worker.state['launches'])
    assert not list((worker.repository.path/'.co4-private').glob('*-prompt.md'))
    assert '.co4-private' not in lease['diff']
    events = clients['contributor'].get(f"/api/leases/{lease['id']}/events").json()
    assert sum(e['kind'] == 'prompt' for e in events) == 3
    assert any(e['kind'] == 'harness_terminal' for e in events)
    assert all('not-a-real-key' not in e['text'] for e in events)
    app.state.dispatcher.tick()
    assert not app.state.github.publications
    client.close()


def test_real_pty_window_size_is_forwarded(tmp_path, terminal_pair):
    import fcntl
    import struct
    import termios
    from co4.terminal import run_interactive_process
    master, slave = terminal_pair
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 31, 93, 0, 0))
    chunks = []
    def output(_, text):
        chunks.append(text)
        if 'READY' in text:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 42, 105, 0, 0))
    code = '''import os,time
assert os.get_terminal_size(0).columns == 93
print('READY',flush=True)
for _ in range(200):
 if os.get_terminal_size(0).columns==105:
  print('RESIZED',flush=True);break
 time.sleep(.01)
else:raise SystemExit(2)
'''
    result = run_interactive_process([sys.executable,'-c',code], tmp_path,
        input_fd=slave, output_fd=slave, on_output=output, timeout_seconds=4)
    assert result.exit_code == 0 and 'RESIZED' in ''.join(chunks)


def test_interactive_cli_help_exposes_both_modes():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable,'-m','co4','worker','--help'],
        env={**os.environ,'PYTHONPATH':str(root/'src')}, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert '--interactive' in result.stdout and '--noninteractive' in result.stdout


@pytest.mark.parametrize('answer,expected', [('continue',True), ('stop',False)])
def test_actual_terminal_phase_confirmation(terminal_pair, monkeypatch, answer, expected):
    from co4.terminal import confirm_phase
    master, slave = terminal_pair
    monkeypatch.setattr('co4.terminal.require_terminal', lambda: (slave, slave))
    sender = threading.Timer(.1, lambda: os.write(master, (answer + '\n').encode()))
    sender.start()
    try:
        assert confirm_phase('spec', timeout_seconds=2) is expected
    finally:
        sender.join()


def test_real_pty_absolute_watchdog_cannot_be_defeated_by_screen_activity(tmp_path, terminal_pair):
    from co4.terminal import run_interactive_process
    from co4.process import ExecutionStopped
    _, slave = terminal_pair
    code = 'import time\nwhile True:\n print("working",flush=True);time.sleep(.05)'
    with pytest.raises(ExecutionStopped, match='Absolute interactive'):
        run_interactive_process([sys.executable,'-c',code], tmp_path, input_fd=slave, output_fd=slave,
                                timeout_seconds=.3, idle_seconds=2)


@pytest.mark.skipif(sys.platform == "win32", reason="NTFS reserves ':' in filenames; run in WSL")
def test_private_prompt_directory_never_enters_checkpoint(tmp_path):
    from co4.worker import init_demo_source
    from co4.gitops import Repository, GitError
    source = tmp_path/'source'; init_demo_source(source)
    repo = Repository(tmp_path/'checkout', demo=True)
    repo.prepare('co4-demo/tiny-library','co4-demo/tiny-library','co4/work/1/test','main',local_source=str(source))
    private = repo.path/'.co4-private'; private.mkdir()
    (private/'task.md').write_text('private prompt not for a commit')
    (repo.path/':(exclude)literal.txt').write_text('ordinary tracked content')
    before = repo.sha()
    repo.checkpoint('co4/work/1/test','checkpoint','co4-demo/tiny-library')
    diff = repo.diff(before)
    assert 'private prompt' not in diff and 'ordinary tracked content' in diff
    repo.command('add','-f','--','.co4-private/task.md')
    with pytest.raises(GitError, match='Private coordinator'):
        repo.checkpoint('co4/work/1/test','must stop','co4-demo/tiny-library')


def test_undrained_terminal_cannot_block_supervision_forever(tmp_path, terminal_pair):
    import termios
    from co4.terminal import run_interactive_process
    from co4.process import ExecutionStopped
    _, slave = terminal_pair
    saved = termios.tcgetattr(slave)
    before = time.monotonic()
    with pytest.raises(ExecutionStopped, match='backpressure'):
        run_interactive_process([sys.executable, '-S', '-c', 'import os;os.write(1,b"x"*1000000)'],
            tmp_path, input_fd=slave, output_fd=slave, timeout_seconds=5, idle_seconds=5)
    assert time.monotonic() - before < 5
    assert os.get_blocking(slave) and termios.tcgetattr(slave) == saved
