from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from co4.app import create_app
from co4.config import Settings

@pytest.fixture
def app(tmp_path):
    cfg = Settings(demo=True, database_url=f"sqlite:///{tmp_path}/test.db", background=False,
                   webhook_secret="test-secret-" * 4, public_url="http://testserver")
    app = create_app(cfg)
    yield app
    app.state.db.engine.dispose()

@pytest.fixture
def clients(app):
    found = {}
    for name in ["maintainer", "contributor", "backup"]:
        client = TestClient(app, headers={"X-Co4-CSRF": "1"})
        assert client.post("/auth/demo", json={"login": name}).status_code == 200
        found[name] = client
    yield found
    for client in found.values():
        client.close()

def device_client(app, user_client, *, autonomy="automatic", harness="mock", labels=None, max_tokens=100_000):
    response = user_client.post("/api/devices", json={"name": "Test device", "harness": harness,
        "autonomy": autonomy, "labels": labels or [], "max_tokens": max_tokens})
    assert response.status_code == 200, response.text
    info = response.json()
    worker = TestClient(app, headers={"Authorization": "Bearer " + info["token"]})
    return worker, info

def poll(worker, repositories=None):
    response = worker.post("/api/worker/poll", json={"repositories": repositories or ["co4-demo/tiny-library"]})
    assert response.status_code == 200, response.text
    return response.json()
