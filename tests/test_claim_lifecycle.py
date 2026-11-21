from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from apps.api.auth import Scope
from apps.api.claim_lifecycle import mark_overdue_claims
from apps.api.db import connect
from apps.api.main import app

MEMORIES = "/v1/memories"


def credentials(issue_key, tenant, *scopes):
    workspace, _, subject = tenant
    key = issue_key(*tenant, *scopes)["api_key"]
    return {
        "params": {"workspace_id": workspace, "subject_id": subject},
        "headers": {"Authorization": f"Bearer {key}"},
    }


def employer(company, starts):
    return {
        "category": "semantic",
        "content": f"Sam works for {company}.",
        "claims": [
            {
                "predicate": "employer",
                "subject_entity": {"kind": "person", "reference": "sam"},
                "object_entity": {"kind": "organization", "reference": company},
                "valid_from": starts,
            }
        ],
        "source": {
            "origin": "conversation",
            "source_ref": "conversation:employment",
            "observed_at": "2026-11-17T15:00:00Z",
            "trust_class": "user_confirmed",
        },
    }


def create(client, auth, data):
    response = client.post(MEMORIES, json=data, **auth)
    assert response.status_code == 201, response.text
    return response.json()


def review(client, auth, memory, claim=None, **data):
    return client.post(
        f"{MEMORIES}/{memory['id']}/verify",
        json={
            "claim_id": claim or memory["claims"][0]["id"],
            "outcome": "verified",
            **data,
        },
        **auth,
    )


def test_employer_change_preserves_validity_and_recorded_history(tenants, issue_key):
    tenant = tenants[0]
    auth = credentials(
        issue_key, tenant, Scope.MEMORY_READ, Scope.MEMORY_WRITE, Scope.MEMORY_VERIFY
    )
    with TestClient(app) as client:
        old = create(client, auth, employer("Acme", "2026-01-01T00:00:00Z"))
        accepted = review(client, auth, old)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "active"
        assert accepted.json()["verified_by"] == tenant[1]
        assert datetime.fromisoformat(
            accepted.json()["review_due_at"]
        ) - datetime.fromisoformat(accepted.json()["verified_at"]) == timedelta(days=90)

        new = create(client, auth, employer("Contoso", "2026-10-01T00:00:00Z"))
        candidate = new["claims"][0]
        assert candidate["status"] == "review_due"
        ambiguous = review(client, auth, new)
        assert ambiguous.status_code == 200
        assert ambiguous.json()["status"] == "review_due"
        assert ambiguous.json()["verified_at"] is None
        assert (
            client.get(f"{MEMORIES}/{old['id']}", **auth).json()["claims"][0]["status"]
            == "active"
        )

        retryable = {
            **auth,
            "headers": {**auth["headers"], "Idempotency-Key": "employment-correction"},
        }
        replaced = review(
            client,
            retryable,
            new,
            supersedes_claim_id=old["claims"][0]["id"],
            review_interval_days=30,
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["status"] == "active"
        retry = review(
            client,
            retryable,
            new,
            supersedes_claim_id=old["claims"][0]["id"],
            review_interval_days=30,
        )
        assert retry.json() == replaced.json()
        assert client.get(f"{MEMORIES}/{new['id']}", **auth).json()["revision"] == 3

        path = f"{MEMORIES}/{old['id']}"
        history = client.get(path, **auth).json()
        assert history["claims"] == []
        assert history["history"][0]["claims"][0]["status"] == "active"
        assert history["history"][0]["claims"][0]["valid_to"] is None

        def at(valid_at):
            response = client.get(
                path + "/claims",
                **{**auth, "params": {**auth["params"], "valid_at": valid_at}},
            )
            assert response.status_code == 200, response.text
            return response.json()

        past = at("2026-09-30T23:59:59Z")
        assert past[0]["object_entity"]["reference"] == "Acme"
        assert past[0]["valid_to"] == "2026-10-01T00:00:00Z"
        assert past[0]["status"] == "superseded"
        assert past[0]["superseded_at"] == replaced.json()["verified_at"]
        assert past[0]["source"]["id"] == old["source"]["id"]
        assert at("2026-10-01T00:00:00Z") == []
        search = client.post(MEMORIES + "/search", **auth, json={"query": "Acme"})
        assert search.status_code == 200, search.text
        assert old["id"] not in {hit["memory_id"] for hit in search.json()["evidence"]}


def test_overdue_claims_are_stale_in_reads_and_policy_is_idempotent(tenants, issue_key):
    auth = credentials(
        issue_key,
        tenants[0],
        Scope.MEMORY_READ,
        Scope.MEMORY_WRITE,
        Scope.MEMORY_VERIFY,
    )
    with TestClient(app) as client:
        memory = create(client, auth, employer("Acme", "2026-01-01T00:00:00Z"))
        assert review(client, auth, memory, review_interval_days=1).status_code == 200
        claim_id = memory["claims"][0]["id"]
        with connect() as db:
            db.execute(
                "UPDATE claims SET review_due_at = now() - interval '1 second' WHERE id = %s",
                (claim_id,),
            )

        path = f"{MEMORIES}/{memory['id']}"
        stale = client.get(path, **auth).json()["claims"][0]
        assert stale["status"] == "stale"
        assert stale["verified_at"] is not None
        result = client.post(
            MEMORIES + "/search", **auth, json={"query": "Acme"}
        ).json()
        assert result["evidence"][0]["claims"][0]["status"] == "stale"

        with connect() as db:
            assert mark_overdue_claims(db) == 1
            assert mark_overdue_claims(db) == 0
        with connect() as db:
            assert (
                db.execute(
                    "SELECT status FROM claims WHERE id = %s", (claim_id,)
                ).fetchone()["status"]
                == "stale"
            )

        fresh = review(client, auth, memory, review_interval_days=None)
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["status"] == "active"
        assert fresh.json()["review_due_at"] is None


def test_review_requires_scope_and_same_memory_claim(tenants, issue_key):
    first, second = tenants
    editor = credentials(issue_key, first, Scope.MEMORY_WRITE, Scope.MEMORY_READ)
    verifier = credentials(issue_key, first, Scope.MEMORY_VERIFY, Scope.MEMORY_READ)
    outsider = credentials(issue_key, second, Scope.MEMORY_VERIFY, Scope.MEMORY_READ)
    with TestClient(app) as client:
        old = create(client, editor, employer("Acme", "2026-01-01T00:00:00Z"))
        assert review(client, editor, old).status_code == 403
        assert review(client, outsider, old).status_code == 404
        assert (
            client.get(f"{MEMORIES}/{old['id']}/claims", **outsider).status_code == 404
        )
        assert review(client, verifier, old).status_code == 200

        path = f"{MEMORIES}/{old['id']}"
        edit = employer("Contoso", "2026-10-01T00:00:00Z")
        patched = client.patch(path, json=edit, **editor)
        assert patched.status_code == 200, patched.text
        assert [claim["status"] for claim in patched.json()["claims"]] == [
            "active",
            "review_due",
        ]
        candidate = patched.json()["claims"][1]

        unrelated = create(
            client, editor, employer("Unrelated", "2026-10-01T00:00:00Z")
        )
        assert (
            review(client, verifier, unrelated, claim=candidate["id"]).status_code
            == 404
        )
        closed = review(
            client, verifier, old, claim=candidate["id"], outcome="archived"
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "archived"
        assert review(client, verifier, old, claim=candidate["id"]).status_code == 409
        assert len(client.get(path, **editor).json()["claims"]) == 1
