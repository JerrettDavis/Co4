"""CO4_GITHUB_URL / CO4_GITHUB_API_URL test seam used by scripts/device_flow_e2e.py."""
import httpx
import pytest
from co4.config import Settings
from co4.github import GitHub


def _settings(**overrides):
    return Settings(demo=True, database_url="sqlite://", client_id="cid", client_secret="secret", **overrides)


def test_defaults_point_at_real_github(monkeypatch):
    monkeypatch.delenv("CO4_GITHUB_URL", raising=False)
    monkeypatch.delenv("CO4_GITHUB_API_URL", raising=False)
    gh = GitHub(Settings(demo=True))
    assert gh.web == "https://github.com" and gh.api == "https://api.github.com"


def test_env_overrides_are_read_and_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("CO4_GITHUB_URL", "http://127.0.0.1:9999/")
    monkeypatch.setenv("CO4_GITHUB_API_URL", "http://localhost:9998/")
    gh = GitHub(Settings(demo=True))
    assert gh.web == "http://127.0.0.1:9999" and gh.api == "http://localhost:9998"


def test_loopback_override_routes_device_flow_and_user_calls():
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/login/device/code":
            return httpx.Response(200, json={"device_code": "d", "user_code": "U", "verification_uri": "v",
                                             "expires_in": 900, "interval": 1})
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={"id": 1, "login": "octo"})
    gh = GitHub(_settings(github_url="http://127.0.0.1:9999", github_api_url="http://127.0.0.1:9998"),
                httpx.Client(transport=httpx.MockTransport(handler)))
    gh.request_device_code("read:user")
    assert gh.poll_device_token("d") == {"status": "authorized", "access_token": "tok"}
    assert gh.user("tok")["login"] == "octo"
    assert seen == ["http://127.0.0.1:9999/login/device/code", "http://127.0.0.1:9999/login/oauth/access_token",
                    "http://127.0.0.1:9998/user"]


@pytest.mark.parametrize("field,url", [
    ("github_url", "http://github.example.com"),
    ("github_api_url", "http://10.0.0.5:8080"),
    ("github_url", "ftp://127.0.0.1"),
])
def test_non_https_override_is_refused_off_loopback(field, url):
    with pytest.raises(ValueError, match="loopback"):
        GitHub(_settings(**{field: url}))


def test_https_override_is_allowed_for_enterprise_style_hosts():
    gh = GitHub(_settings(github_url="https://ghe.example.com", github_api_url="https://ghe.example.com/api/v3"))
    assert gh.api == "https://ghe.example.com/api/v3"
