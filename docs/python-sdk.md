# Python SDK

The `agent-memory-sdk` package provides a synchronous, typed client for the
Agent Memory API. Each client uses one bearer key for one workspace and subject.

## Quickstart

From the repository root, start the local stack and install the SDK into a
separate Python 3.13 environment.

```sh
docker compose up --build --wait

SDK_ENV=$(mktemp -d)
uv venv --python 3.13 "$SDK_ENV"
SDK_PYTHON="$SDK_ENV/bin/python"
uv pip install --python "$SDK_PYTHON" ./packages/sdk-python
```

Provision a workspace, a subject, and two agents. The commands capture each
secret in a shell variable when the server issues it.

```sh
identity() { docker compose exec -T api python -m apps.api.identity "$@"; }
json_field() { "$SDK_PYTHON" -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

WORKSPACE=$(identity create-workspace --name "SDK quickstart" --owner sdk-demo)
export AGENT_MEMORY_BASE_URL=http://127.0.0.1:8000
AGENT_MEMORY_WORKSPACE_ID=$(printf '%s' "$WORKSPACE" | json_field workspace_id)
USER_ID=$(printf '%s' "$WORKSPACE" | json_field user_id)
AGENT_MEMORY_SUBJECT_ID=$(identity create-subject "$AGENT_MEMORY_WORKSPACE_ID" \
  --kind project --reference inventory | json_field subject_id)
export AGENT_MEMORY_WORKSPACE_ID AGENT_MEMORY_SUBJECT_ID

WRITER_ID=$(identity register-agent "$AGENT_MEMORY_WORKSPACE_ID" \
  --member-id "$USER_ID" --name Writer | json_field agent_id)
READER_ID=$(identity register-agent "$AGENT_MEMORY_WORKSPACE_ID" \
  --member-id "$USER_ID" --name Reader | json_field agent_id)
WRITER_KEY=$(identity issue-key "$AGENT_MEMORY_WORKSPACE_ID" \
  --agent-id "$WRITER_ID" --subject-id "$AGENT_MEMORY_SUBJECT_ID" \
  --scope memory:write | json_field api_key)
READER_KEY=$(identity issue-key "$AGENT_MEMORY_WORKSPACE_ID" \
  --agent-id "$READER_ID" --subject-id "$AGENT_MEMORY_SUBJECT_ID" \
  --scope memory:read | json_field api_key)
```

Run the example as the writer. After that process exits, restart the API and
run the reader with its own key.

```sh
MEMORY_ID=$(AGENT_MEMORY_API_KEY="$WRITER_KEY" "$SDK_PYTHON" examples/python_quickstart.py write)

docker compose restart api
docker compose up --wait api

AGENT_MEMORY_API_KEY="$READER_KEY" "$SDK_PYTHON" examples/python_quickstart.py read "$MEMORY_ID"
```

The reader prints the inventory memory, its source reference and ID, and the
number of memories returned by text search. The example works with the default
full-text search. Configure Ollama for semantic search and extraction.

## Integrate with an agent

Use `MemoryClient.from_env()` with the variables above and the agent's
`AGENT_MEMORY_API_KEY`. The constructor also accepts `base_url`,
`workspace_id`, `subject_id`, and `api_key` explicitly. Store the key in your
application's secret store or inject it through the environment. Use HTTPS for
remote API deployments.

```python
from datetime import datetime
from agent_memory import MemoryClient, SearchRequest, TimeRange

with MemoryClient.from_env() as client:
    result = client.search(
        SearchRequest(
            time_range=TimeRange(
                starts_at=datetime.fromisoformat("2026-11-20T19:30:00-05:00"),
                ends_at=datetime.fromisoformat("2026-11-20T20:30:00-05:00"),
            ),
            status="confirmed",
        )
    )
    for hit in result.evidence:
        print(hit.memory_id, hit.source_id)
```

Use `MemoryCreate`, `MemoryPatch`, `ClaimReview`, and `SearchRequest` for typed
requests. The client decodes IDs as UUIDs and timestamps as timezone-aware
datetimes. Treat returned content as evidence from the recorded source.

| Method | Result | Scope |
| --- | --- | --- |
| `add(memory)` | New memory snapshot | `memory:write` |
| `list(limit=…, after=…)` | Memory summaries and `next_after` | `memory:read` |
| `get(id, revision_limit=…, revision_before=…)` | Detail and earlier revisions | `memory:read` |
| `update(id, patch)` | Updated snapshot | `memory:write` |
| `search(query_or_request)` | Ranked evidence with source IDs | `memory:read` |
| `list_claims(id, valid_at=…, limit=…, offset=…)` | Current or historical claims and sources | `memory:read` |
| `verify(id, review)` | Reviewed claim | `memory:verify` |
| `delete(id)` | Deletes the record and returns `None` | `memory:delete` |
| `graph(entity_id, depth=…, edge_limit=…, valid_at=…)` | Scoped entity neighborhood | `graph:read` |

For pagination, pass `next_after` from `list()` or `next_revision_before` from
`get()` into the next request. Continue until the cursor is `None`. A
`MemoryPatch` sends only supplied fields, including fields explicitly set to
`None` where the API allows clearing.

## Retries and errors

The default is a 15-second HTTP timeout and up to two retries. GET requests
and the keyed writes `add`, `update`, and `verify` retry transport failures
and HTTP 429, 502, 503, and 504. Each write generates one idempotency key and
reuses it across its attempts. Pass your own stable `idempotency_key` to
resume the same write across process restarts. The client honors
`Retry-After` up to 30 seconds and returns the error for longer waits.
Search records retrieval activity, and deletion can succeed before a response
arrives, so the client makes a single attempt for those operations.

| Exception | Meaning |
| --- | --- |
| `AuthenticationError` | 401. Key missing, invalid, or revoked. |
| `PermissionDenied` | 403. Key lacks the required permissions or scope. |
| `NotFound` | 404. Resource unavailable in this scope. |
| `Conflict` | 409. Invalid state transition or idempotency conflict. |
| `RequestValidationError` | 422. Request rejected. `issues` includes field locations and messages when supplied. |
| `RateLimitError` | 429. Request rate limited. |
| `APIError` | Other unexpected HTTP status. |
| `TransportError` | The HTTP exchange failed before a complete response. |
| `ProtocolError` | A successful response failed SDK validation. |
| `AgentMemoryError` | Base class for SDK request errors. |

HTTP errors expose `status_code`, `detail`, and `request_id` when the server
provides a request ID. Invalid local model data can raise Pydantic's
`ValidationError`. Invalid client configuration can raise `ValueError`.

See the [API reference](api.md) for endpoint parameters and retention behavior.
