# Agent Memory

Agent Memory is a persistent memory API for AI agents. Agents with access to
the same workspace and subject can write and retrieve source-backed memories
across sessions. Each API key grants specific operations for one subject.

## What works today

- Create, read, update, and delete memories with provenance and revision history.
- Extract candidate claims asynchronously when a write opts in.
- Verify claims, flag conflicting evidence for review, and preserve prior
  claim intervals when a reviewer confirms a replacement.
- Search with PostgreSQL full-text ranking and pgvector similarity. Results
  include source IDs and respect workspace and subject permissions.
- Traverse scoped entity relationships and find memories that overlap a time
  window, including confirmed commitments.
- Review activity, manage agents and keys, edit memories, and test retrieval
  from the web console.
- Run the FastAPI service, Redis worker, PostgreSQL database, and Next.js
  console together in Docker Compose.

The [Python SDK quickstart](docs/python-sdk.md) runs a cross-agent memory write
and read. See the [API reference](docs/api.md) for endpoints and retention.
The [private beta runbook](docs/private-beta.md) covers release checks, backup
and restore, and pilot acceptance.

## Run locally

Install Docker with Compose and Buildx, then run this from the repository root.

```sh
docker compose up --build --wait
```

Open the [API explorer](http://localhost:8000/docs) or the
[web console](http://localhost:3000). Compose applies database migrations at
startup. Use `docker compose down` to stop the stack and retain local data.

The default setup supports full-text search. For semantic search and candidate
extraction, configure the two local Ollama models. Follow the
[development guide](docs/development.md) to configure Ollama, provision scoped
API keys, or make a first memory write.

## Test

With the stack running:

```sh
bash tests/smoke.sh
docker compose run --build --rm test
```

The integration tests use PostgreSQL and a simulated model provider.
