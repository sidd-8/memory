# API reference

The live OpenAPI schema is at [the local API explorer](http://localhost:8000/docs).
Scoped requests send `Authorization: Bearer <key>` and the `workspace_id` and
`subject_id` query parameters. The subject endpoint takes both IDs in its
path. The console session endpoint accepts any valid key for the supplied
scope. Health checks are unauthenticated.

Responses include a generated `X-Request-ID`. Search reports the time spent
requesting an embedding as `Server-Timing: embedding;dur=…` in milliseconds.

| Method and path | Scope | Behavior |
| --- | --- | --- |
| `GET /healthz` | Public | Process liveness. |
| `GET /readyz` | Public | Database readiness. Returns 503 if PostgreSQL is unavailable. |
| `GET /v1/workspaces/{workspace_id}/subjects/{subject_id}` | `subject:read` | Subject ID, kind, and reference. |
| `POST /v1/memories` | `memory:write` | Creates a source-backed memory and optional candidate claims. Accepts `Idempotency-Key`. |
| `GET /v1/memories` | `memory:read` | Lists memory summaries. Pass `limit` and `after` from `next_after`. |
| `GET /v1/memories/{memory_id}` | `memory:read` | Returns memory detail, extraction status, and earlier revisions. Page history with `revision_limit` and `revision_before`. |
| `PATCH /v1/memories/{memory_id}` | `memory:write` | Writes a new revision. Content or replacement claims require a source. Accepts `Idempotency-Key`. |
| `POST /v1/memories/search` | `memory:read` | Searches by `query`, `category`, `status`, or `time_range`. Accepts `limit` and `token_budget`. Logs a retrieval event. |
| `GET /v1/memories/{memory_id}/claims` | `memory:read` | Lists claims, including historical claims. Pass optional `valid_at`, `limit`, and `offset`. |
| `POST /v1/memories/{memory_id}/verify` | `memory:verify` | Records verification, review, or archival. Accepts `claim_id`, `outcome`, an optional review interval or superseded claim ID, and `Idempotency-Key`. |
| `DELETE /v1/memories/{memory_id}` | `memory:delete` | Deletes the memory and its dependent records. Returns 204. |
| `GET /v1/entities/{entity_id}/graph` | `graph:read` | Returns a scoped neighborhood. Pass `depth` (1 or 2), `edge_limit`, or `valid_at`. |
| `GET /v1/console/session` | Any valid scoped key | Returns workspace, subject, agent, and granted scopes. |
| `GET /v1/console/overview` | `memory:read` | Returns recent writes, retrievals, and unresolved claims. |
| `GET /v1/console/agents` | `workspace:manage` | Lists workspace agents and activity for the connected subject. |
| `POST /v1/console/agents` | `workspace:manage` | Registers an agent in the workspace. |
| `GET /v1/console/keys` | `workspace:manage` | Lists key metadata for the selected subject. |
| `POST /v1/console/keys` | `workspace:manage` | Issues a subject-scoped key for a workspace agent and returns its secret once. |
| `DELETE /v1/console/keys/{key_id}` | `workspace:manage` | Revokes a subject key. An operator provisions and revokes management keys through the local CLI. |
| `PATCH /v1/console/settings` | `workspace:manage` | Updates workspace name and default retention and review intervals. |

## Errors

Errors use a `detail` field. Validation errors may return an array of
locations, messages, and codes. A 401 means the key is missing, invalid,
or revoked. A 403 means the key lacks permission or the requested scope.
A 404 means the resource is unavailable inside the authorized scope. A 409
means a state transition or idempotency key conflicts with an earlier
operation. A 422 means the request needs correction. A 503 indicates
temporary service or readiness failure. An HTTP intermediary may also
return statuses such as 429, 502, or 504.

## Retention and deletion

- A new memory inherits the workspace retention default when `expires_at` is
  omitted. An explicit timestamp overrides the default. An explicit `null`
  stores the memory without an expiration timestamp.
- Expired memories drop out of search, graph traversal, and the overview's
  writes and review queue. Direct memory reads, history, and listing can
  still show expired records until an authorized caller deletes them.
- Verification deadlines affect a claim's freshness. Overdue active claims
  appear as stale. Supersession closes an older validity interval and keeps
  earlier evidence and revisions available.
- Deletion removes the live memory, revisions, claims, source records,
  embedding, and relationship edges. Shared entities survive while other
  claims reference them. Retrieval event counts remain, with the deleted
  memory ID cleared. The service retains an opaque idempotency-key digest
  so a delayed retry cannot recreate a deleted memory.
- Compose stores PostgreSQL and Redis data in volumes across service
  restarts. Manage database backups separately from API deletion.

See [local development](development.md) for examples of verification, graph
traversal, and the dinner overlap search.
