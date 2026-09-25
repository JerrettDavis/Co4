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
        assert body['session_token']
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