from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from apps.api.auth import AgentAccess, Database, Scope, require_subject
from apps.api.identity import issue_agent_key
from apps.api.memory_models import Input, ShortText

router = APIRouter(prefix="/v1/console")
SessionAccess = Annotated[AgentAccess, Depends(require_subject(None))]
ManageAccess = Annotated[AgentAccess, Depends(require_subject(Scope.WORKSPACE_MANAGE))]
ReadAccess = Annotated[AgentAccess, Depends(require_subject(Scope.MEMORY_READ))]
Days = Annotated[int, Field(ge=1, le=3650)]


class WorkspaceSettings(Input):
    name: ShortText
    default_retention_days: Days | None
    default_review_days: Days | None


class AgentCreate(Input):
    name: ShortText


class KeyCreate(Input):
    agent_id: UUID
    scopes: Annotated[list[Scope], Field(min_length=1, max_length=6)]


def _scope(access):
    return access.workspace_id, access.subject_id


@router.get("/session")
def get_session(access: SessionAccess, db: Database):
    row = db.execute(
        """
        SELECT w.name AS workspace, w.default_retention_days, w.default_review_days,
            s.reference AS subject, s.kind AS subject_kind, a.name AS agent
        FROM subjects s
        JOIN workspaces w ON w.id = s.workspace_id
        JOIN agents a ON a.workspace_id = w.id AND a.id = %s
        WHERE (s.workspace_id, s.id) = (%s, %s)
    """,
        (access.agent_id, *_scope(access)),
    ).fetchone()
    return {
        **row,
        "agent_id": access.agent_id,
        "key_id": access.key_id,
        "scopes": access.scopes,
    }


@router.get("/overview")
def get_overview(access: ReadAccess, db: Database):
    scope = _scope(access)
    activity = db.execute(
        """
        SELECT id AS memory_id, content, category, updated_at AS recorded_at
        FROM memories WHERE (workspace_id, subject_id) = (%s, %s)
            AND (expires_at IS NULL OR expires_at > statement_timestamp())
        ORDER BY updated_at DESC, id LIMIT 8
    """,
        scope,
    ).fetchall()
    reviews = db.execute(
        """
        SELECT c.id AS claim_id, c.memory_id, c.predicate, c.evidence->>'status' AS status,
            c.review_due_at, m.content
        FROM claim_evidence c
        JOIN memories m ON (m.workspace_id, m.subject_id, m.id) = (c.workspace_id, c.subject_id, c.memory_id)
        WHERE (c.workspace_id, c.subject_id) = (%s, %s)
            AND (m.expires_at IS NULL OR m.expires_at > statement_timestamp())
            AND (c.status IN ('review_due', 'stale')
                OR (c.status = 'active' AND c.review_due_at <= statement_timestamp()))
        ORDER BY c.review_due_at NULLS FIRST, c.created_at DESC, c.id LIMIT 8
    """,
        scope,
    ).fetchall()
    retrievals = db.execute(
        """
        SELECT r.memory_id, r.result_count, r.recorded_at, a.name AS agent
        FROM retrieval_events r JOIN agents a ON (a.workspace_id, a.id) = (r.workspace_id, r.agent_id)
        WHERE (r.workspace_id, r.subject_id) = (%s, %s)
        ORDER BY recorded_at DESC LIMIT 8
    """,
        scope,
    ).fetchall()
    return {"writes": activity, "reviews": reviews, "retrievals": retrievals}


@router.get("/agents")
def list_agents(access: ManageAccess, db: Database):
    return db.execute(
        """
        SELECT a.id, a.name, activity.last_activity
        FROM agents a
        LEFT JOIN LATERAL (
            SELECT greatest(
                (SELECT max((v.snapshot->>'updated_at')::timestamptz) FROM memory_versions v
                    WHERE (v.workspace_id, v.subject_id, v.agent_id) = (a.workspace_id, %s, a.id)),
                (SELECT max(recorded_at) FROM retrieval_events
                    WHERE (workspace_id, subject_id, agent_id) = (a.workspace_id, %s, a.id))
            ) AS last_activity
        ) activity ON true
        WHERE a.workspace_id = %s ORDER BY lower(a.name), a.id
    """,
        (access.subject_id, access.subject_id, access.workspace_id),
    ).fetchall()


@router.post("/agents", status_code=201)
def create_agent(data: AgentCreate, access: ManageAccess, db: Database):
    return db.execute(
        """
        INSERT INTO agents (workspace_id, registered_by, name)
        SELECT workspace_id, registered_by, %s FROM agents WHERE (workspace_id, id) = (%s, %s)
        RETURNING id, name
    """,
        (data.name, access.workspace_id, access.agent_id),
    ).fetchone()


@router.get("/keys")
def list_keys(access: ManageAccess, db: Database):
    return db.execute(
        """
        SELECT k.id, k.agent_id, k.scopes, k.revoked_at, a.name AS agent
        FROM api_keys k JOIN agents a ON (a.workspace_id, a.id) = (k.workspace_id, k.agent_id)
        WHERE (k.workspace_id, k.subject_id) = (%s, %s)
        ORDER BY k.revoked_at NULLS FIRST, a.name, k.id
    """,
        _scope(access),
    ).fetchall()


@router.post("/keys", status_code=201)
def create_key(data: KeyCreate, access: ManageAccess, db: Database):
    if Scope.WORKSPACE_MANAGE in data.scopes:
        raise HTTPException(
            status_code=403,
            detail="Use local operator provisioning for management keys",
        )
    if (
        db.execute(
            "SELECT 1 FROM agents WHERE (workspace_id, id) = (%s, %s)",
            (access.workspace_id, data.agent_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(status_code=404, detail="Agent unavailable")
    return issue_agent_key(db, *_scope(access), data.agent_id, data.scopes)


@router.delete("/keys/{key_id}", status_code=204)
def revoke_key(key_id: UUID, access: ManageAccess, db: Database):
    changed = db.execute(
        """
        UPDATE api_keys SET revoked_at = COALESCE(revoked_at, now())
        WHERE (workspace_id, subject_id, id) = (%s, %s, %s)
            AND NOT ('workspace:manage' = ANY(scopes))
    """,
        (*_scope(access), key_id),
    )
    if changed.rowcount == 0:
        raise HTTPException(
            status_code=404, detail="Key unavailable for console revocation"
        )


@router.patch("/settings")
def update_settings(data: WorkspaceSettings, access: ManageAccess, db: Database):
    return db.execute(
        """
        UPDATE workspaces SET name = %s, default_retention_days = %s, default_review_days = %s
        WHERE id = %s RETURNING name AS workspace, default_retention_days, default_review_days
    """,
        (
            data.name,
            data.default_retention_days,
            data.default_review_days,
            access.workspace_id,
        ),
    ).fetchone()


def record_retrieval(db, access: AgentAccess, result) -> None:
    first_id = result.evidence[0].memory_id if result.evidence else None
    db.execute(
        """
        INSERT INTO retrieval_events (workspace_id, subject_id, agent_id, memory_id, result_count)
        VALUES (%s, %s, %s, (
            SELECT id FROM memories WHERE (workspace_id, subject_id, id) = (%s, %s, %s)
            FOR KEY SHARE
        ), %s)
    """,
        (
            *_scope(access),
            access.agent_id,
            *_scope(access),
            first_id,
            len(result.evidence),
        ),
    )
