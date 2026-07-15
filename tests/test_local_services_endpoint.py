from unittest.mock import patch

from fastapi.testclient import TestClient

from cowork.server import app


def test_list_services_returns_seeded_omniroute():
    client = TestClient(app)
    with patch("cowork.services.local_services._probe", return_value=False):
        response = client.get("/api/v1/services/")
    assert response.status_code == 200
    body = response.json()
    ids = [s["id"] for s in body]
    assert "omniroute" in ids


def test_restart_unknown_service_is_404():
    client = TestClient(app)
    response = client.post("/api/v1/services/does-not-exist/restart")
    assert response.status_code == 404


def test_restart_known_service_returns_status():
    client = TestClient(app)
    with patch("cowork.services.local_services._probe", return_value=True):
        response = client.post("/api/v1/services/omniroute/restart")
    assert response.status_code == 200
    assert response.json()["status"] == "running"
