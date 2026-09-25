"""Validate the installed native-terminal transport outside the source checkout.

Uses a local Python child, not an authenticated coding provider or model request.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

CHECK = r'''
import json, os, pathlib, pty, sys, termios
import co4
from co4.adapters import invocation
from co4.terminal import run_interactive_process, require_terminal
assert "site-packages" in str(pathlib.Path(co4.__file__).resolve()), co4.__file__
assert co4.__version__ == "0.1.0a2"
assert pathlib.Path(co4.__file__).with_name("_pty_child.py").is_file()
for name in ("claude", "codex", "copilot"):
    plan = invocation(name, "Read the governed task", execution_mode="interactive")
    assert not set(plan.argv) & {"-p", "exec", "--json", "--output-format", "--no-ask-user"}
master, slave = pty.openpty()
saved = termios.tcgetattr(slave)
seen = []
sent = False
def output(channel, text):
    global sent
    seen.append(text)
    if "READY" in "".join(seen) and not sent:
        os.write(master, b"contributor input\n")
        sent = True
try:
    child = "import os; assert all(os.isatty(i) for i in (0,1,2)); f=os.open('/dev/tty',os.O_RDWR);os.close(f); print('READY',flush=True); print('RECEIVED='+input(),flush=True)"
    result = run_interactive_process([sys.executable, "-c", child], os.getcwd(),
        input_fd=slave, output_fd=slave, on_output=output, timeout_seconds=5, idle_seconds=3)
    assert result.exit_code == 0
    assert "RECEIVED=contributor input" in "".join(seen)
    assert termios.tcgetattr(slave) == saved and os.get_blocking(slave)
finally:
    os.close(master); os.close(slave)
r,w = os.pipe()
try:
    try: require_terminal(r,w)
    except RuntimeError: pass
    else: raise AssertionError("Piped input must not trigger unattended fallback")
finally:
    os.close(r); os.close(w)
print(json.dumps({"status":"passed", "installed_package":"co4-orchestrator 0.1.0a2",
    "package_location":str(pathlib.Path(co4.__file__).resolve()), "mode":"installed Linux PTY with deterministic local child",
    "checks":["imports installed wheel outside checkout", "PTY exec helper is packaged",
        "all three native argv contracts", "real controlling terminal and keyboard round trip",
        "terminal settings/blocking mode restored", "piped input rejected without fallback"],
    "live_providers":False}, indent=2))
'''

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True)
    parser.add_argument('--output', default='docs/installed-terminal-test-report.json')
    args = parser.parse_args()
    python = str(Path(args.python).absolute())
    env = {k:v for k,v in os.environ.items() if k != 'PYTHONPATH' and not k.startswith(('CO4_', 'GITHUB_APP_', 'GITHUB_CLIENT_'))}
    with tempfile.TemporaryDirectory(prefix='co4-installed-terminal-') as tmp:
        result = subprocess.run([python, '-c', CHECK], cwd=tmp, env=env,
            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    report = json.loads(result.stdout)
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
