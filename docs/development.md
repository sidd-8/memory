# Local development

Use Docker with Compose and Buildx. For host-side checks, use uv with Python
3.13 and pnpm 10.33.2 with Node.js 24.

Run `docker compose up --build --wait` from the repository root. The default
host ports are 8000 for the API and 3000 for the web app. To override them, copy
`.env.example` to `.env` and edit the values. The local database and Redis are
available to services on the Compose network.

## Checks

```sh
uv sync --locked --all-packages
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync python -m pytest
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web lint
pnpm --dir apps/web format:check
bash tests/smoke.sh
docker compose run --build --rm test
```

The smoke test requires a running stack and checks API readiness, the web
console, the pgvector migration, Redis, and worker registration.
The Compose test service runs the PostgreSQL integration tests in temporary schemas.

## Local identity provisioning

Run these commands as a trusted operator with access to the local Compose stack.
Each command returns JSON. Use the returned IDs in the next command.

```sh
docker compose exec api python -m apps.api.identity create-workspace \
  --name Demo --owner demo-owner
docker compose exec api python -m apps.api.identity register-agent "$WORKSPACE_ID" \
  --member-id "$USER_ID" --name DemoAgent
docker compose exec api python -m apps.api.identity create-subject "$WORKSPACE_ID" \
  --kind project --reference demo-project
docker compose exec api python -m apps.api.identity issue-key "$WORKSPACE_ID" \
  --agent-id "$AGENT_ID" --subject-id "$SUBJECT_ID" --scope subject:read
```

`create-workspace` adds the owner as the first member. Use `add-member
WORKSPACE_ID --user IDENTIFIER` to add a member. Subjects support user, project,
and team identifiers. An agent can hold multiple keys. Each key authorizes one
workspace and subject. Repeat `--scope` for additional permissions.

Capture the returned key securely when issued. PostgreSQL stores its SHA-256
digest. Send the key as a bearer token to
`GET /v1/workspaces/{workspace_id}/subjects/{subject_id}`. Use `revoke-key
WORKSPACE_ID KEY_ID` to revoke it. The API returns 401 for missing, invalid, or
revoked keys and 403 for a valid key without the requested permissions.

## Connect the web console

Open the [local console](http://localhost:3000). Enter the workspace ID, subject
ID, and an agent API key. The browser keeps the key in memory for that session.
Use `memory:read` for browsing and retrieval, `memory:write` for edits,
`memory:verify` for claim reviews, `memory:delete` for deletion, and
`graph:read` for the graph.

For workspace administration, use the local `issue-key` command above with
`--scope workspace:manage` and any other permissions you need. This scope
allows workspace-wide agent registration and default-policy changes. Key
issuance and revocation in the console are limited to the connected subject.
Workspace management keys are issued and revoked through local provisioning.
Retention defaults apply to new records that omit `expires_at`. Review
defaults apply on the next verification that omits `review_interval_days`.

Run the web app outside Docker with `pnpm --dir apps/web dev`. The server
forwards requests to the local API at port 8000. Set `API_INTERNAL_URL` to
override that address.

## Memory records

The [local API explorer](http://localhost:8000/docs) exposes `POST` and `GET`
on `/v1/memories`, and `GET`, `PATCH`, and `DELETE` on
`/v1/memories/{memory_id}`. Pass `workspace_id` and `subject_id` as query
parameters. Issue a bearer key with `memory:read`, `memory:write`, or
`memory:delete` for the operation you want to perform.

A create request contains a category, content, and source. For example:

```json
{
  "category": "semantic",
  "content": "Sam works for Acme.",
  "source": {
    "origin": "conversation",
    "source_ref": "conversation:123",
    "observed_at": "2026-11-17T15:00:00Z"
  },
  "claims": [
    {
      "predicate": "employer",
      "subject_entity": {"kind": "person", "reference": "sam"},
      "object_entity": {"kind": "company", "reference": "acme"}
    }
  ]
}
```

Claims are optional. Each submitted batch shares the supplied evidence source
and starts as candidates or flags overlapping conflicts for review. A patch
that changes content or submits replacement claims requires a source. Omitting
`claims` preserves the current claims. Sending a claims list replaces
unverified candidates and preserves verified claims for explicit review.
Earlier revisions retain their original evidence.

Use `limit` and `after` for listing, and `revision_limit` and
`revision_before` for history. The API returns the next cursor when more
results exist. Include an `Idempotency-Key` header on create or patch to retry
the same request safely. Reusing the key for a different request returns 409.
Deletion removes the memory and its history, evidence, embedding, and edges.
Entities used by other memories are kept. Retrying an idempotent write after
its memory was deleted returns 409.

## Claim verification and history

Use `POST /v1/memories/{memory_id}/verify` with a `memory:verify` key and the
workspace and subject query parameters. Send `claim_id` and an `outcome` of
`verified`, `needs_review`, or `archived`. Supply an `Idempotency-Key` for a
retryable review. The result records the reviewer and review time.

Verification sets `verified_at` and a review deadline. The default is 90 days
for employment and roles, 60 for project claims, 180 for preferences and
procedural claims, and 7 for working context. Other claims have no default
deadline. Set `review_interval_days` to override the default or send
`null` to omit the deadline. Overdue active claims appear as stale immediately
in reads, and the worker records that change during its scheduled pass.

For the same subject entity and predicate, a different value with an
overlapping validity interval is flagged as `review_due`. To confirm a
replacement, supply `supersedes_claim_id` when verifying the replacement.
Its `valid_from` becomes the previous claim's exclusive `valid_to`.
`GET /v1/memories/{memory_id}/claims?valid_at=...` reads claims that applied
at a timestamp, including superseded or archived claims. Claim responses show
when a claim applied and when the system recorded, reviewed, verified, or
superseded it. Memory detail includes prior revisions as they were recorded.

## Extraction and search

The following commands start Homebrew Ollama and download the two models for
this Colima setup.

```sh
brew services run ollama
ollama pull qwen3:4b
ollama pull qwen3-embedding:0.6b
```

Set `OLLAMA_URL=http://host.lima.internal:11434` in `.env` and restart the
Compose services with `docker compose up --build --wait`. The worker indexes
memory content asynchronously and picks up pending work from PostgreSQL.
Set `"extract_claims": true` on create or patch to request extraction. The
memory detail endpoint reports whether extraction is pending, complete, or
rejected. Extracted claims carry the source ID and model name, and enter the
revision history as candidates.

Use `POST /v1/memories/search` with `memory:read` and the workspace and subject
query parameters. The body accepts `query`, an optional `limit`, and an
optional `token_budget`. Search combines PostgreSQL full-text ranking with
pgvector similarity and falls back to full-text search when Ollama is
unavailable. Expired records are excluded.

Set `MEMORY_CONTEXT_TOKEN_BUDGET` in `.env` to change the server cap
(default 2048). The API caps the compact UTF-8 bytes of the returned evidence
array as a conservative token bound. Treat evidence text as untrusted data.

## Time windows and relationship graphs

Memory writes accept `starts_at`, `ends_at`, and `timezone` together, with an
optional `state`. Use timestamps with explicit offsets and an IANA timezone.
The API stores and returns the instants in UTC and preserves the timezone.
Confirming or rescheduling a confirmed memory requires a source with
`trust_class` of `user_confirmed` or `authoritative`.

For a dinner conflict, have one agent save a `commitment` memory for November
20, 2026, from 7–8 p.m. New York time with `state` set to `confirmed`. Restart the API with
`docker compose restart api`. Then have another agent with `memory:read`
for the same workspace and subject send this body to `POST /v1/memories/search`.

```json
{
  "time_range": {
    "starts_at": "2026-11-20T19:30:00-05:00",
    "ends_at": "2026-11-20T20:30:00-05:00"
  },
  "status": "confirmed"
}
```

A window-only search uses the timestamps directly and returns each overlap
with its source ID. An interval that starts exactly at another interval's end
counts as adjacent. Patch the dinner to `state: cancelled` to remove it from
confirmed overlap results.

Use `GET /v1/entities/{entity_id}/graph` with a `graph:read` key and the usual
workspace and subject query parameters. Get entity IDs from relationship
claims. The graph returns one hop by default, or two with `depth=2`. Use
`edge_limit` to cap the result and `valid_at` to inspect the graph at a past
instant. Edges include claim and source IDs, validity dates, and claim status.

## Migrations and shutdown

Use `docker compose run --rm migrate alembic current` to see the current
revision. After updating migration files, rebuild the backend and run
`docker compose up --build --wait` to apply changes.

Use `docker compose down` to stop the stack and retain local data.
