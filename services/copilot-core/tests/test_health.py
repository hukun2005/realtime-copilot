"""HTTP 面：/healthz 与自动文档关闭。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "test"
    assert body["protocol"] == 1
    assert body["mode"] == "standalone"
    assert body["database"] == "ok"
    assert body["connections"] == 0
    assert body["uptimeMs"] >= 0


def test_auto_docs_are_disabled(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_unknown_route_is_404(client: TestClient) -> None:
    assert client.get("/").status_code == 404


def test_healthz_reports_connection_count(config, token, data_dir) -> None:  # type: ignore[no-untyped-def]
    from copilot_core.app import create_app
    from fastapi.testclient import TestClient as _TestClient

    app = create_app(config, token=token, data_dir=data_dir, version="test")
    with _TestClient(app) as test_client:
        assert test_client.get("/healthz").json()["connections"] == 0
        app.state.connections = 3
        assert test_client.get("/healthz").json()["connections"] == 3
