from types import SimpleNamespace
from unittest.mock import MagicMock

from api.admin import routes
from api.app import app


def test_feedback_municipality_route_requires_service_token(monkeypatch):
    monkeypatch.setenv("FEEDBACK_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setattr(
        app,
        "manager",
        SimpleNamespace(_db=MagicMock()),
        raising=False,
    )
    monkeypatch.setattr(app, "_db", SimpleNamespace(info={}), raising=False)
    with app.test_request_context("/internal/feedback/municipalities"):
        response = routes.feedback_municipalities()

    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"


def test_feedback_municipality_route_returns_effective_recipient_emails(monkeypatch):
    monkeypatch.setenv("FEEDBACK_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setattr(
        app,
        "manager",
        SimpleNamespace(_db=MagicMock()),
        raising=False,
    )
    monkeypatch.setattr(app, "_db", SimpleNamespace(info={}), raising=False)
    monkeypatch.setattr(
        routes.EkirjastoConsortiumMonitor,
        "get_consortium_municipality_list",
        lambda: {
            "generated_at": "2026-10-21T00:00:00+00:00",
            "consortiums": [
                {
                    "id": 1,
                    "name": "Test Consortium",
                    "slug": "test-consortium",
                    "municipalities": [
                        {"code": "001", "name": "Member City"},
                        {"code": "002", "name": "City with Own Email"},
                    ],
                }
            ],
            "unassigned_municipalities": [{"code": "003", "name": "Unassigned City"}],
        },
    )
    monkeypatch.setattr(
        routes,
        "get_consortium_municipality_emails",
        lambda _db: {
            "consortiums": {"test-consortium": "consortium@example.fi"},
            "municipalities": {"002": "city@example.fi", "003": "solo@example.fi"},
        },
    )
    with app.test_request_context(
        "/internal/feedback/municipalities",
        headers={"Authorization": "Bearer test-service-token"},
    ):
        response = routes.feedback_municipalities()

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    municipalities = {item["code"]: item for item in response.json["municipalities"]}
    assert municipalities["001"]["email"] == "consortium@example.fi"
    assert municipalities["002"]["email"] == "city@example.fi"
    assert municipalities["003"]["email"] == "solo@example.fi"
    assert response.json["consortiums"][0]["email"] == "consortium@example.fi"
