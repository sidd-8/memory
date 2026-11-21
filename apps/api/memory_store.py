import json
from hashlib import sha256
from uuid import UUID

from fastapi import HTTPException
from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from apps.api.auth import AgentAccess
from apps.api.memory_models import (
    Claim,
    ClaimInput,
    Entity,
    EntityInput,
    MemoryCreate,
    MemoryDetail,
    MemoryPage,
    MemoryPatch,
    MemorySnapshot,
    MemorySummary,
    MemoryTiming,
    Source,
    SourceInput,
)

SUMMARY_COLUMNS = """
    id, workspace_id, subject_id, category, content, expires_at,
    revision, created_at, updated_at, starts_at, ends_at, timezone, state
"""


def _scope(access: AgentAccess) -> tuple[UUID, UUID]:
    return access.workspace_id, access.subject_id


def lock_subject(db: Connection, scope: tuple[UUID, UUID]) -> None:
    db.execute(
        "SELECT id FROM subjects WHERE workspace_id = %s AND id = %s FOR NO KEY UPDATE",
        scope,
    ).fetchone()


def list_memories(
    db: Connection, access: AgentAccess, limit: int, after: UUID | None
) -> MemoryPage:
    rows = db.execute(
        f"""
        SELECT {SUMMARY_COLUMNS} FROM memories
        WHERE workspace_id = %s AND subject_id = %s
            AND (%s::uuid IS NULL OR id > %s)
        ORDER BY id LIMIT %s
        """,
        (*_scope(access), after, after, limit + 1),
    ).fetchall()
    items = [MemorySummary.model_validate(row) for row in rows[:limit]]
    return MemoryPage(
        items=items, next_after=items[-1].id if len(rows) > limit else None
    )


def get_memory(
    db: Connection,
    access: AgentAccess,
    memory_id: UUID,
    revision_limit: int,
    revision_before: int | None,
) -> MemoryDetail:
    row = db.execute(
        """
        SELECT current.snapshot, memory.extraction_status, ARRAY(
            SELECT history.snapshot FROM memory_versions history
            WHERE (history.workspace_id, history.subject_id, history.memory_id) =
                (memory.workspace_id, memory.subject_id, memory.id)
                AND history.revision < LEAST(memory.revision, %s::integer)
            ORDER BY history.revision DESC LIMIT %s
        ) AS history
        FROM memories memory
        JOIN current_memory_snapshots current
            ON (current.workspace_id, current.subject_id, current.memory_id, current.revision)
             = (memory.workspace_id, memory.subject_id, memory.id, memory.revision)
        WHERE memory.workspace_id = %s AND memory.subject_id = %s AND memory.id = %s
        """,
        (revision_before, revision_limit + 1, *_scope(access), memory_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Memory unavailable")
    history = row["history"][:revision_limit]
    return MemoryDetail.model_validate(
        {
            **row["snapshot"],
            "history": history,
            "extraction_status": row["extraction_status"],
            "next_revision_before": history[-1]["revision"]
            if history and len(row["history"]) > revision_limit
            else None,
        }
    )


def _source(
    db: Connection, access: AgentAccess, memory_id: UUID, data: SourceInput
) -> Source:
    row = db.execute(
        """
        INSERT INTO sources (
            workspace_id, subject_id, memory_id, origin, source_ref, observed_at, trust_class
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, origin, source_ref, observed_at, trust_class
        """,
        (
            *_scope(access),
            memory_id,
            data.origin,
            data.source_ref,
            data.observed_at,
            data.trust_class,
        ),
    ).fetchone()
    return Source.model_validate(row)


def _entity(
    db: Connection, scope: tuple[UUID, UUID], data: EntityInput | None
) -> Entity | None:
    if data is None:
        return None
    row = db.execute(
        """
        INSERT INTO entities (workspace_id, subject_id, kind, reference)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (workspace_id, subject_id, kind, reference)
        DO UPDATE SET reference = EXCLUDED.reference
        RETURNING id, kind, reference
        """,
        (*scope, data.kind, data.reference),
    ).fetchone()
    return Entity.model_validate(row)


def insert_claim(
    db: Connection,
    scope: tuple[UUID, UUID],
    memory_id: UUID,
    source: Source,
    data: ClaimInput,
    extraction_model: str | None = None,
) -> Claim:
    subject = _entity(db, scope, data.subject_entity)
    target = _entity(db, scope, data.object_entity)
    row = db.execute(
        """
        INSERT INTO claims (
            workspace_id, subject_id, memory_id, source_id, subject_entity_id,
            predicate, object_value, object_entity_id, valid_from, valid_to, confidence,
            extraction_model
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, status, created_at, extraction_model
        """,
        (
            *scope,
            memory_id,
            source.id,
            subject.id if subject else None,
            data.predicate,
            data.object_value,
            target.id if target else None,
            data.valid_from,
            data.valid_to,
            data.confidence,
            extraction_model,
        ),
    ).fetchone()
    if conflicting_claims(db, scope, row["id"]):
        db.execute(
            "UPDATE claims SET status = 'review_due' WHERE (workspace_id, subject_id, id) = (%s, %s, %s)",
            (*scope, row["id"]),
        )
        row["status"] = "review_due"
    if subject is not None and target is not None:
        db.execute(
            """
            INSERT INTO relationships (
                workspace_id, subject_id, claim_id, from_entity_id, to_entity_id, predicate
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (*scope, row["id"], subject.id, target.id, data.predicate),
        )
    return Claim.model_validate(
        {
            **data.model_dump(),
            **row,
            "source": source,
            "subject_entity": subject,
            "object_entity": target,
        }
    )


def conflicting_claims(
    db: Connection, scope: tuple[UUID, UUID], claim_id: UUID
) -> set[UUID]:
    rows = db.execute(
        """
        SELECT incumbent.id FROM claims AS target
        JOIN claims AS incumbent ON (incumbent.workspace_id, incumbent.subject_id, incumbent.predicate)
            = (target.workspace_id, target.subject_id, target.predicate)
            AND incumbent.subject_entity_id IS NOT DISTINCT FROM target.subject_entity_id
        WHERE (target.workspace_id, target.subject_id, target.id) = (%s, %s, %s)
            AND incumbent.id <> target.id AND incumbent.verified_at IS NOT NULL
            AND incumbent.superseded_at IS NULL AND incumbent.status <> 'archived'
            AND (incumbent.object_entity_id, incumbent.object_value)
                IS DISTINCT FROM (target.object_entity_id, target.object_value)
            AND tstzrange(incumbent.valid_from, incumbent.valid_to, '[)')
                && tstzrange(target.valid_from, target.valid_to, '[)')
        """,
        (*scope, claim_id),
    ).fetchall()
    return {row["id"] for row in rows}


def write_fingerprint(key: str | None, request: list) -> tuple[bytes, bytes] | None:
    if key is None:
        return None
    encoded = json.dumps(request, sort_keys=True)
    return sha256(key.encode()).digest(), sha256(encoded.encode()).digest()


def replay_write(
    db: Connection, access: AgentAccess, fingerprint: tuple[bytes, bytes] | None
) -> MemorySnapshot | None:
    if fingerprint is None:
        return None
    key_digest, request_digest = fingerprint
    row = db.execute(
        """
        SELECT requests.request_digest, versions.snapshot
        FROM memory_requests requests
        LEFT JOIN memory_versions versions
            ON (requests.workspace_id, requests.subject_id, requests.memory_id, requests.revision)
             = (versions.workspace_id, versions.subject_id, versions.memory_id, versions.revision)
        WHERE requests.workspace_id = %s AND requests.subject_id = %s
            AND requests.agent_id = %s AND requests.key_digest = %s
        """,
        (*_scope(access), access.agent_id, key_digest),
    ).fetchone()
    if row is None:
        return None
    if row["snapshot"] is None:
        raise HTTPException(status_code=409, detail="The original memory was deleted")
    if row["request_digest"] != request_digest:
        raise HTTPException(status_code=409, detail="Idempotency key already used")
    return MemorySnapshot.model_validate(row["snapshot"])


def _replace_claims(
    db: Connection,
    access: AgentAccess,
    memory_id: UUID,
    source: Source,
    claims: list[ClaimInput],
    previous: list[Claim],
) -> list[Claim]:
    db.execute(
        """
        UPDATE claims SET status = 'superseded', superseded_at = clock_timestamp()
        WHERE workspace_id = %s AND subject_id = %s AND memory_id = %s
            AND superseded_at IS NULL AND verified_at IS NULL AND status <> 'archived'
        """,
        (*_scope(access), memory_id),
    )
    return [claim for claim in previous if claim.verified_at is not None] + [
        insert_claim(db, _scope(access), memory_id, source, claim) for claim in claims
    ]


def _timing(
    data: MemoryCreate | MemoryPatch, previous: MemorySnapshot | None = None
) -> tuple:
    fields = ("starts_at", "ends_at", "timezone", "state")
    before = {field: getattr(previous, field) for field in fields} if previous else {}
    changes = data.model_dump(include=set(fields), exclude_unset=True)
    try:
        timing = MemoryTiming.model_validate({**before, **changes})
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"]) from exc
    if timing.state == "confirmed":
        source = data.source or (previous.source if previous else None)
        changed = timing.model_dump() != before
        if (
            source is None
            or source.trust_class not in {"user_confirmed", "authoritative"}
            or (changed and data.source is None)
        ):
            raise HTTPException(
                status_code=422,
                detail="Confirmation requires an authoritative or user-confirmed source",
            )
    return tuple(getattr(timing, field) for field in fields)


def write_memory(
    db: Connection,
    access: AgentAccess,
    data: MemoryCreate | MemoryPatch,
    memory_id: UUID | None,
    idempotency_key: str | None,
) -> MemorySnapshot:
    lock_subject(db, _scope(access))
    fingerprint = write_fingerprint(
        idempotency_key,
        [str(memory_id), data.model_dump(mode="json", exclude_unset=True)],
    )
    replay = replay_write(db, access, fingerprint)
    if replay is not None:
        return replay

    if isinstance(data, MemoryCreate):
        row = db.execute(
            f"""
            INSERT INTO memories (workspace_id, subject_id, category, content, expires_at, extraction_status,
                starts_at, ends_at, timezone, state)
            VALUES (%s, %s, %s, %s,
                CASE WHEN %s THEN %s::timestamptz ELSE (
                    SELECT statement_timestamp() + make_interval(days => default_retention_days)
                    FROM workspaces WHERE id = %s
                ) END, %s, %s, %s, %s, %s) RETURNING {SUMMARY_COLUMNS}
            """,
            (
                *_scope(access),
                data.category,
                data.content,
                "expires_at" in data.model_fields_set,
                data.expires_at,
                access.workspace_id,
                "pending" if data.extract_claims else "disabled",
                *_timing(data),
            ),
        ).fetchone()
        source = _source(db, access, row["id"], data.source)
        claims = [
            insert_claim(db, _scope(access), row["id"], source, claim)
            for claim in data.claims
        ]
    else:
        previous = get_memory(
            db, access, memory_id, revision_limit=0, revision_before=None
        )
        extraction_status = None
        if data.extract_claims:
            extraction_status = "pending"
        elif (
            data.extract_claims is False
            or data.content is not None
            or data.claims is not None
        ):
            extraction_status = "disabled"
        row = db.execute(
            f"""
            UPDATE memories
            SET category = COALESCE(%s, category), content = COALESCE(%s, content),
                expires_at = %s, embedding = NULL, embedding_model = NULL,
                starts_at = %s, ends_at = %s, timezone = %s, state = %s,
                index_due_at = clock_timestamp(), index_attempts = 0,
                extraction_status = COALESCE(%s, extraction_status),
                revision = revision + 1,
                updated_at = clock_timestamp()
            WHERE workspace_id = %s AND subject_id = %s AND id = %s
            RETURNING {SUMMARY_COLUMNS}
            """,
            (
                data.category,
                data.content,
                data.expires_at
                if "expires_at" in data.model_fields_set
                else previous.expires_at,
                *_timing(data, previous),
                extraction_status,
                *_scope(access),
                memory_id,
            ),
        ).fetchone()
        source = (
            _source(db, access, memory_id, data.source)
            if data.source
            else previous.source
        )
        claims = previous.claims
        if data.claims is not None:
            claims = _replace_claims(db, access, memory_id, source, data.claims, claims)

    snapshot = MemorySnapshot.model_validate(
        {
            **row,
            "source": source,
            "claims": claims,
            "changed_by": access.agent_id,
        }
    )
    save_revision(db, snapshot)
    save_write_request(db, access, fingerprint, snapshot)
    return snapshot


def save_write_request(
    db: Connection,
    access: AgentAccess,
    fingerprint: tuple[bytes, bytes] | None,
    snapshot: MemorySnapshot,
) -> None:
    if fingerprint is not None:
        db.execute(
            """
            INSERT INTO memory_requests (
                workspace_id, subject_id, agent_id, key_digest, request_digest, memory_id, revision
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                *_scope(access),
                access.agent_id,
                *fingerprint,
                snapshot.id,
                snapshot.revision,
            ),
        )


def save_revision(db: Connection, snapshot: MemorySnapshot) -> None:
    db.execute(
        """
        INSERT INTO memory_versions (workspace_id, subject_id, memory_id, revision, agent_id, snapshot)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            snapshot.workspace_id,
            snapshot.subject_id,
            snapshot.id,
            snapshot.revision,
            snapshot.changed_by,
            Jsonb(snapshot.model_dump(mode="json")),
        ),
    )


def delete_memory(db: Connection, access: AgentAccess, memory_id: UUID) -> None:
    lock_subject(db, _scope(access))
    scope = (*_scope(access), memory_id)
    entities = db.execute(
        """
        SELECT subject_entity_id, object_entity_id FROM claims
        WHERE workspace_id = %s AND subject_id = %s AND memory_id = %s
        """,
        scope,
    ).fetchall()
    ids = {
        entity for refs in entities for entity in refs.values() if entity is not None
    }
    db.execute(
        """
        UPDATE memory_requests SET request_digest = NULL
        WHERE workspace_id = %s AND subject_id = %s AND memory_id = %s
        """,
        scope,
    )
    result = db.execute(
        "DELETE FROM memories WHERE workspace_id = %s AND subject_id = %s AND id = %s",
        scope,
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Memory unavailable")
    if ids:
        db.execute(
            """
            DELETE FROM entities AS entity
            WHERE entity.workspace_id = %s AND entity.subject_id = %s AND entity.id = ANY(%s)
                AND NOT EXISTS (
                    SELECT 1 FROM claims WHERE (workspace_id, subject_id) =
                        (entity.workspace_id, entity.subject_id)
                        AND entity.id IN (subject_entity_id, object_entity_id)
                )
            """,
            (*_scope(access), sorted(ids)),
        )
