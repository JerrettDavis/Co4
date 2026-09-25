"""GitHub device flow authentication tests."""
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from co4.github import GitHub, GitHubError
from co4.models import DeviceFlow, Session


def test_device_flow_init_returns_user_code(app, monkeypatch):
    app.state.settings.demo = False
    monkeypatch.setattr(app.state.github, 'request_device_code',
                        lambda scope: {'device_code':'raw-device-abc', 'user_code':'ABCD-EFGH',
                                       'verification_uri':'https://github.com/login/device',
                                       'expires_in':600, 'interval':5},
                        raising=False)
    with TestClient(app) as c:
        r = c.post('/auth/github/device/code', headers={'X-Co4-CSRF':'1'})
        assert r.status_code == 200
        body = r.json()
        assert body['user_code'] == 'ABCD-EFGH'
        assert body['device_code'] == 'raw-device-abc'
        assert body['verification_uri'] == 'https://github.com/login/device'
        assert body['expires_in'] == 600
        assert body['interval'] == 5
        with app.state.db.read() as s:
            row = s.get(DeviceFlow, 'raw-device-abc')
            assert row is not None
            assert row.user_code == 'ABCD-EFGH'
            assert row.scope == 'read:user user:email'
            assert row.interval == 5


def test_device_flow_poll_authorized_creates_session(app, monkeypatch):
    app.state.settings.demo = False
    monkeypatch.setattr(app.state.github, 'request_device_code',
                        lambda scope: {'device_code':'dev-1', 'user_code':'CODE-CODE',
                                       'verification_uri':'https://github.com/login/device',
                                       'expires_in':600, 'interval':1},
                        raising=False)
    monkeypatch.setattr(app.state.github, 'poll_device_token',
                        lambda dc: {'status':'authorized', 'access_token':'oauth-tok'},
                        raising=False)
    monkeypatch.setattr(app.state.github, 'user',
                        lambda access: {'id':7777, 'login':'device-user'},
                        raising=False)
    with TestClient(app) as c:
        c.post('/auth/github/device/code', headers={'X-Co4-CSRF':'1'})
        r = c.post('/auth/github/device/poll',
                   headers={'X-Co4-CSRF':'1'},
                   json={'device_code':'dev-1'})
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'authorized'
        assert body['user']['login'] == 'device-user'
        with app.state.db.read() as s:
            sess = s.scalar(select(Session))
            assert sess is not None
            assert app.state.vault.open(sess.github_token) == 'oauth-tok'
            df = s.get(DeviceFlow, 'dev-1')
            assert df.completed_at is not None
            assert df.user_id == sess.user_id


def test_device_flow_poll_pending_no_session(app, monkeypatch):
    app.state.settings.demo = False
    monkeypatch.setattr(app.state.github, 'request_device_code',
                        lambda scope: {'device_code':'dev-2', 'user_code':'PEND-PEND',
                                       'verification_uri':'https://github.com/login/device',
                                       'expires_in':600, 'interval':1},
                        raising=False)
    monkeypatch.setattr(app.state.github, 'poll_device_token',
                        lambda dc: {'status':'pending'},
                        raising=False)
    with TestClient(app) as c:
        c.post('/auth/github/device/code', headers={'X-Co4-CSRF':'1'})
        r = c.post('/auth/github/device/poll',
                   headers={'X-Co4-CSRF':'1'},
                   json={'device_code':'dev-2'})
        assert r.status_code == 200
        assert r.json()['status'] == 'pending'


def test_device_flow_poll_slow_down_returns_interval(app, monkeypatch):
    app.state.settings.demo = False
    monkeypatch.setattr(app.state.github, 'request_device_code',
                        lambda scope: {'device_code':'dev-3', 'user_code':'SLOW-SLOW',
                                       'verification_uri':'https://github.com/login/device',
                                       'expires_in':600, 'interval':1},
                        raising=False)
    monkeypatch.setattr(app.state.github, 'poll_device_token',
                        lambda dc: {'status':'slow_down', 'interval':10},
                        raising=False)
    with TestClient(app) as c:
        c.post('/auth/github/device/code', headers={'X-Co4-CSRF':'1'})
        r = c.post('/auth/github/device/poll', headers={'X-Co4-CSRF':'1'},
                   json={'device_code':'dev-3'})
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'slow_down'
        assert body['interval'] == 10


def test_device_flow_poll_unknown_device_code_returns_404(app):
    app.state.settings.demo = False
    with TestClient(app) as c:
        r = c.post('/auth/github/device/poll', headers={'X-Co4-CSRF':'1'},
                   json={'device_code':'does-not-exist'})
        assert r.status_code == 404


def test_device_flow_disabled_in_demo_mode(app):
    with TestClient(app) as c:
        r = c.post('/auth/github/device/code', headers={'X-Co4-CSRF':'1'})
        assert r.status_code == 404


def test_device_flow_completed_cannot_be_reused(app, monkeypatch):
    app.state.settings.demo = False
    monkeypatch.setattr(app.state.github, 'request_device_code',
                        lambda scope: {'device_code':'dev-4', 'user_code':'USED-USED',
                                       'verification_uri':'https://github.com/login/device',
                                       'expires_in':600, 'interval':1},
                        raising=False)
    monkeypatch.setattr(app.state.github, 'poll_device_token',
                        lambda dc: {'status':'authorized', 'access_token':'t'},
                        raising=False)
    monkeypatch.setattr(app.state.github, 'user',
                        lambda access: {'id':8888, 'login':'one-time'},
                        raising=False)
    with TestClient(app) as c:
        c.post('/auth/github/device/code', headers={'X-Co4-CSRF':'1'})
        first = c.post('/auth/github/device/poll', headers={'X-Co4-CSRF':'1'},
                       json={'device_code':'dev-4'})
        assert first.status_code == 200
        second = c.post('/auth/github/device/poll', headers={'X-Co4-CSRF':'1'},
                        json={'device_code':'dev-4'})
        assert second.status_code == 409


def test_device_flow_page_renders_html(app):
    with TestClient(app) as c:
        r = c.get('/auth/github/device?code=ABCD-EFGH')
        assert r.status_code == 200
        assert 'ABCD-EFGH' in r.text
        assert 'text/html' in r.headers.get('content-type', '')


def test_request_device_code_raises_on_4xx(app):
    def handler(req):
        return httpx.Response(400, json={'error':'invalid_client'})
    gh = GitHub(app.state.settings, httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(GitHubError):
        gh.request_device_code('read:user')


@pytest.mark.parametrize('error_field,expected_status', [
    ('authorization_pending', 'pending'),
    ('slow_down', 'slow_down'),
    ('expired_token', 'expired'),
    ('access_denied', 'denied'),
])
def test_poll_device_token_maps_error_codes(app, error_field, expected_status):
    def handler(req):
        return httpx.Response(200, json={'error': error_field})
    gh = GitHub(app.state.settings, httpx.Client(transport=httpx.MockTransport(handler)))
    result = gh.poll_device_token('dev')
    assert result['status'] == expected_status

def _authorize_device(app, monkeypatch, device_code='dev-cookie', github_id=4242, login='cookie-user'):
    monkeypatch.setattr(app.state.github, 'request_device_code',
                        lambda scope: {'device_code':device_code, 'user_code':'COOK-COOK',
                                       'verification_uri':'https://github.com/login/device',
                                       'expires_in':600, 'interval':1},
                        raising=False)
    monkeypatch.setattr(app.state.github, 'poll_device_token',
                        lambda dc: {'status':'authorized', 'access_token':'oauth-tok'},
                        raising=False)
    monkeypatch.setattr(app.state.github, 'user',
                        lambda access: {'id':github_id, 'login':login},
                        raising=False)


def test_device_flow_poll_authorized_sets_httponly_session_cookie(app, monkeypatch):
    app.state.settings.demo = False
    _authorize_device(app, monkeypatch)
    with TestClient(app) as c:
        c.post('/auth/github/device/code', headers={'X-Co4-CSRF':'1'})
        r = c.post('/auth/github/device/poll', headers={'X-Co4-CSRF':'1'},
                   json={'device_code':'dev-cookie'})
        assert r.status_code == 200
        set_cookie = r.headers.get('set-cookie', '')
        assert set_cookie.startswith('co4_session=')
        assert 'httponly' in set_cookie.lower()
        assert 'session_token' not in r.json()
        boot = c.get('/api/bootstrap').json()
        assert boot['user']['login'] == 'cookie-user'


def _loopback_settings(tmp_path, public_url='http://127.0.0.1:8080'):
    from cryptography.fernet import Fernet
    from co4.config import Settings
    key = tmp_path / 'app.pem'
    key.write_text('unused')
    return Settings(demo=False, database_url=f"sqlite:///{tmp_path}/loop.db", background=False,
                    public_url=public_url, data_key=Fernet.generate_key().decode(),
                    app_id='1', app_slug='co4', private_key_path=str(key),
                    webhook_secret='w' * 32, client_id='cid', client_secret='csecret')


def test_serve_mode_on_loopback_http_starts_with_insecure_cookies(tmp_path):
    cfg = _loopback_settings(tmp_path)
    cfg.validate()
    assert cfg.secure_cookies is False


def test_serve_mode_on_non_loopback_http_still_refused(tmp_path):
    cfg = _loopback_settings(tmp_path, public_url='http://co4.example.com')
    with pytest.raises(ValueError, match='HTTPS'):
        cfg.validate()


def test_serve_mode_on_https_keeps_secure_cookies(tmp_path):
    cfg = _loopback_settings(tmp_path, public_url='https://co4.example.com')
    cfg.validate()
    assert cfg.secure_cookies is True


def test_serve_mode_loopback_device_login_then_mutation_passes_origin_guard(tmp_path, monkeypatch):
    from co4.app import create_app
    from co4.github import DemoGitHub
    app = create_app(_loopback_settings(tmp_path), github=DemoGitHub())
    try:
        _authorize_device(app, monkeypatch)
        with TestClient(app, base_url='http://localhost:8080') as c:
            c.post('/auth/github/device/code', headers={'X-Co4-CSRF':'1'})
            r = c.post('/auth/github/device/poll', headers={'X-Co4-CSRF':'1'},
                       json={'device_code':'dev-cookie'})
            assert r.status_code == 200
            created = c.post('/api/devices',
                             headers={'X-Co4-CSRF':'1', 'Origin':'http://localhost:8080'},
                             json={'name':'Laptop', 'harness':'claude'})
            assert created.status_code == 200, created.text
    finally:
        app.state.db.engine.dispose()


@pytest.mark.parametrize('origin,public_url,expected', [
    ('http://localhost:8080', 'http://127.0.0.1:8080', True),
    ('http://127.0.0.1:8080', 'http://localhost:8080', True),
    ('http://localhost:9090', 'http://127.0.0.1:8080', False),
    ('https://localhost:8080', 'http://127.0.0.1:8080', False),
    ('http://evil.example', 'http://127.0.0.1:8080', False),
    ('https://co4.example.com', 'https://co4.example.com', True),
    ('http://localhost', 'https://co4.example.com', False),
    ('https://evil:99999', 'http://127.0.0.1:8080', False),
    ('https://evil:99999', 'https://co4.example.com', False),
    ('http://localhost:abc', 'http://127.0.0.1:8080', False),
    ('http://localhost:abc', 'https://co4.example.com', False),
])
def test_origins_match_relaxes_only_between_loopback_hosts(origin, public_url, expected):
    from co4.app import _origins_match
    assert _origins_match(origin, public_url) is expected
