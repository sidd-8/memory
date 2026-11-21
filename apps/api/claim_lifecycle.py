from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from psycopg import Connection

from apps.api.auth import AgentAccess
from apps.api.memory_models import Claim, ClaimReview, MemorySnapshot
from apps.api.memory_store import (
    conflicting_claims,
    lock_subject,
    replay_write,
    save_revision,
    save_write_request,
    write_fingerprint,
)


def _scope(access: AgentAccess) -> tuple[UUID, UUID]:
    return access.workspace_id, access.subject_id


def list_claims(
    db: Connection,
    access: AgentAccess,
    memory_id: UUID,
    valid_at: datetime | None,
    limit: int,
    offset: int,
) -> list[Claim]:
    scope = (*_scope(access), memory_id)
    if (
        db.execute(
            "SELECT 1 FROM memories WHERE (workspace_id, subject_id, id) = (%s, %s, %s)",
            scope,
        ).fetchone()
        is None
    ):
        raise HTTPException(status_code=404, detail="Memory unavailable")
    rows = db.execute(
        """
        SELECT evidence FROM claim_evidence
        WHERE (workspace_id, subject_id, memory_id) = (%s, %s, %s)
            AND (%s::timestamptz IS NULL OR tstzrange(valid_from, valid_to, '[)') @> %s::timestamptz)
        ORDER BY created_at DESC, id LIMIT %s OFFSET %s
        """,
        (*scope, valid_at, valid_at, limit, offset),
    ).fetchall()
    return [Claim.model_validate(row["evidence"]) for row in rows]


def _default_interval(category: str, predicate: str) -> int | None:
    if predicate in {"employer", "works_for", "employment", "current_role"}:
        return 90
    if predicate in {"preference", "prefers"}:
        return 180
    return {"project": 60, "procedural": 180, "working": 7}.get(category)


def _record_review(
    db: Connection, access: AgentAccess, memory_id: UUID
) -> MemorySnapshot:
    scope = (*_scope(access), memory_id)
    row = db.execute(
        """
        SELECT v.snapshot, ARRAY(
            SELECT c.evidence
            FROM jsonb_array_elements(v.snapshot->'claims') WITH ORDINALITY AS item(claim, position)
            JOIN claim_evidence c ON (c.workspace_id, c.subject_id, c.memory_id, c.id)
                = (v.workspace_id, v.subject_id, v.memory_id, (item.claim->>'id')::uuid)
            ORDER BY item.position
        ) AS claims
        FROM memories m JOIN memory_versions v
            ON (v.workspace_id, v.subject_id, v.memory_id, v.revision)
             = (m.workspace_id, m.subject_id, m.id, m.revision)
        WHERE (m.workspace_id, m.subject_id, m.id) = (%s, %s, %s)
        """,
        scope,
    ).fetchone()
    revision = db.execute(
        """
        UPDATE memories SET revision = revision + 1, updated_at = clock_timestamp(),
            index_due_at = CASE WHEN index_due_at IS NOT NULL THEN clock_timestamp() END
        WHERE (workspace_id, subject_id, id) = (%s, %s, %s)
        RETURNING revision, updated_at
        """,
        scope,
    ).fetchone()
    snapshot = MemorySnapshot.model_validate(
        {
            **row["snapshot"],
            **revision,
            "claims": row["claims"],
            "changed_by": access.agent_id,
        }
    )
    save_revision(db, snapshot)
    return snapshot


def review_claim(
    db: Connection,
    access: AgentAccess,
    memory_id: UUID,
    review: ClaimReview,
    idempotency_key: str | None,
) -> Claim:
    scope = _scope(access)
    lock_subject(db, scope)
    fingerprint = write_fingerprint(
        idempotency_key,
        ["verify", str(memory_id), review.model_dump(mode="json", exclude_unset=True)],
    )
    replay = replay_write(db, access, fingerprint)
    if replay is not None:
        return next(claim for claim in replay.claims if claim.id == review.claim_id)
    target = db.execute(
        """
        SELECT c.status, c.predicate, c.valid_from, m.category
        FROM claims c JOIN memories m
            ON (c.workspace_id, c.subject_id, c.memory_id) = (m.workspace_id, m.subject_id, m.id)
        WHERE (c.workspace_id, c.subject_id, c.memory_id, c.id) = (%s, %s, %s, %s)
        """,
        (*scope, memory_id, review.claim_id),
    ).fetchone()
    if target is None:
        raise HTTPException(status_code=404, detail="Claim unavailable")
    if target["status"] in {"superseded", "archived"}:
        raise HTTPException(status_code=409, detail="Claim is closed")

    now = db.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    status = {
        "verified": "active",
        "needs_review": "review_due",
        "archived": "archived",
    }[review.outcome]
    old_memory = None
    if review.outcome == "verified":
        conflicts = conflicting_claims(db, scope, review.claim_id)
        old_id = review.supersedes_claim_id
        if old_id is not None:
            if old_id not in conflicts or target["valid_from"] is None:
                raise HTTPException(
                    status_code=409,
                    detail="Supersession requires an overlapping verified claim and a replacement valid_from",
                )
            old = db.execute(
                """
                SELECT memory_id, valid_from FROM claims
                WHERE (workspace_id, subject_id, id) = (%s, %s, %s)
                """,
                (*scope, old_id),
            ).fetchone()
            if (
                old["valid_from"] is not None
                and target["valid_from"] <= old["valid_from"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Replacement must start after the older claim",
                )
        if conflicts - {old_id}:
            status = "review_due"
        elif old_id is not None:
            old_memory = old["memory_id"]
            db.execute(
                """
                UPDATE claims SET status = 'superseded', valid_to = %s, superseded_at = %s
                WHERE (workspace_id, subject_id, id) = (%s, %s, %s)
                """,
                (target["valid_from"], now, *scope, old_id),
            )

    if status == "active":
        interval = review.review_interval_days
        if "review_interval_days" not in review.model_fields_set:
            settings = db.execute(
                "SELECT default_review_days FROM workspaces WHERE id = %s",
                (access.workspace_id,),
            ).fetchone()
            interval = settings["default_review_days"] or _default_interval(
                target["category"], target["predicate"]
            )
        due_at = now + timedelta(days=interval) if interval is not None else None
        db.execute(
            """
            UPDATE claims SET status = 'active', verified_at = %s, verified_by = %s,
                review_due_at = %s, reviewed_at = %s, reviewed_by = %s
            WHERE (workspace_id, subject_id, memory_id, id) = (%s, %s, %s, %s)
            """,
            (
                now,
                access.agent_id,
                due_at,
                now,
                access.agent_id,
                *scope,
                memory_id,
                review.claim_id,
            ),
        )
    else:
        db.execute(
            """
            UPDATE claims SET status = %s, reviewed_at = %s, reviewed_by = %s
            WHERE (workspace_id, subject_id, memory_id, id) = (%s, %s, %s, %s)
            """,
            (status, now, access.agent_id, *scope, memory_id, review.claim_id),
        )
    if old_memory is not None and old_memory != memory_id:
        _record_review(db, access, old_memory)
    snapshot = _record_review(db, access, memory_id)
    save_write_request(db, access, fingerprint, snapshot)
    return next(claim for claim in snapshot.claims if claim.id == review.claim_id)


def mark_overdue_claims(db: Connection) -> int:
    return db.execute("""
        WITH due AS (
            SELECT workspace_id, subject_id, id FROM claims
            WHERE status = 'active' AND review_due_at <= statement_timestamp()
            ORDER BY review_due_at LIMIT 500 FOR UPDATE SKIP LOCKED
        )
        UPDATE claims c SET status = 'stale' FROM due
        WHERE (c.workspace_id, c.subject_id, c.id) = (due.workspace_id, due.subject_id, due.id)
    """).rowcount
