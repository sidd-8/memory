# Private beta runbook

## Local release checks

Run the [development checks](development.md#checks), then run the release
evaluation with a working Ollama endpoint. The evaluation starts and removes
a disposable Compose project with synthetic data.

```sh
docker compose up --build --wait
uv sync --locked --all-packages
uv run --no-sync python scripts/check_release.py
```

The evaluation seeds 20 records with five curated queries each and 20
distractors. Each query has one labeled relevant record. It uses the actual
embedding model and calculates recall@5 over the 100 queries. It measures
sequential end-to-end local API latency and subtracts the `embedding` duration
reported in `Server-Timing`. The run also checks that a second agent can read
after an API restart, drills database backup and restore, and checks deletion.
The default Ollama address is for Colima. Use `--ollama-url` to point the
disposable worker and API to another reachable address.

## Release gate results

The November 20, 2026 run used an Apple M4 with 16 GiB of RAM, a Colima VM
with four CPUs and about 5.8 GiB of RAM, and the local Qwen3 embedding model.

| Gate | Result |
| --- | --- |
| Persistence | A second agent read the same record after the API restarted. |
| Isolation and temporal correctness | The 43-test suite passed, including workspace and subject isolation, supersession, stale claims, DST, adjacency, and cancellation. |
| Retrieval quality | 100 of 100 queries found their labeled record in the first five results. Target ≥90%. |
| Latency | P95 was 15.91 ms excluding embedding time. Target <700 ms. |
| Deletion | Direct lookup and retrieval omitted the deleted record. Integration tests also checked embedding and graph cleanup. |
| Recovery | A custom-format dump restored into a fresh database with the expected schema revision and all 40 records. |
| Pilot usability | Open. The five-developer exercise requires a private deployment and pilot participants. |

The npm production audit and PyPI release advisory checks found no active
advisories in the checked runtime dependencies on that date.

## Logs and metrics

The API logs one JSON record for each application request with the route
template, status, duration, and generated request ID. The ID appears as
`X-Request-ID` in the API and console proxy responses. The log omits raw paths,
query parameters, keys, and memory contents. Health probes log failures.

Scrape `GET /metrics` on the private API listener for HTTP request totals and
duration counts and sums by method, route template, and status. Counters
reset when the process restarts. Use the release evaluation for P95 latency.
Keep the metrics endpoint on the operator network.

## Backup and restore

Create a PostgreSQL custom-format dump before upgrades and on the deployment's
regular backup schedule. Set the schedule, encrypted storage, and retention
for the pilot environment. The commands below use the local Compose database.

```sh
umask 077
BACKUP_DIR=$(mktemp -d)
BACKUP="$BACKUP_DIR/memory.dump"
docker compose exec -T postgres pg_dump -U memory -Fc memory > "$BACKUP"

docker compose exec -T postgres createdb -U memory -T template0 memory_restore
docker compose exec -T postgres pg_restore -U memory --exit-on-error \
  --single-transaction -d memory_restore < "$BACKUP"
docker compose exec -T postgres psql -U memory -d memory_restore \
  -c 'SELECT version_num FROM alembic_version'
```

Use a fresh restore database with pgvector available. Verify the expected
schema revision, representative records and their evidence, and scoped
access before using the restored database. Handle backup copies separately
from live API deletion.

## Release and rollback

1. Record the deployed image identifiers and Alembic revision. Take a
   pre-upgrade backup. Preserve the previous images.
2. Restore the backup into a separate database and test the new images and
   migrations against it. Check readiness, the SDK quickstart, and a retrieval.
3. If a release fails, pause writes and preserve the current database for
   reconciliation. Restore the pre-upgrade backup into a fresh database and
   start the matching previous images against it with the API and worker
   pointed to that database.
4. Confirm readiness and the previous Alembic revision. Reconcile deletions,
   revoked keys, and writes made after the backup before reopening access.
   Keep the failed database available for recovery decisions.

## Pilot acceptance

Use a private deployment with TLS and operator-managed keys. Give five
developers separate, scoped test credentials and the [SDK quickstart](python-sdk.md).
Ask each to write a source-backed memory and retrieve it with a second agent
without assistance. Record completion and blockers. Keep the pilot gate open
until all five complete it.

The initial evaluation uses synthetic records and a single local machine.
The local Compose configuration uses development credentials and loopback
ports. Expiration hides records from retrieval while direct reads remain
available until deletion. Hosting, backup scheduling, and pilot onboarding
are deployment tasks.
