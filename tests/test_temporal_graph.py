from fastapi.testclient import TestClient

from apps.api.auth import Scope
from apps.api.main import app


def credentials(issue_key, tenant, *scopes):
    workspace, _, subject = tenant
    token = issue_key(*tenant, *scopes)["api_key"]
    return {
        "params": {"workspace_id": workspace, "subject_id": subject},
        "headers": {"Authorization": f"Bearer {token}"},
    }


def source(trust="user_confirmed"):
    return {
        "origin": "conversation",
        "source_ref": "conversation:demo",
        "observed_at": "2026-11-18T15:00:00Z",
        "trust_class": trust,
    }


def dinner(**fields):
    return {
        "category": "commitment",
        "content": "Dinner with Sam",
        "starts_at": "2026-11-20T19:00:00-05:00",
        "ends_at": "2026-11-20T20:00:00-05:00",
        "timezone": "America/New_York",
        "state": "confirmed",
        "source": source(),
        **fields,
    }


def save(client, auth, record):
    response = client.post("/v1/memories", json=record, **auth)
    assert response.status_code == 201, response.text
    return response.json()


def find_conflicts(client, auth, starts, ends):
    response = client.post(
        "/v1/memories/search",
        json={
            "time_range": {"starts_at": starts, "ends_at": ends},
            "status": "confirmed",
        },
        **auth,
    )
    assert response.status_code == 200, response.text
    return response.json()["evidence"]


def test_confirmed_overlap_across_agents_with_adjacency_and_cancellation(
    provision, tenants, issue_key
):
    tenant, other = tenants
    workspace, _, subject = tenant
    member = provision("add-member", workspace, "--user", "calendar-reader")["user_id"]
    reader_agent = provision(
        "register-agent",
        workspace,
        "--member-id",
        member,
        "--name",
        "Meeting agent",
    )["agent_id"]
    writer = credentials(issue_key, tenant, Scope.MEMORY_WRITE)
    reader = credentials(
        issue_key, (workspace, reader_agent, subject), Scope.MEMORY_READ
    )
    outsider = credentials(issue_key, other, Scope.MEMORY_READ, Scope.MEMORY_WRITE)
    starts, ends = "2026-11-20T19:30:00-05:00", "2026-11-20T20:30:00-05:00"

    with TestClient(app) as first_session:
        confirmed = save(first_session, writer, dinner())
        save(first_session, writer, dinner(state="tentative"))
        save(first_session, writer, dinner(expires_at="2000-01-01T00:00:00Z"))
        save(first_session, outsider, dinner())

    with TestClient(app) as second_session:
        overlap = find_conflicts(second_session, reader, starts, ends)
        assert [hit["memory_id"] for hit in overlap] == [confirmed["id"]]
        assert overlap[0]["source_id"] == confirmed["source"]["id"]
        assert overlap[0]["starts_at"] == "2026-11-21T00:00:00Z"
        assert overlap[0]["timezone"] == "America/New_York"

        assert (
            find_conflicts(second_session, reader, "2026-11-20T20:00:00-05:00", ends)
            == []
        )
        assert (
            find_conflicts(
                second_session,
                reader,
                "2026-11-20T18:00:00-05:00",
                "2026-11-20T19:00:00-05:00",
            )
            == []
        )
        wrong_key = {**reader, "headers": outsider["headers"]}
        rejected = second_session.post(
            "/v1/memories/search",
            json={"status": "confirmed"},
            **wrong_key,
        )
        assert rejected.status_code == 403

        path = f"/v1/memories/{confirmed['id']}"
        changed = second_session.patch(path, json={"state": "cancelled"}, **writer)
        assert changed.status_code == 200, changed.text
        assert find_conflicts(second_session, reader, starts, ends) == []
        assert (
            second_session.get(path, **reader).json()["history"][0]["state"]
            == "confirmed"
        )


def test_dst_fold_keeps_absolute_times_and_requires_confirmation_evidence(
    tenants, issue_key
):
    auth = credentials(issue_key, tenants[0], Scope.MEMORY_READ, Scope.MEMORY_WRITE)
    with TestClient(app) as client:
        record = dinner(
            starts_at="2026-11-01T01:15:00-04:00",
            ends_at="2026-11-01T01:15:00-05:00",
            state="tentative",
            source=source("agent_reported"),
        )
        rejected = client.post(
            "/v1/memories", json={**record, "state": "confirmed"}, **auth
        )
        assert rejected.status_code == 422
        bad_tz = client.post("/v1/memories", json={**record, "timezone": "ET"}, **auth)
        assert bad_tz.status_code == 422
        naive = client.post(
            "/v1/memories", json={**record, "starts_at": "2026-11-01T01:15:00"}, **auth
        )
        assert naive.status_code == 422
        saved = save(client, auth, record)
        path = f"/v1/memories/{saved['id']}"
        assert (
            client.patch(path, json={"state": "confirmed"}, **auth).status_code == 422
        )
        accepted = client.patch(
            path, json={"state": "confirmed", "source": source()}, **auth
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["starts_at"] == "2026-11-01T05:15:00Z"
        assert accepted.json()["ends_at"] == "2026-11-01T06:15:00Z"

        overlap = find_conflicts(
            client, auth, "2026-11-01T01:00:00-05:00", "2026-11-01T01:10:00-05:00"
        )
        assert [hit["memory_id"] for hit in overlap] == [saved["id"]]
        assert (
            find_conflicts(
                client,
                auth,
                "2026-11-01T01:15:00-05:00",
                "2026-11-01T01:30:00-05:00",
            )
            == []
        )
        moved = client.patch(path, json={"ends_at": "2026-11-01T06:30:00Z"}, **auth)
        assert moved.status_code == 422


def relationship(left, right, **validity):
    return {
        "category": "project",
        "content": f"{left} connects to {right}",
        "source": source(),
        "claims": [
            {
                "predicate": "uses",
                "subject_entity": {"kind": "project", "reference": left},
                "object_entity": {"kind": "project", "reference": right},
                **validity,
            }
        ],
    }


def test_graph_depth_budget_validity_deletion_and_tenant_isolation(tenants, issue_key):
    tenant, other = tenants
    writer = credentials(issue_key, tenant, Scope.MEMORY_WRITE, Scope.MEMORY_DELETE)
    reader = credentials(issue_key, tenant, Scope.GRAPH_READ)
    outsider = credentials(issue_key, other, Scope.GRAPH_READ)
    with TestClient(app) as client:
        first = save(client, writer, relationship("Atlas", "Beacon"))
        second = save(
            client,
            writer,
            relationship("Cedar", "Beacon", valid_from="2022-01-01T00:00:00Z"),
        )
        save(
            client,
            writer,
            relationship("Beacon", "Legacy", valid_to="2021-01-01T00:00:00Z"),
        )
        save(
            client,
            writer,
            relationship("Atlas", "Future", valid_from="2099-01-01T00:00:00Z"),
        )
        save(
            client,
            writer,
            {**relationship("Atlas", "Expired"), "expires_at": "2000-01-01T00:00:00Z"},
        )
        root = first["claims"][0]["subject_entity"]["id"]
        path = f"/v1/entities/{root}/graph"

        def graph(**params):
            response = client.get(
                path, **{**reader, "params": {**reader["params"], **params}}
            )
            assert response.status_code == 200, response.text
            return response.json()

        direct = graph()
        assert direct["root_id"] == root
        assert {node["reference"] for node in direct["nodes"]} == {"Atlas", "Beacon"}
        assert direct["edges"][0]["source_id"] == first["source"]["id"]
        two_hops = graph(depth=2)
        assert {node["reference"] for node in two_hops["nodes"]} == {
            "Atlas",
            "Beacon",
            "Cedar",
        }
        assert len(two_hops["edges"]) == 2
        assert len(graph(depth=2, edge_limit=1)["edges"]) == 1
        assert {
            node["reference"]
            for node in graph(depth=2, valid_at="2020-01-01T00:00:00Z")["nodes"]
        } == {
            "Atlas",
            "Beacon",
            "Legacy",
        }
        assert client.get(path, **writer).status_code == 403
        assert client.get(path, **outsider).status_code == 404
        assert (
            client.get(path, **{**reader, "headers": outsider["headers"]}).status_code
            == 403
        )
        assert (
            client.delete(f"/v1/memories/{second['id']}", **writer).status_code == 204
        )
        assert len(graph(depth=2)["edges"]) == 1
