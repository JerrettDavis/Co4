"""Foreground native-terminal supervision, not a screen-scraping agent harness.

Input goes to the official CLI unchanged except Ctrl+] (stop allocation). We never
synthesize permission approvals, parse TUI text as accounting, or switch to print
mode. Raw keystrokes are not logged: they may contain passwords or login codes.
"""
from __future__ import annotations
import codecs
import errno
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
from typing import Callable
from co4.process import ExecutionStopped, ProcessResult


def require_terminal(input_fd: int | None = None, output_fd: int | None = None) -> tuple[int, int]:
    if os.name != 'posix':
        raise RuntimeError('Interactive mode requires a POSIX terminal. On Windows, run the worker and harness in WSL. No noninteractive fallback is performed.')
    try:
        input_fd = sys.stdin.fileno() if input_fd is None else input_fd
        output_fd = sys.stdout.fileno() if output_fd is None else output_fd
        if not os.isatty(input_fd) or not os.isatty(output_fd):
            raise ValueError('not a tty')
    except (ValueError, AttributeError, OSError) as error:
        raise RuntimeError('Interactive mode requires an attached terminal on stdin and stdout. Use a terminal, ssh -t, or tmux. No print-mode fallback is performed.') from error
    return input_fd, output_fd


def _write(fd: int, data: bytes, *, timeout_seconds: float = 2):
    # Both the local terminal and remote child can stop draining their buffers.
    # Never let a stalled display/input queue defeat lease cancellation/watchdogs.
    view = memoryview(data)
    deadline = time.monotonic() + timeout_seconds
    while view:
        if time.monotonic() >= deadline:
            raise ExecutionStopped('Interactive terminal stopped accepting data (backpressure)')
        try:
            count = os.write(fd, view)
            view = view[count:]
        except InterruptedError:
            continue
        except BlockingIOError:
            select.select([], [fd], [], .2)


def _stop_group(process: subprocess.Popen):
    # Kill the group even if its leader has exited but left children holding the PTY.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()


def run_interactive_process(argv: list[str], cwd, *, env: dict | None = None,
        on_output: Callable[[str, str], None] = lambda *_: None,
        on_tick: Callable[[], None] = lambda: None,
        on_start: Callable[[int], None] = lambda *_: None,
        timeout_seconds: float = 3600, idle_seconds: float = 1800,
        tick_seconds: float = 10, input_fd: int | None = None,
        output_fd: int | None = None) -> ProcessResult:
    """Relay a real controlling PTY, preserving resize, input, Unicode and cleanup.

    stdin/stdout/stderr of the child are terminals. The parent keeps the lease
    alive and checkpoints while the contributor uses the CLI. Exiting the CLI
    returns control to Co4; it does not by itself assert phase success.
    """
    input_fd, output_fd = require_terminal(input_fd, output_fd)
    import fcntl
    import pty
    import termios
    import tty
    if not argv or timeout_seconds <= 0 or idle_seconds <= 0 or tick_seconds <= 0:
        raise ValueError('A command and positive terminal watchdog intervals are required')
    started = last_activity = last_tick = last_flush = time.monotonic()
    master, slave = pty.openpty()
    process = None
    saved = termios.tcgetattr(input_fd)
    output_blocking = os.get_blocking(output_fd)
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    pending: list[str] = []
    pending_chars = 0
    def flush():
        nonlocal pending_chars, last_flush
        if pending:
            text = ''.join(pending)
            pending.clear()
            pending_chars = 0
            on_output('terminal', text)
        last_flush = time.monotonic()
    try:
        termios.tcsetattr(slave, termios.TCSANOW, saved)
        size = fcntl.ioctl(input_fd, termios.TIOCGWINSZ, b'\0' * 8)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, size)
        child = Path(__file__).with_name('_pty_child.py')
        process = subprocess.Popen([sys.executable, str(child), *argv], cwd=str(cwd), env=env,
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True, close_fds=True)
        os.close(slave)
        slave = -1
        os.set_blocking(master, False)
        os.set_blocking(output_fd, False)
        tty.setraw(input_fd, termios.TCSANOW)
        on_start(process.pid)
        eof = False
        exited_at = None
        while True:
            now = time.monotonic()
            if now - started > timeout_seconds:
                raise ExecutionStopped('Absolute interactive execution time limit reached')
            if now - last_activity > idle_seconds:
                raise ExecutionStopped('Interactive terminal idle watchdog fired; recover the allocation to continue')
            if now - last_tick >= tick_seconds:
                flush()
                on_tick()
                last_tick = time.monotonic()
            next_size = fcntl.ioctl(input_fd, termios.TIOCGWINSZ, b'\0' * 8)
            if next_size != size:
                fcntl.ioctl(master, termios.TIOCSWINSZ, next_size)
                size = next_size
            code = process.poll()
            if code is not None:
                exited_at = exited_at or now
                if eof or now - exited_at > .3:
                    break
            ready, _, _ = select.select([input_fd] + ([] if eof else [master]), [], [], min(.05, tick_seconds))
            if input_fd in ready:
                data = os.read(input_fd, 4096)
                if not data:
                    raise ExecutionStopped('Contributor terminal disconnected')
                if b'\x1d' in data:
                    raise ExecutionStopped('Interactive allocation stopped by contributor (Ctrl+])')
                if process.poll() is None:
                    _write(master, data)
                last_activity = time.monotonic()
            if master in ready:
                try:
                    chunk = os.read(master, 16384)
                except OSError as error:
                    if error.errno == errno.EIO:
                        chunk = b''
                    elif error.errno == errno.EAGAIN:
                        continue
                    else:
                        raise
                if not chunk:
                    eof = True
                else:
                    _write(output_fd, chunk)
                    text = decoder.decode(chunk)
                    pending.append(text)
                    pending_chars += len(text)
                    last_activity = time.monotonic()
            if pending and (pending_chars >= 16384 or time.monotonic() - last_flush >= .15):
                flush()
        pending.append(decoder.decode(b'', final=True))
        flush()
        on_tick()
        return ProcessResult(process.wait(), time.monotonic() - started)
    finally:
        # Cleanup must run for Ctrl+], lease revocation, HTTP errors and watchdogs.
        # Restore the user's terminal even if cancellation itself encounters an error.
        try:
            if process is not None:
                _stop_group(process)
            if pending:
                try:
                    flush()
                except Exception:
                    pass  # Preserve the original stop reason; durable unacked events remain replayable.
        finally:
            try:
                termios.tcsetattr(input_fd, termios.TCSANOW, saved)
            finally:
                os.set_blocking(output_fd, output_blocking)
                os.close(master)
                if slave >= 0:
                    os.close(slave)


def confirm_phase(phase: str, *, on_tick: Callable[[], None] = lambda: None,
                  timeout_seconds: float = 1800) -> bool:
    """Explicit phase boundary with heartbeat supervision. This is not PR approval."""
    fd, out = require_terminal()
    import termios
    termios.tcflush(fd, termios.TCIFLUSH)  # Queued CLI input is not phase confirmation.
    was_blocking = os.get_blocking(out)
    try:
        os.set_blocking(out, False)
        _write(out, (f'\nCo4: {phase} session closed. Type continue to validate this phase, or stop to pause: ').encode())
    finally:
        os.set_blocking(out, was_blocking)
    start = last_tick = time.monotonic()
    line = bytearray()
    while time.monotonic() - start < timeout_seconds:
        if time.monotonic() - last_tick >= 1:
            on_tick()
            last_tick = time.monotonic()
        if select.select([fd], [], [], .1)[0]:
            data = os.read(fd, 4096)
            if not data:
                return False
            line.extend(data)
            if len(line) > 4096:
                return False
            if b'\n' in line or b'\r' in line:
                return bytes(line).strip().lower() == b'continue'
    raise ExecutionStopped('Interactive phase confirmation timed out; allocation paused')
