import logging
import os
import time

import psycopg
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue

from apps.api.claim_lifecycle import mark_overdue_claims
from apps.api.db import connect
from apps.api.memory_models import ClaimInput, MemorySnapshot
from apps.api.memory_store import insert_claim, lock_subject, save_revision
from apps.api.model_client import (
    EMBEDDING_MODEL,
    EXTRACTION_MODEL,
    InvalidExtraction,
    ModelUnavailable,
    embedding,
    extract_claims,
    model_enabled,
)

logger = logging.getLogger(__name__)


def _reserve():
    with connect() as db:
        return db.execute("""
            WITH due AS (
                SELECT workspace_id, subject_id, id FROM memories
                WHERE index_due_at <= statement_timestamp()
                    AND (expires_at IS NULL OR expires_at > statement_timestamp())
                ORDER BY index_due_at, workspace_id, subject_id, id
                LIMIT 1 FOR UPDATE SKIP LOCKED
            ), leased AS (
                UPDATE memories AS m
                SET index_due_at = clock_timestamp() + interval '4 minutes',
                    index_attempts = LEAST(index_attempts + 1, 15)
                FROM due
                WHERE (m.workspace_id, m.subject_id, m.id) = (due.workspace_id, due.subject_id, due.id)
                RETURNING m.workspace_id, m.subject_id, m.id, m.revision,
                    m.index_due_at, m.index_attempts, m.extraction_status
            )
            SELECT leased.*, v.snapshot FROM leased
            JOIN current_memory_snapshots AS v
              ON (v.workspace_id, v.subject_id, v.memory_id, v.revision)
               = (leased.workspace_id, leased.subject_id, leased.id, leased.revision)
        """).fetchone()


def _lease(job):
    return tuple(
        job[field]
        for field in ("workspace_id", "subject_id", "id", "revision", "index_due_at")
    )


def _defer(job):
    with connect() as db:
        db.execute(
            """
            UPDATE memories SET index_due_at = clock_timestamp() + %s * interval '1 second'
            WHERE (workspace_id, subject_id, id, revision, index_due_at) = (%s, %s, %s, %s, %s)
            """,
            (min(120, 2 ** job["index_attempts"]), *_lease(job)),
        )


def _claim_key(claim: ClaimInput):
    subject = claim.subject_entity
    target = claim.object_entity
    return (
        claim.predicate,
        (subject.kind, subject.reference) if subject else None,
        claim.object_value,
        (target.kind, target.reference) if target else None,
        claim.valid_from,
        claim.valid_to,
    )


def _finish(job, snapshot, vector, extracted, status):
    scope = (snapshot.workspace_id, snapshot.subject_id)
    with connect() as db:
        lock_subject(db, scope)
        available = db.execute(
            """
            SELECT id FROM memories
            WHERE (workspace_id, subject_id, id, revision, index_due_at) = (%s, %s, %s, %s, %s)
                AND (expires_at IS NULL OR expires_at > statement_timestamp())
            """,
            _lease(job),
        ).fetchone()
        if available is None:
            return

        claims_by_key = {_claim_key(claim) for claim in snapshot.claims}
        new_claims = []
        for claim in extracted:
            key = _claim_key(claim)
            if key not in claims_by_key and len(snapshot.claims) + len(new_claims) < 32:
                new_claims.append(
                    insert_claim(
                        db, scope, snapshot.id, snapshot.source, claim, EXTRACTION_MODEL
                    )
                )
                claims_by_key.add(key)

        row = db.execute(
            """
            UPDATE memories
            SET embedding = %s::public.vector, embedding_model = %s,
                index_due_at = NULL, index_attempts = 0, extraction_status = %s,
                revision = revision + %s,
                updated_at = CASE WHEN %s THEN clock_timestamp() ELSE updated_at END
            WHERE workspace_id = %s AND subject_id = %s AND id = %s
            RETURNING revision, updated_at
            """,
            (
                vector,
                EMBEDDING_MODEL,
                status,
                int(bool(new_claims)),
                bool(new_claims),
                *scope,
                snapshot.id,
            ),
        ).fetchone()
        if new_claims:
            save_revision(
                db,
                snapshot.model_copy(
                    update={
                        **row,
                        "claims": snapshot.claims + new_claims,
                    }
                ),
            )


def process_next() -> None:
    job = _reserve()
    if job is None:
        return

    snapshot = MemorySnapshot.model_validate(job["snapshot"])
    status = job["extraction_status"]
    extracted = []
    try:
        vector = embedding(snapshot.content)
        if status == "pending":
            try:
                extracted = extract_claims(snapshot.content)
                status = "complete"
            except InvalidExtraction:
                status = "rejected"
                logger.warning("Rejected an invalid extraction result.")
    except ModelUnavailable:
        _defer(job)
        logger.warning("Ollama unavailable. Memory indexing will retry.")
        return
    _finish(job, snapshot, vector, extracted, status)


def dispatch() -> None:
    connection = Redis.from_url(os.environ["REDIS_URL"])
    queue = Queue("memory", connection=connection, serializer="json")
    next_review = 0.0
    while True:
        try:
            if time.monotonic() >= next_review:
                with connect() as db:
                    mark_overdue_claims(db)
                next_review = time.monotonic() + 30
            if model_enabled() and queue.count == 0:
                with connect() as db:
                    due = db.execute("""
                        SELECT 1 FROM memories WHERE index_due_at <= statement_timestamp()
                            AND (expires_at IS NULL OR expires_at > statement_timestamp())
                        LIMIT 1
                    """).fetchone()
                if due:
                    queue.enqueue_call(process_next, timeout=180, result_ttl=0)
        except (psycopg.OperationalError, RedisError):
            logger.warning("Index dispatcher waiting for PostgreSQL or Redis.")
        time.sleep(2)
