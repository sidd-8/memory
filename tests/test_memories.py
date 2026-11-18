from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from apps.api.auth import Scope
from apps.api.db import connect
from apps.api.main import app

COLLECTION = "/v1/memories"


def credentials(issue_key, tenant, *scopes):
    workspace, _, subject = tenant
    token = issue_key(*tenant, *scopes)["api_key"]
    return {
        "params": {"workspace_id": workspace, "subject_id": subject},
        "headers": {"Authorization": f"Bearer {token}"},
    }


def memory_data(company="Acme", source_ref="conversation:1"):
    return {
        "category": "semantic",
        "content": f"Sam works for {company}",
        "source": {
            "origin": "conversation",
            "source_ref": source_ref,
            "observed_at": "2026-11-17T15:00:00Z",
            "trust_class": "user_confirmed",
        },
        "claims": [
            {
                "predicate": "employer",
                "subject_entity": {"kind": "person", "reference": "sam"},
                "object_entity": {"kind": "company", "reference": company},
                "valid_from": "2026-11-01T00:00:00Z",
                "confidence": 0.9,
            }
        ],
    }


def create(client, auth, data=None):
    response = client.post(COLLECTION, json=data or memory_data(), **auth)
    assert response.status_code == 201, response.text
    return response.json()


def test_cross_agent_provenance_and_append_only_history(provision, tenants, issue_key):
    tenant, _ = tenants
    workspace, _, subject = tenant
    agent = provision(
        "register-agent",
        workspace,
        "--member-id",
        provision("add-member", workspace, "--user", "editor")["user_id"],
        "--name",
        "Editor",
    )["agent_id"]
    writer = credentials(issue_key, tenant, Scope.MEMORY_WRITE)
    editor = credentials(
        issue_key, (workspace, agent, subject), Scope.MEMORY_WRITE, Scope.MEMORY_READ
    )

    with TestClient(app) as first_session:
        original = create(first_session, writer)

    path = f"{COLLECTION}/{original['id']}"
    with TestClient(app) as second_session:
        saved = second_session.get(path, **editor).json()
        assert saved["claims"][0]["source"] == saved["source"]
        assert saved["claims"][0]["status"] == "candidate"
        assert saved["history"] == []

        replacement = memory_data("Contoso", "conversation:2")
        corrected = second_session.patch(path, json=replacement, **editor)
        assert corrected.status_code == 200, corrected.text
        version = corrected.json()
        assert version["revision"] == 2
        assert version["changed_by"] == agent
        assert version["claims"][0]["source"]["source_ref"] == "conversation:2"
        assert (
            version["claims"][0]["subject_entity"]["id"]
            == saved["claims"][0]["subject_entity"]["id"]
        )

        for category in ("project", "semantic"):
            patch = second_session.patch(path, json={"category": category}, **editor)
            assert patch.status_code == 200, patch.text
        page = second_session.get(
            path, **{**editor, "params": {**editor["params"], "revision_limit": 2}}
        ).json()
        assert [entry["revision"] for entry in page["history"]] == [3, 2]
        assert page["next_revision_before"] == 2
        history = second_session.get(
            path, **{**editor, "params": {**editor["params"], "revision_before": 2}}
        ).json()
        assert history["history"] == [original]
        assert history["next_revision_before"] is None

        create(
            second_session, writer, memory_data("Another employer", "conversation:3")
        )

    with connect() as db:
        rows = db.execute(
            "SELECT status, superseded_at FROM claims WHERE workspace_id = %s AND subject_id = %s",
            (workspace, subject),
        ).fetchall()
    assert sorted(row["status"] for row in rows) == [
        "candidate",
        "candidate",
        "superseded",
    ]
    assert sum(row["superseded_at"] is not None for row in rows) == 1


def test_memory_access_scopes_and_pagination(provision, tenants, issue_key):
    tenant, other_tenant = tenants
    workspace, agent, _ = tenant
    sibling = provision(
        "create-subject", workspace, "--kind", "project", "--reference", "another"
    )["subject_id"]
    scopes = (Scope.MEMORY_READ, Scope.MEMORY_WRITE, Scope.MEMORY_DELETE)
    owner = credentials(issue_key, tenant, *scopes)
    other = credentials(issue_key, other_tenant, *scopes)
    same_workspace = credentials(issue_key, (workspace, agent, sibling), *scopes)
    reader = credentials(issue_key, tenant, Scope.MEMORY_READ)

    with TestClient(app) as client:
        records = [create(client, owner), create(client, owner)]
        create(client, other, memory_data("Hidden Corp"))
        path = f"{COLLECTION}/{records[0]['id']}"
        for outsider in (other, same_workspace):
            assert client.get(path, **outsider).status_code == 404
            assert (
                client.patch(path, json={"category": "project"}, **outsider).status_code
                == 404
            )
            assert client.delete(path, **outsider).status_code == 404
            spoofed = {**owner, "headers": outsider["headers"]}
            assert client.get(path, **spoofed).status_code == 403
        assert (
            client.patch(path, json={"category": "project"}, **reader).status_code
            == 403
        )
        assert client.delete(path, **reader).status_code == 403
        params = {**reader["params"], "limit": 1}
        first = client.get(COLLECTION, **{**reader, "params": params}).json()
        params["after"] = first["next_after"]
        later = client.get(COLLECTION, **{**reader, "params": params}).json()
        assert [first["items"][0]["id"], later["items"][0]["id"]] == sorted(
            item["id"] for item in records
        )
        assert later["next_after"] is None


def test_deletion_cascades_and_preserves_shared_entities(tenants, issue_key):
    tenant, _ = tenants
    workspace, _, subject = tenant
    auth = credentials(
        issue_key, tenant, Scope.MEMORY_READ, Scope.MEMORY_WRITE, Scope.MEMORY_DELETE
    )
    create_auth = {
        **auth,
        "headers": {**auth["headers"], "Idempotency-Key": "remove-this-record"},
    }
    with TestClient(app) as client:
        original = create(client, create_auth)
        retained = create(client, auth, memory_data("Contoso"))
        path = f"{COLLECTION}/{original['id']}"
        assert (
            client.patch(path, json={"category": "project"}, **auth).status_code == 200
        )
        with connect() as db:
            db.execute(
                """
                UPDATE memories SET embedding = '[0.1,0.2,0.3]'::public.vector
                WHERE workspace_id = %s AND subject_id = %s AND id = %s
                """,
                (workspace, subject, original["id"]),
            )
        assert client.delete(path, **auth).status_code == 204
        assert client.get(path, **auth).status_code == 404
        assert (
            client.post(COLLECTION, json=memory_data(), **create_auth).status_code
            == 409
        )

    with connect() as db:
        for table, column in (
            ("memories", "id"),
            ("claims", "memory_id"),
            ("sources", "memory_id"),
            ("memory_versions", "memory_id"),
            ("relationships", "claim_id"),
        ):
            identifier = (
                original["claims"][0]["id"]
                if table == "relationships"
                else original["id"]
            )
            assert (
                db.execute(
                    sql.SQL(
                        "SELECT 1 FROM {} WHERE workspace_id = %s AND subject_id = %s AND {} = %s"
                    ).format(sql.Identifier(table), sql.Identifier(column)),
                    (workspace, subject, identifier),
                ).fetchone()
                is None
            )

        assert db.execute(
            "SELECT reference FROM entities WHERE workspace_id = %s AND subject_id = %s ORDER BY reference",
            (workspace, subject),
        ).fetchall() == [{"reference": "Contoso"}, {"reference": "sam"}]
        assert db.execute(
            "SELECT request_digest, memory_id, revision FROM memory_requests WHERE workspace_id = %s",
            (workspace,),
        ).fetchone() == {"request_digest": None, "memory_id": None, "revision": None}
        assert (
            db.execute(
                "SELECT 1 FROM claims WHERE workspace_id = %s AND subject_id = %s AND memory_id = %s",
                (workspace, subject, retained["id"]),
            ).fetchone()
            is not None
        )


def test_concurrent_create_and_patch_retries(tenants, issue_key):
    tenant, _ = tenants
    auth = credentials(issue_key, tenant, Scope.MEMORY_WRITE, Scope.MEMORY_READ)
    create_auth = {
        **auth,
        "headers": {**auth["headers"], "Idempotency-Key": str(uuid4())},
    }

    def submit():
        with TestClient(app) as client:
            return create(client, create_auth)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: submit(), range(2)))

    assert first == second
    with TestClient(app) as client:
        assert len(client.get(COLLECTION, **auth).json()["items"]) == 1
        assert (
            client.post(
                COLLECTION, json=memory_data("Different"), **create_auth
            ).status_code
            == 409
        )

        path = f"{COLLECTION}/{first['id']}"
        patch_auth = {
            **auth,
            "headers": {**auth["headers"], "Idempotency-Key": str(uuid4())},
        }
        correction = {"category": "project"}
        accepted = client.patch(path, json=correction, **patch_auth)
        assert accepted.status_code == 200, accepted.text
        assert (
            client.patch(path, json={"category": "working"}, **auth).status_code == 200
        )
        retry = client.patch(path, json=correction, **patch_auth)
        assert retry.json() == accepted.json()
        assert client.get(path, **auth).json()["revision"] == 3


@pytest.mark.parametrize(
    "change",
    [
        {"source": None},
        {"claims": [{"predicate": "employer"}]},
        {
            "claims": [
                {
                    "predicate": "employer",
                    "object_value": "Acme",
                    "valid_from": "2026-11-17",
                }
            ]
        },
        {
            "claims": [
                {"predicate": "employer", "object_value": "Acme", "status": "active"}
            ]
        },
    ],
)
def test_provenance_and_claim_validation(tenants, issue_key, change):
    auth = credentials(issue_key, tenants[0], Scope.MEMORY_WRITE)
    with TestClient(app) as client:
        invalid = client.post(COLLECTION, json={**memory_data(), **change}, **auth)
        assert invalid.status_code == 422
        valid = create(client, auth)
        path = f"{COLLECTION}/{valid['id']}"
        assert (
            client.patch(
                path, json={"content": "Changed without evidence"}, **auth
            ).status_code
            == 422
        )
