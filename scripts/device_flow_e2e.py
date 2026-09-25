"""Browser end-to-end test of GitHub device-flow sign-in against a real `co4 serve` process.

Nothing talks to github.com. The script starts:

1. A local stand-in for GitHub (stdlib HTTP server) implementing the device-flow endpoints
   (POST /login/device/code, POST /login/oauth/access_token), the verification page a person
   uses to enter their code (GET/POST /login/device), and GET /user from the REST API.
2. `python -m co4 serve` on loopback HTTP, pointed at that stand-in through
   CO4_GITHUB_URL / CO4_GITHUB_API_URL.
3. Headless Chromium, which clicks through the SPA exactly like a person would: open the
   sign-in dialog, follow the verification link, type the code on the "GitHub" page, approve,
   and then wait for the Co4 tab to turn into the signed-in dashboard.

It asserts the SPA transition (the part only a browser can see), that the session cookie is
HttpOnly and invisible to `document.cookie`, and that denied/expired codes leave the visitor
signed out with a visible error.

Usage:  python scripts/device_flow_e2e.py [--output DIR] [--headed]
"""
from __future__ import annotations
import argparse
import html
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import urllib.request

from playwright.sync_api import expect, sync_playwright

CLIENT_ID = "co4-e2e-client"
GITHUB_LOGIN = "octo-e2e"
GITHUB_ID = 424242


# --------------------------------------------------------------------------------------------
# Local GitHub stand-in
# --------------------------------------------------------------------------------------------

class FakeGitHub:
    def __init__(self):
        self.lock = threading.Lock()
        self.codes: dict[str, dict] = {}   # device_code -> {"user_code", "status"}
        self.tokens: dict[str, str] = {}   # access token -> login
        self.polls = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def set_status(self, user_code: str, status: str) -> bool:
        with self.lock:
            for code in self.codes.values():
                if code["user_code"] == user_code.strip().upper():
                    code["status"] = status
                    return True
        return False

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _form(self):
                length = int(self.headers.get("Content-Length") or 0)
                return {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}

            def _send(self, status, body, content_type="application/json"):
                data = (json.dumps(body) if content_type == "application/json" else body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _page(self, title, body, status=200):
                self._send(status, f"<!doctype html><title>{title}</title><h1>{title}</h1>{body}", "text/html")

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/login/device":
                    return self._page("Device activation", (
                        '<form method="post" action="/login/device">'
                        '<label for="user_code">Enter the code displayed on your device</label>'
                        '<input id="user_code" name="user_code" autocomplete="off">'
                        '<button name="decision" value="authorize">Authorize co4</button>'
                        '<button name="decision" value="deny">Cancel</button></form>'))
                if path == "/user":
                    login = fake.tokens.get(self.headers.get("Authorization", "").removeprefix("Bearer "))
                    if not login:
                        return self._send(401, {"message": "Bad credentials"})
                    return self._send(200, {"id": GITHUB_ID, "login": login})
                self._send(404, {"message": "Not Found"})

            def do_POST(self):
                path = urlparse(self.path).path
                form = self._form()
                if path == "/login/device/code":
                    if form.get("client_id") != CLIENT_ID:
                        return self._send(200, {"error": "invalid_client"})
                    device_code = secrets.token_hex(16)
                    user_code = f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
                    with fake.lock:
                        fake.codes[device_code] = {"user_code": user_code, "status": "pending"}
                    return self._send(200, {"device_code": device_code, "user_code": user_code,
                                            "verification_uri": fake.url + "/login/device",
                                            "expires_in": 900, "interval": 1})
                if path == "/login/oauth/access_token":
                    if form.get("grant_type") != "urn:ietf:params:oauth:grant-type:device_code":
                        return self._send(200, {"error": "unsupported_grant_type"})
                    with fake.lock:
                        fake.polls += 1
                        code = fake.codes.get(form.get("device_code", ""))
                        if code is None:
                            return self._send(200, {"error": "incorrect_device_code"})
                        status = code["status"]
                        if status == "approved":
                            code["status"] = "redeemed"
                            access = "gho_" + secrets.token_hex(16)
                            fake.tokens[access] = GITHUB_LOGIN
                            return self._send(200, {"access_token": access, "token_type": "bearer", "scope": "read:user"})
                    error = {"pending": "authorization_pending", "denied": "access_denied",
                             "expired": "expired_token", "redeemed": "bad_verification_code"}[status]
                    return self._send(200, {"error": error})
                if path == "/login/device":
                    status = "approved" if form.get("decision") == "authorize" else "denied"
                    if not fake.set_status(form.get("user_code", ""), status):
                        return self._page("Unknown code", "<p>That code is not valid.</p>", 404)
                    if status == "approved":
                        return self._page("Congratulations, you're all set!",
                                          f"<p>Your device is now connected as {html.escape(GITHUB_LOGIN)}.</p>")
                    return self._page("Access denied", "<p>You cancelled the authorization.</p>")
                self._send(404, {"message": "Not Found"})

        return Handler


# --------------------------------------------------------------------------------------------
# Co4 server process
# --------------------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_co4(github: FakeGitHub, workdir: Path):
    from cryptography.fernet import Fernet
    port = free_port()
    key = workdir / "app-private-key.pem"
    key.write_text("not used by device-flow sign-in")
    env = {**os.environ,
           "CO4_PUBLIC_URL": f"http://127.0.0.1:{port}",
           "CO4_DATABASE_URL": "sqlite:///" + (workdir / "co4-e2e.db").as_posix(),
           "CO4_DATA_KEY": Fernet.generate_key().decode(),
           "GITHUB_APP_ID": "1", "GITHUB_APP_SLUG": "co4-e2e",
           "GITHUB_APP_PRIVATE_KEY_PATH": str(key),
           "GITHUB_WEBHOOK_SECRET": secrets.token_hex(32),
           "GITHUB_CLIENT_ID": CLIENT_ID, "GITHUB_CLIENT_SECRET": secrets.token_hex(20),
           "CO4_GITHUB_URL": github.url, "CO4_GITHUB_API_URL": github.url}
    env.pop("CO4_DEMO", None)
    log = (workdir / "co4-serve.log").open("w")
    process = subprocess.Popen([sys.executable, "-m", "co4", "serve", "--host", "127.0.0.1", "--port", str(port)],
                               env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit("co4 serve exited early:\n" + (workdir / "co4-serve.log").read_text())
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=2) as response:
                health = json.load(response)
                if health.get("mode") != "github":
                    raise SystemExit(f"Expected GitHub-connected mode, got {health}")
                return process, base, log
        except OSError:
            time.sleep(.3)
    process.terminate()
    raise SystemExit("co4 serve did not become healthy:\n" + (workdir / "co4-serve.log").read_text())


# --------------------------------------------------------------------------------------------
# Browser scenarios
# --------------------------------------------------------------------------------------------

def open_device_dialog(page, base):
    page.goto(base)
    sign_in = page.get_by_role("button", name="Sign in with device flow").first
    expect(sign_in).to_be_visible()
    sign_in.click()
    expect(page.get_by_role("heading", name="Sign in with GitHub")).to_be_visible()
    user_code = page.locator("#device-user-code").inner_text().strip()
    assert user_code, "the dialog did not show a user code"
    return user_code


def approve_on_github(context, page, user_code, decision):
    with context.expect_page() as popup_info:
        page.locator("#dialog a[target=_blank]").click()   # the verification_uri link
    github = popup_info.value
    github.wait_for_load_state()
    expect(github.get_by_role("heading", name="Device activation")).to_be_visible()
    github.get_by_label("Enter the code displayed on your device").fill(user_code)
    github.get_by_role("button", name=decision).click()
    return github


def session_cookies(context):
    return [c for c in context.cookies() if c["name"] == "co4_session"]


def scenario_approved(browser, base, github, shots):
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    user_code = open_device_dialog(page, base)
    expect(page.locator("#device-poll-status")).to_contain_text("Waiting")
    assert not session_cookies(context), "a session existed before GitHub approval"
    if shots:
        page.screenshot(path=str(shots / "device-flow-code.png"))
    tab = approve_on_github(context, page, user_code, "Authorize co4")
    expect(tab.get_by_role("heading", name="Congratulations, you're all set!")).to_be_visible()
    tab.close()
    # The SPA must transition by itself: no manual reload, dialog closes, dashboard renders.
    expect(page.get_by_role("heading", name="Build together, deliberately.")).to_be_visible(timeout=15_000)
    expect(page.locator(".workspace")).to_contain_text(f"{GITHUB_LOGIN}'s workspace")
    # Regression guard for the original bug: the token must never be script-visible.
    cookies = session_cookies(context)
    assert len(cookies) == 1, cookies
    assert cookies[0]["httpOnly"] is True, cookies[0]
    assert cookies[0]["sameSite"] == "Lax" and cookies[0]["path"] == "/", cookies[0]
    visible = page.evaluate("document.cookie")
    assert "co4_session" not in visible, f"session cookie readable by page scripts: {visible!r}"
    expect(page.locator(".identity")).to_contain_text(GITHUB_LOGIN)
    expect(page.locator("#dialog")).not_to_have_attribute("open", "")
    expect(page.get_by_role("button", name="Sign in with device flow")).to_have_count(0)
    expect(page.locator("#toast")).to_contain_text(f"Signed in as {GITHUB_LOGIN}")
    boot = page.evaluate("fetch('/api/bootstrap',{credentials:'same-origin'}).then(r=>r.json())")
    assert boot["user"]["login"] == GITHUB_LOGIN, boot
    if shots:
        page.screenshot(path=str(shots / "device-flow-signed-in.png"))
    page.reload()
    expect(page.get_by_role("heading", name="Build together, deliberately.")).to_be_visible()
    page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_role("button", name="Sign in with device flow").first).to_be_visible()
    assert not errors, errors
    context.close()
    return ["device code dialog shown; no session before approval",
            "verification page approval turns the SPA into the signed-in dashboard without a reload",
            "session cookie is HttpOnly, SameSite=Lax, path=/, and absent from document.cookie",
            "session survives a page reload; sign-out returns to the landing page"]


def scenario_not_approved(browser, base, github, kind, shots):
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    user_code = open_device_dialog(page, base)
    if kind == "denied":
        tab = approve_on_github(context, page, user_code, "Cancel")
        expect(tab.get_by_role("heading", name="Access denied")).to_be_visible()
        tab.close()
        message = "Authorization denied on GitHub."
    else:
        assert github.set_status(user_code, "expired")
        message = "Code expired. Please restart sign-in."
    expect(page.locator("#device-poll-status")).to_have_text(message, timeout=15_000)
    if shots:
        page.screenshot(path=str(shots / f"device-flow-{kind}.png"))
    page.wait_for_timeout(2_500)   # longer than the poll interval: nothing may sign us in later
    expect(page.locator("#device-poll-status")).to_have_text(message)
    assert not session_cookies(context), context.cookies()
    boot = page.evaluate("fetch('/api/bootstrap',{credentials:'same-origin'}).then(r=>r.json())")
    assert boot["user"] is None, boot
    page.get_by_role("button", name="Close dialog").click()
    expect(page.get_by_role("button", name="Sign in with device flow").first).to_be_visible()
    expect(page.get_by_role("heading", name="Build together, deliberately.")).to_have_count(0)
    assert not errors, errors
    context.close()
    return [f"{kind} code shows '{message}' and leaves the visitor signed out"]


def scenario_cancelled(browser, base, github, shots):
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    before = github.polls
    open_device_dialog(page, base)
    deadline = time.monotonic() + 10
    while github.polls == before and time.monotonic() < deadline:
        page.wait_for_timeout(200)
    assert github.polls > before, "the SPA never polled GitHub through Co4"
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#dialog")).not_to_have_attribute("open", "")
    page.wait_for_timeout(300)
    settled = github.polls
    page.wait_for_timeout(2_500)   # > 2 poll intervals
    assert github.polls == settled, f"polling continued after Cancel ({settled} -> {github.polls})"
    assert not session_cookies(context)
    expect(page.get_by_role("button", name="Sign in with device flow").first).to_be_visible()
    assert not errors, errors
    context.close()
    return ["Cancel closes the dialog and stops polling without page errors"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", help="directory for screenshots and a JSON report")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--chromium", default=os.getenv("CHROMIUM_PATH"))
    args = parser.parse_args()
    shots = Path(args.output) if args.output else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    github = FakeGitHub().start()
    checks = []
    with tempfile.TemporaryDirectory(prefix="co4-device-flow-e2e-") as tmp:
        process, base, log = start_co4(github, Path(tmp))
        try:
            with sync_playwright() as p:
                launch = {"headless": not args.headed}
                if args.chromium:
                    launch["executable_path"] = args.chromium
                browser = p.chromium.launch(**launch)
                try:
                    checks += scenario_approved(browser, base, github, shots)
                    checks += scenario_not_approved(browser, base, github, "denied", shots)
                    checks += scenario_not_approved(browser, base, github, "expired", shots)
                    checks += scenario_cancelled(browser, base, github, shots)
                finally:
                    browser.close()
        except BaseException:
            log.flush()
            print("---- co4 serve log ----\n" + (Path(tmp) / "co4-serve.log").read_text(), file=sys.stderr)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            log.close()
            github.stop()
    report = {"mode": "native browser HTTP against `co4 serve` + local GitHub stand-in",
              "server": base, "github_stand_in": github.url, "token_polls_seen_by_github": github.polls,
              "checks": checks, "check_count": len(checks), "status": "passed"}
    if shots:
        (shots / "device-flow-e2e-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
