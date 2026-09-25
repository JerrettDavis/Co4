from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from cryptography.fernet import Fernet


def _configure_streams() -> None:
    """Force UTF-8 on stdout/stderr so non-ASCII output never crashes on Windows."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def main():
    _configure_streams()
    parser = argparse.ArgumentParser(prog="co4", description="Co4 contribution governance and device orchestration")
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve", help="Run the production GitHub-connected control plane")
    server.add_argument("--host", default="0.0.0.0")
    server.add_argument("--port", type=int, default=8080)
    demo = sub.add_parser("demo", help="Run the loopback-only, offline demo control plane")
    demo.add_argument("--port", type=int, default=8080)
    demo.add_argument("--database", default="sqlite:///./co4-demo.db")
    worker = sub.add_parser("worker", help="Poll and execute locally approved contribution work")
    worker.add_argument("--config", default="worker.toml")
    worker.add_argument("--once", action="store_true")
    mode = worker.add_mutually_exclusive_group()
    mode.add_argument("--interactive", dest="execution_mode", action="store_const", const="interactive",
                      help="Run the native harness TUI in this terminal; never fall back to print mode")
    mode.add_argument("--noninteractive", dest="execution_mode", action="store_const", const="noninteractive",
                      help="Explicitly use the programmatic harness adapter")
    mode.add_argument("--execution-mode", choices=["interactive", "noninteractive"])
    doctor = sub.add_parser("doctor", help="Check locally installed Git and harness executables")
    doctor.add_argument("--config", default="worker.toml")
    sub.add_parser("keygen", help="Generate an encryption key for CO4_DATA_KEY")
    demo_worker = sub.add_parser("demo-worker", help="Enroll and run a deterministic offline fixture worker")
    demo_worker.add_argument("--server", default="http://localhost:8080")
    demo_worker.add_argument("--root", default="./.co4-demo-worker")
    demo_worker.add_argument("--identity", choices=["contributor", "backup"], default="contributor")
    export = sub.add_parser("trace", help="Decrypt a local transcript to stdout; output may contain sensitive data")
    export.add_argument("--root", default="~/.co4")
    export.add_argument("lease_id")
    args = parser.parse_args()
    try:
        if args.command == "keygen":
            print(Fernet.generate_key().decode())
        elif args.command in {"serve", "demo"}:
            import uvicorn
            from co4.app import create_app
            from co4.config import Settings
            settings = Settings()
            if args.command == "demo":
                settings.demo = True
                settings.database_url = args.database
                settings.public_url = f"http://localhost:{args.port}"
                settings.app_id = settings.client_id = settings.client_secret = settings.private_key_path = ""
            else:
                settings.demo = False  # `serve` can never expose fixture identities via an ambient env flag.
            uvicorn.run(create_app(settings), host="127.0.0.1" if args.command == "demo" else args.host, port=args.port)
        elif args.command in {"worker", "doctor"}:
            from co4.worker import Worker, doctor, load_config
            config = load_config(args.config)
            if args.command == "worker" and args.execution_mode:
                config["execution_mode"] = args.execution_mode
            if args.command == "doctor":
                checks = doctor(config)
                print(json.dumps(checks, indent=2))
                if not all(x["installed"] for x in checks):
                    raise SystemExit(1)
            else:
                Worker(config).run(once=args.once)
        elif args.command == "demo-worker":
            import httpx
            from co4.worker import Worker, init_demo_source, load_config
            root = Path(args.root).resolve()
            root.mkdir(parents=True, exist_ok=True)
            source = root / "fixture-source"
            init_demo_source(source)
            with httpx.Client(base_url=args.server, headers={"X-Co4-CSRF": "1"}, timeout=20) as client:
                health = client.get("/healthz"); health.raise_for_status()
                if health.json().get("mode") != "demo":
                    raise ValueError("demo-worker refuses to connect to a production server")
                auth = client.post("/auth/demo", json={"login": args.identity}); auth.raise_for_status()
                enrolled = client.post("/api/devices", json={"name": "Offline fixture worker", "harness": "mock", "autonomy": "automatic"})
                enrolled.raise_for_status()
                raw = enrolled.json()["token"]
            config_file = root / "worker.toml"
            # JSON-quoted strings are compatible with these TOML basic string values.
            quote = json.dumps
            config_file.write_text(f'''server = {quote(args.server)}
root = {quote(str(root))}
token_env = "CO4_DEMO_DEVICE_TOKEN"
demo = true
acknowledge_code_execution = true
allow_checkpoint_push = true
harnesses = ["mock"]
pick_next = false
[repositories."co4-demo/tiny-library"]
push_repository = "co4-demo/tiny-library"
local_source = {quote(str(source))}
test_profile = "default"
test_command = [{quote(sys.executable)}, "-m", "unittest", "discover", "-v"]
test_globs = ["test_*.py"]
''')
            os.environ["CO4_DEMO_DEVICE_TOKEN"] = raw
            Worker(load_config(config_file)).run(once=True)
        elif args.command == "trace":
            from co4.security import Vault
            if not __import__("re").fullmatch(r"[a-f0-9]{32}", args.lease_id):
                raise ValueError("Invalid lease identifier")
            root = Path(args.root).expanduser()
            vault = Vault((root / "transcript.key").read_text().strip())
            with (root / "runs" / args.lease_id / "transcript.enc.jsonl").open() as f:
                for line in f:
                    print(json.dumps(vault.open(line.strip()), ensure_ascii=True))
    except KeyboardInterrupt:
        print("Stopped; active subprocesses were cancelled.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        from co4.security import redact
        print("Co4: " + redact(str(error)), file=sys.stderr)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
