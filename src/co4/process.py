from __future__ import annotations
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

class ExecutionStopped(RuntimeError):
    pass

@dataclass
class ProcessResult:
    exit_code: int
    duration_seconds: float


def kill_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def run_process(argv: list[str], cwd, *, stdin: str | None = None, env: dict | None = None,
                on_output: Callable[[str, str], None] = lambda *_: None,
                on_tick: Callable[[], None] = lambda: None,
                timeout_seconds: float = 3600, idle_seconds: float = 900,
                tick_seconds: float = 10) -> ProcessResult:
    """Bounded streams, whole-process-tree cancellation, idle and absolute watchdogs. No shell=True."""
    started = last_output = last_tick = time.monotonic()
    messages = queue.Queue(maxsize=128)
    process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    def read(stream, channel):
        try:
            while chunk := stream.readline(262144):
                messages.put((channel, chunk.decode("utf-8", errors="replace")))
        finally:
            messages.put((channel, None))
            stream.close()
    readers = [threading.Thread(target=read, args=(process.stdout, "stdout"), daemon=True),
               threading.Thread(target=read, args=(process.stderr, "stderr"), daemon=True)]
    for reader in readers:
        reader.start()
    if stdin is not None:
        def writer():
            try:
                process.stdin.write(stdin.encode()); process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        threading.Thread(target=writer, daemon=True).start()
    done = 0
    try:
        while done < 2 or process.poll() is None:
            now = time.monotonic()
            if now - started > timeout_seconds:
                raise ExecutionStopped("Absolute execution time limit reached")
            if now - last_output > idle_seconds:
                raise ExecutionStopped("Harness or test command stopped communicating; idle watchdog fired")
            if now - last_tick >= tick_seconds:
                on_tick()
                last_tick = now
            try:
                channel, text = messages.get(timeout=min(0.2, tick_seconds))
                if text is None:
                    done += 1
                else:
                    last_output = time.monotonic()
                    on_output(channel, text)
            except queue.Empty:
                continue
        on_tick()
        return ProcessResult(process.wait(), time.monotonic() - started)
    except BaseException:
        kill_tree(process)
        raise
    finally:
        for reader in readers:
            reader.join(timeout=0.5)
