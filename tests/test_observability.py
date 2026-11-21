from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.db import request_database
from apps.api.main import app


def test_request_ids_and_low_cardinality_metrics():
    memory_id = uuid4()
    with TestClient(app) as client:
        missing = client.get(
            f"/v1/memories/{memory_id}",
            params={
                "workspace_id": str(uuid4()),
                "subject_id": str(uuid4()),
            },
        )
        assert missing.headers["cache-control"] == "no-store"
        assert len(missing.headers["x-request-id"]) == 32
        assert (
            missing.headers["x-request-id"]
            != client.get("/healthz").headers["x-request-id"]
        )
        metrics = client.get("/metrics").text

    assert (
        'agent_memory_http_requests_total{method="GET",route="/v1/memories/{memory_id}"'
        in metrics
    )
    assert str(memory_id) not in metrics


def test_validation_error_omits_original_input(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, request_database, lambda: None)
    malformed = "marker-for-private-input"
    with TestClient(app) as client:
        response = client.get(f"/v1/memories/{malformed}")
    assert response.status_code == 422
    assert malformed not in response.text
    assert all(
        set(issue) == {"loc", "msg", "type"} for issue in response.json()["detail"]
    )
