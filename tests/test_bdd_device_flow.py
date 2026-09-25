"""Executable steps for specs/device-flow.feature.

GitHub is replaced at the HTTP boundary with ``httpx.MockTransport`` so the real
``co4.github.GitHub`` gateway (form encoding, error-code mapping, /user lookup) runs
unchanged; only the network hop to github.com is simulated.
"""
from __future__ import annotations
from http.cookies import SimpleCookie
from urllib.parse import parse_qs
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when
from co4.app import create_app
from co4.config import Settings
from co4.github import GitHub

scenarios("device-flow.feature")

PUBLIC_URL = "http://127.0.0.1:8080"


class FakeGitHubOAuth:
    """Minimal stand-in for github.com's device-flow endpoints and api.github.com/user."""

    def __init__(self):
        self.codes = {}      # device_code -> {"user_code", "error", "login"}
        self.tokens = {}     # access_token -> login
        self.users = {}      # login -> id
        self.issued = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if request.content else {}
        if request.url.host == "github.com" and request.url.path == "/login/device/code":
            assert form.get("client_id") == "cid"
            self.issued += 1
            device_code = f"device-{self.issued}"
            self.codes[device_code] = {"user_code": f"WDJB-{self.issued:04d}", "error": "authorization_pending", "login": None}
            return httpx.Response(200, json={"device_code": device_code, "user_code": self.codes[device_code]["user_code"],
                                             "verification_uri": "https://github.com/login/device",
                                             "expires_in": 900, "interval": 1})
        if request.url.host == "github.com" and request.url.path == "/login/oauth/access_token":
            assert form.get("grant_type") == "urn:ietf:params:oauth:grant-type:device_code"
            code = self.codes.get(form.get("device_code"))
            if code is None:
                return httpx.Response(200, json={"error": "incorrect_device_code"})
            if code["login"]:
                access = f"gho_fake_{code['login']}"
                self.tokens[access] = code["login"]
                return httpx.Response(200, json={"access_token": access, "token_type": "bearer", "scope": "read:user"})
            return httpx.Response(200, json={"error": code["error"]})
        if request.url.host == "api.github.com" and request.url.path == "/user":
            login = self.tokens.get(request.headers.get("authorization", "").removeprefix("Bearer "))
            if not login:
                return httpx.Response(401, json={"message": "Bad credentials"})
            return httpx.Response(200, json={"id": self.users.setdefault(login, 5000 + len(self.users)), "login": login})
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def world(tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("unused by device flow")
    cfg = Settings(demo=False, database_url=f"sqlite:///{tmp_path}/device-flow.db", background=False,
                   public_url=PUBLIC_URL, data_key=Fernet.generate_key().decode(), app_id="1", app_slug="co4",
                   private_key_path=str(key), webhook_secret="w" * 32, client_id="cid", client_secret="csecret")
    fake = FakeGitHubOAuth()
    app = create_app(cfg, github=GitHub(cfg, httpx.Client(transport=httpx.MockTransport(fake.handle))))
    client = TestClient(app, base_url=PUBLIC_URL, headers={"X-Co4-CSRF": "1"})
    state = {"app": app, "fake": fake, "client": client}
    yield state
    client.close()
    app.state.db.engine.dispose()


@given("Co4 is running in GitHub-connected mode")
def connected_mode(world):
    health = world["client"].get("/healthz").json()
    assert health["mode"] == "github"


@given("the visitor has started device-flow sign-in")
def started_sign_in(world):
    response = world["client"].post("/auth/github/device/code")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_code"] and body["verification_uri"].startswith("https://github.com/")
    world["device_code"] = body["device_code"]


@when(parsers.parse('the visitor approves the code on GitHub as "{login}"'))
def approve_on_github(world, login):
    world["fake"].codes[world["device_code"]]["login"] = login


@when(parsers.parse("GitHub reports the code as {github_error}"))
def github_reports(world, github_error):
    world["fake"].codes[world["device_code"]]["error"] = github_error


@when("the page polls for the result")
def page_polls(world):
    world["poll"] = world["client"].post("/auth/github/device/poll", json={"device_code": world["device_code"]})
    assert world["poll"].status_code == 200, world["poll"].text


@then(parsers.parse('the poll reports the visitor as authorized as "{login}"'))
def poll_authorized(world, login):
    body = world["poll"].json()
    assert body["status"] == "authorized"
    assert body["user"]["login"] == login


@then(parsers.parse("the poll reports the sign-in as {outcome}"))
def poll_outcome(world, outcome):
    assert world["poll"].json() == {"status": outcome}


@then(parsers.parse('the visitor is signed in as "{login}"'))
def signed_in(world, login):
    boot = world["client"].get("/api/bootstrap").json()
    assert boot["user"]["login"] == login
    dashboard = world["client"].get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["user"]["login"] == login


@then("the device code cannot be redeemed a second time")
def not_reusable(world):
    again = world["client"].post("/auth/github/device/poll", json={"device_code": world["device_code"]})
    assert again.status_code == 409


@then("no session cookie is issued")
def no_cookie(world):
    assert "set-cookie" not in world["poll"].headers
    assert world["client"].cookies.get("co4_session") is None


@then("the visitor is still signed out")
def signed_out(world):
    assert world["client"].get("/api/bootstrap").json()["user"] is None
    assert world["client"].get("/api/dashboard").status_code == 401


def _session_morsel(world):
    headers = world["poll"].headers.get_list("set-cookie")
    jar = SimpleCookie()
    for header in headers:
        jar.load(header)
    assert "co4_session" in jar, headers
    return jar["co4_session"]


@then("the session cookie is set by the server with the HttpOnly attribute")
def cookie_httponly(world):
    morsel = _session_morsel(world)
    assert morsel.value
    assert morsel["httponly"] is True


@then("the session cookie is scoped to the whole site with SameSite=Lax")
def cookie_scope(world):
    morsel = _session_morsel(world)
    assert morsel["path"] == "/"
    assert morsel["samesite"].lower() == "lax"


@then("the poll response body does not contain the session token")
def body_has_no_token(world):
    token = _session_morsel(world).value
    assert token not in world["poll"].text
    assert "session_token" not in world["poll"].json()
