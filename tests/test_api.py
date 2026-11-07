import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.seed import seed


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_health_and_empty_portfolio(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/v1/dashboard").json()["total_accounts"] == 0
    assert client.get("/api/v1/accounts").json()["items"] == []


def test_not_found_and_query_validation(client):
    assert client.get("/api/v1/accounts/missing").status_code == 404
    for query in ("limit=0", "limit=101", "offset=-1", "status=unknown"):
        assert client.get(f"/api/v1/accounts?{query}").status_code == 422


def test_cors_for_frontend(client):
    response = client.options("/api/v1/accounts", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET",
    })
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_seed_search_pagination_and_detail(client):
    seed()
    seed()
    response = client.get("/api/v1/accounts?limit=2&offset=2")
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 8
    assert len(page["items"]) == 2
    assert page["items"][0]["id"] == "CF-1003"
    search = client.get("/api/v1/accounts?search=alex&status=critical").json()
    assert search["total"] == 1
    assert search["items"][0]["borrower_name"] == "Alex Morgan"
    assert client.get("/api/v1/accounts/CF-1001").json() == search["items"][0]
    assert client.get("/api/v1/dashboard").json()["total_outstanding"] == 21_500_000
