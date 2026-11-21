from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from apps.api.auth import Scope
from apps.api.main import app


def session(issue_key, tenant, *scopes):
    workspace, _, subject = tenant
    key = issue_key(*tenant, *scopes)["api_key"]
    return {
        "params": {"workspace_id": workspace, "subject_id": subject},
        "headers": {"Authorization": f"Bearer {key}"},
    }


def test_console_management_and_scoped_activity(tenants, issue_key):
    tenant, other_tenant = tenants
    admin = session(
        issue_key,
        tenant,
        Scope.WORKSPACE_MANAGE,
        Scope.MEMORY_READ,
        Scope.MEMORY_WRITE,
        Scope.MEMORY_VERIFY,
    )
    reader = session(issue_key, tenant, Scope.MEMORY_READ)
    other = session(issue_key, other_tenant, Scope.WORKSPACE_MANAGE, Scope.MEMORY_READ)
    with TestClient(app) as client:
        connected = client.get("/v1/console/session", **admin).json()
        assert "workspace:manage" in connected["scopes"]
        blocked = client.get("/v1/console/agents", **reader)
        assert blocked.status_code == 403
        assert (
            client.get(
                "/v1/console/agents", **{**admin, "headers": other["headers"]}
            ).status_code
            == 403
        )

        renamed = client.patch(
            "/v1/console/settings",
            **admin,
            json={
                "name": "Operations",
                "default_retention_days": 14,
                "default_review_days": 23,
            },
        )
        assert renamed.status_code == 200, renamed.text
        assert (
            client.get("/v1/console/session", **reader).json()["workspace"]
            == "Operations"
        )

        agent = client.post(
            "/v1/console/agents", **admin, json={"name": "Search agent"}
        )
        assert agent.status_code == 201, agent.text
        agent_id = agent.json()["id"]
        assert (
            client.post(
                "/v1/console/keys",
                **other,
                json={"agent_id": agent_id, "scopes": ["memory:read"]},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/v1/console/keys",
                **admin,
                json={"agent_id": agent_id, "scopes": ["workspace:manage"]},
            ).status_code
            == 403
        )

        issued = client.post(
            "/v1/console/keys",
            **admin,
            json={"agent_id": agent_id, "scopes": ["memory:read"]},
        )
        assert issued.status_code == 201, issued.text
        entry = issued.json()
        searcher = {**admin, "headers": {"Authorization": "Bearer " + entry["api_key"]}}
        assert (
            client.get("/v1/console/session", **searcher).json()["agent"]
            == "Search agent"
        )
        keys = client.get("/v1/console/keys", **admin).json()
        assert all(
            "api_key" not in item and "token_digest" not in item for item in keys
        )

        created = client.post(
            "/v1/memories",
            **admin,
            json={
                "category": "project",
                "content": "PostgreSQL database.",
                "source": {
                    "origin": "test",
                    "source_ref": "test:console",
                    "observed_at": "2026-11-18T12:00:00Z",
                },
                "claims": [{"predicate": "database", "object_value": "PostgreSQL"}],
            },
        )
        assert created.status_code == 201, created.text
        item = created.json()
        duration = datetime.fromisoformat(item["expires_at"]) - datetime.fromisoformat(
            item["created_at"]
        )
        assert abs(duration - timedelta(days=14)) < timedelta(seconds=3)
        reviewed = client.post(
            f"/v1/memories/{item['id']}/verify",
            **admin,
            json={
                "outcome": "verified",
                "claim_id": item["claims"][0]["id"],
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        claim = reviewed.json()
        assert datetime.fromisoformat(claim["review_due_at"]) - datetime.fromisoformat(
            claim["verified_at"]
        ) == timedelta(days=23)
        retrieved = client.post(
            "/v1/memories/search", **searcher, json={"query": "PostgreSQL"}
        )
        assert retrieved.status_code == 200, retrieved.text
        overview = client.get("/v1/console/overview", **admin).json()
        assert overview["writes"][0]["memory_id"] == item["id"]
        assert overview["retrievals"][0]["agent"] == "Search agent"
        assert overview["retrievals"][0]["memory_id"] == item["id"]
        assert client.get("/v1/console/overview", **other).json()["retrievals"] == []
        agents = client.get("/v1/console/agents", **admin).json()
        assert next(actor for actor in agents if actor["id"] == agent_id)[
            "last_activity"
        ]

        key_path = "/v1/console/keys/" + entry["key_id"]
        assert client.delete(key_path, **other).status_code == 404
        assert client.delete(key_path, **admin).status_code == 204
        assert client.get("/v1/console/session", **searcher).status_code == 401
