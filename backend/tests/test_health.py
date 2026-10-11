from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_without_external_services() -> None:
    settings = Settings(_env_file=None, environment="test")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
