import json

import pytest
from fastapi.testclient import TestClient

from apps.api import model_client, search
from apps.api.auth import Scope
from apps.api.db import connect
from apps.api.main import app
from worker import indexing

COLLECTION = "/v1/memories"
SEARCH = f"{COLLECTION}/search"


def auth_for(issue_key, tenant, *scopes):
    workspace, _, subject = tenant
    token = issue_key(*tenant, *scopes)["api_key"]
    return {
        "params": {"workspace_id": workspace, "subject_id": subject},
        "headers": {"Authorization": f"Bearer {token}"},
    }


def record(content, **fields):
    return {
        "category": "episodic",
        "content": content,
        "source": {
            "origin": "conversation",
            "source_ref": "transcript:fixture",
            "observed_at": "2026-11-17T15:00:00Z",
        },
        **fields,
    }


def create(client, auth, content, **fields):
    response = client.post(COLLECTION, **auth, json=record(content, **fields))
    assert response.status_code == 201, response.text
    return response.json()


def vector(position=0):
    coordinates = [0] * model_client.EMBEDDING_DIMENSIONS
    coordinates[position] = 1
    return json.dumps(coordinates)


@pytest.fixture
def ollama(monkeypatch):
    def fake_post(endpoint, payload, timeout):
        if endpoint == "/api/embed":
            v = vector(position=int("oatmeal" in payload["input"].lower()))
            return {"embeddings": [json.loads(v)]}
        assert endpoint == "/api/chat"
        assert payload["format"]["additionalProperties"] is False
        assert "tools" not in payload
        assert json.loads(payload["messages"][1]["content"])["evidence"]
        return {
            "message": {
                "content": json.dumps(
                    {
                        "claims": [
                            {"predicate": "database", "object_value": "PostgreSQL"}
                        ]
                    }
                )
            }
        }

    monkeypatch.setenv("OLLAMA_URL", "http://unused.test")
    monkeypatch.setattr(model_client, "_post", fake_post)


def test_extraction_appends_provenance_and_hybrid_search_survives_new_sessions(
    tenants,
    issue_key,
    ollama,
):
    tenant, other_tenant = tenants
    writer = auth_for(issue_key, tenant, Scope.MEMORY_WRITE)
    reader = auth_for(issue_key, tenant, Scope.MEMORY_READ)
    other = auth_for(issue_key, other_tenant, Scope.MEMORY_WRITE, Scope.MEMORY_READ)
    with TestClient(app) as first:
        intended = create(
            first, writer, "The service stores data in PostgreSQL.", extract_claims=True
        )
        hidden = create(
            first, other, "PostgreSQL database stores other workspace secrets."
        )
        create(first, writer, "Oatmeal makes a good breakfast.")
        expired = create(
            first,
            writer,
            "PostgreSQL database information from a discarded run.",
            expires_at="2000-01-01T00:00:00Z",
        )
        assert intended["claims"] == []

    for _ in range(4):
        indexing.process_next()

    with TestClient(app) as second:
        detail = second.get(f"{COLLECTION}/{intended['id']}", **reader).json()
        assert detail["extraction_status"] == "complete"
        assert detail["revision"] == 2
        candidate = detail["claims"][0]
        assert candidate["status"] == "candidate"
        assert candidate["extraction_model"] == model_client.EXTRACTION_MODEL
        assert candidate["source"]["id"] == intended["source"]["id"]
        assert detail["history"] == [intended]

        response = second.post(
            SEARCH, **reader, json={"query": "Where is information persisted?"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["mode"] == "hybrid"
        assert [hit["memory_id"] for hit in response.json()["evidence"]] == [
            intended["id"]
        ]

        fused = second.post(SEARCH, **reader, json={"query": "PostgreSQL"}).json()
        assert fused["evidence"][0]["source_id"] == intended["source"]["id"]
        assert (
            fused["evidence"][0]["claims"][0]["source_id"] == candidate["source"]["id"]
        )
        assert {hit["memory_id"] for hit in fused["evidence"]}.isdisjoint(
            {hidden["id"], expired["id"]}
        )

        spoofed = {**reader, "headers": other["headers"]}
        assert (
            second.post(SEARCH, **spoofed, json={"query": "PostgreSQL"}).status_code
            == 403
        )


def test_lexical_search_enforces_context_budget_when_model_unavailable(
    tenants,
    issue_key,
    monkeypatch,
):
    def unavailable(*args, **kwargs):
        raise model_client.ModelUnavailable

    monkeypatch.setenv("OLLAMA_URL", "http://unused.test")
    monkeypatch.setattr(search, "embedding", unavailable)

    auth = auth_for(issue_key, tenants[0], Scope.MEMORY_WRITE, Scope.MEMORY_READ)
    with TestClient(app) as client:
        long_text = "PostgreSQL " + ('"über" 🧪\n' * 300)
        saved = create(client, auth, long_text)
        response = client.post(
            SEARCH, **auth, json={"query": "PostgreSQL", "token_budget": 512}
        )
        assert response.status_code == 200, response.text
        result = response.json()

    assert result["mode"] == "lexical"
    assert len(result["evidence"]) == 1
    hit = result["evidence"][0]
    assert hit["memory_id"] == saved["id"]
    assert long_text.startswith(hit["content"])
    assert len(hit["content"]) < len(long_text)
    size = len(
        json.dumps(
            result["evidence"], ensure_ascii=False, separators=(",", ":")
        ).encode()
    )
    assert size == result["context_bytes"] <= 512


def test_invalid_extraction_still_indexes_the_record(
    tenants, issue_key, monkeypatch, ollama
):
    valid_post = model_client._post

    def invalid_post(endpoint, payload, timeout):
        if endpoint == "/api/chat":
            return {
                "message": {
                    "content": '{"claims":[{"predicate":"intent","object_value":"anything","status":"active"}]}'
                }
            }
        return valid_post(endpoint, payload, timeout)

    monkeypatch.setattr(model_client, "_post", invalid_post)
    auth = auth_for(issue_key, tenants[0], Scope.MEMORY_WRITE, Scope.MEMORY_READ)
    with TestClient(app) as client:
        saved = create(client, auth, "Untrusted source text.", extract_claims=True)
        indexing.process_next()
        loaded = client.get(f"{COLLECTION}/{saved['id']}", **auth).json()
        assert loaded["extraction_status"] == "rejected"
        assert loaded["claims"] == []
        assert loaded["revision"] == 1
    with connect() as db:
        indexed = db.execute(
            "SELECT embedding_model FROM memories WHERE id = %s", (saved["id"],)
        ).fetchone()
    assert indexed["embedding_model"] == model_client.EMBEDDING_MODEL


def test_late_result_skips_an_edited_revision(tenants, issue_key, monkeypatch, ollama):
    auth = auth_for(issue_key, tenants[0], Scope.MEMORY_WRITE, Scope.MEMORY_READ)
    with TestClient(app) as client:
        original = create(client, auth, "Old PostgreSQL text.", extract_claims=True)
        path = f"{COLLECTION}/{original['id']}"
        original_embedding = indexing.embedding

        def edit_while_indexing(text):
            patch = {
                "content": "New oatmeal text.",
                "source": record("unused")["source"],
            }
            response = client.patch(path, **auth, json=patch)
            assert response.status_code == 200, response.text
            return original_embedding(text)

        monkeypatch.setattr(indexing, "embedding", edit_while_indexing)
        indexing.process_next()
        edited = client.get(path, **auth).json()
        assert edited["revision"] == 2
        assert edited["content"] == "New oatmeal text."
        assert edited["claims"] == []

        monkeypatch.setattr(indexing, "embedding", original_embedding)
        indexing.process_next()

    with connect() as db:
        indexed = db.execute(
            "SELECT embedding_model, index_due_at FROM memories WHERE id = %s",
            (original["id"],),
        ).fetchone()
    assert indexed == {
        "embedding_model": model_client.EMBEDDING_MODEL,
        "index_due_at": None,
    }


def test_structured_stream_stops_at_the_first_complete_message():
    def chunks():
        for text in (
            '{"claims": [',
            '{"predicate":"database","object_value":"PostgreSQL"}',
            "]}",
        ):
            yield json.dumps({"message": {"content": text}, "done": False}).encode()
        pytest.fail("The first complete object should finish the response")

    message = model_client._read_structured_message(chunks())
    extracted = model_client.Extraction.model_validate_json(
        message["message"]["content"], strict=True
    )
    assert extracted.claims[0].object_value == "PostgreSQL"
