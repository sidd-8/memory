import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
import pytest
from agent_memory import (
    APIError,
    AuthenticationError,
    Conflict,
    MemoryClient,
    MemoryCreate,
    NotFound,
    PermissionDenied,
    ProtocolError,
    RateLimitError,
    RequestValidationError,
    SourceInput,
    TransportError,
)

WORKSPACE, SUBJECT, RECORD, SOURCE, AGENT = [str(uuid4()) for _ in range(5)]
NOW = "2026-11-18T15:00:00Z"


def client(handler, max_retries=2):
    return MemoryClient(
        base_url="https://memory.example/prefix",
        workspace_id=WORKSPACE,
        subject_id=SUBJECT,
        api_key="example-test-token",
        transport=httpx2.MockTransport(handler),
        max_retries=max_retries,
    )


def snapshot():
    return {
        "id": RECORD,
        "workspace_id": WORKSPACE,
        "subject_id": SUBJECT,
        "category": "project",
        "content": "Inventory uses PostgreSQL.",
        "expires_at": None,
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
        "source": {
            "id": SOURCE,
            "origin": "test",
            "source_ref": "note:1",
            "observed_at": NOW,
        },
        "claims": [],
        "changed_by": AGENT,
    }


def test_write_retries_with_identical_key_body_and_scope():
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx2.Response(503, headers={"Retry-After": "0"})
        return httpx2.Response(201, json=snapshot())

    with client(handler) as sdk:
        result = sdk.add(
            MemoryCreate(
                category="project",
                content="Inventory uses PostgreSQL.",
                source=SourceInput(
                    origin="test",
                    source_ref="note:1",
                    observed_at=datetime(2026, 11, 18, tzinfo=UTC),
                ),
            )
        )

    assert result.id == UUID(RECORD)
    assert result.source.id == UUID(SOURCE)
    assert len(attempts) == 2
    first, retry = attempts
    assert first.headers["Idempotency-Key"] == retry.headers["Idempotency-Key"]
    assert UUID(first.headers["Idempotency-Key"])
    assert first.content == retry.content
    assert "expires_at" not in json.loads(first.content)
    assert first.headers["Authorization"] == "Bearer example-test-token"
    assert first.url.path == "/prefix/v1/memories"
    assert first.url.params["workspace_id"] == WORKSPACE
    assert first.url.params["subject_id"] == SUBJECT


def test_safe_read_retries_transport_and_keeps_cursor(monkeypatch):
    attempts = []
    monkeypatch.setattr("agent_memory.client.sleep", lambda _: None)

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx2.ConnectError("temporary", request=request)
        return httpx2.Response(200, json={"items": [], "next_after": RECORD})

    with client(handler) as sdk:
        page = sdk.list(after=RECORD, limit=3)
    assert len(attempts) == 2
    assert attempts[1].method == "GET"
    assert attempts[1].url.params["limit"] == "3"
    assert attempts[1].url.params["after"] == RECORD
    assert page.next_after == UUID(RECORD)


@pytest.mark.parametrize("operation", ["search", "delete"])
def test_search_and_delete_are_not_retried(operation):
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx2.ReadTimeout("example-test-token", request=request)

    with client(handler) as sdk, pytest.raises(TransportError) as exc:
        getattr(sdk, operation)(RECORD)
    assert len(attempts) == 1
    assert "example-test-token" not in str(exc.value)


@pytest.mark.parametrize(
    ("code", "kind"),
    (
        (401, AuthenticationError),
        (403, PermissionDenied),
        (404, NotFound),
        (409, Conflict),
        (422, RequestValidationError),
        (429, RateLimitError),
        (503, APIError),
    ),
)
def test_http_errors_are_typed(code, kind):
    detail = (
        [
            {
                "loc": ["body", "claim_id"],
                "msg": "Invalid UUID",
                "type": "uuid_parsing",
                "input": "private payload",
            }
        ]
        if code == 422
        else "Test response"
    )
    with (
        client(
            lambda _: httpx2.Response(
                code,
                json={"detail": detail},
                headers={"x-request-id": "request-1", "retry-after": "60"},
            )
        ) as sdk,
        pytest.raises(kind) as exc,
    ):
        sdk.get(RECORD)
    assert exc.value.status_code == code
    assert exc.value.request_id == "request-1"
    if code == 422:
        assert exc.value.issues[0].location == ("body", "claim_id")
        assert "private payload" not in str(exc.value)


def test_redirects_do_not_forward_the_key():
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx2.Response(
            302, headers={"location": "https://other.example/memory"}
        )

    with client(handler) as sdk, pytest.raises(APIError) as exc:
        sdk.get(RECORD)
    assert exc.value.status_code == 302
    assert len(attempts) == 1


def test_invalid_response_raises_protocol_error():
    with client(lambda _: httpx2.Response(200, json={"private": "payload"})) as sdk:
        with pytest.raises(ProtocolError, match="Invalid MemoryDetail response"):
            sdk.get(RECORD)
