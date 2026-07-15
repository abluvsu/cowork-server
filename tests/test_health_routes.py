from fastapi.testclient import TestClient
from cowork.server import app

def test_health_routes_no_307():
    client = TestClient(app)
    
    # Both should return 200, not 307
    response1 = client.get("/api/v1/health", follow_redirects=False)
    assert response1.status_code == 200
    
    response2 = client.get("/api/v1/health/", follow_redirects=False)
    assert response2.status_code == 200
