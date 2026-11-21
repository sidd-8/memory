from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel

from apps.api.auth import AgentAccess, Database, Scope, require_subject
from apps.api.memory_models import ClaimStatus, Entity

router = APIRouter()
GraphAccess = Annotated[AgentAccess, Depends(require_subject(Scope.GRAPH_READ))]


class Edge(BaseModel):
    from_entity_id: UUID
    to_entity_id: UUID
    predicate: str
    claim_id: UUID
    memory_id: UUID
    source_id: UUID
    status: ClaimStatus
    valid_from: datetime | None
    valid_to: datetime | None


class Neighborhood(BaseModel):
    root_id: UUID
    nodes: list[Entity]
    edges: list[Edge]


@router.get("/v1/entities/{entity_id}/graph")
def entity_graph(
    entity_id: UUID,
    access: GraphAccess,
    db: Database,
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
    edge_limit: Annotated[int, Query(ge=1, le=100)] = 50,
    valid_at: AwareDatetime | None = None,
) -> Neighborhood:
    scope = (access.workspace_id, access.subject_id)
    row = db.execute(
        """
        WITH args AS (
            SELECT %s::uuid AS workspace_id, %s::uuid AS subject_id, %s::uuid AS entity_id,
                %s::int AS depth, %s::int AS edge_limit, %s::timestamptz AS valid_at
        ), root AS (
            SELECT e.id FROM entities e, args a
            WHERE (e.workspace_id, e.subject_id, e.id) = (a.workspace_id, a.subject_id, a.entity_id)
                AND EXISTS (
                    SELECT 1 FROM claims c
                    JOIN memories m ON (m.workspace_id, m.subject_id, m.id)
                        = (c.workspace_id, c.subject_id, c.memory_id)
                    WHERE (c.workspace_id, c.subject_id) = (a.workspace_id, a.subject_id)
                        AND (c.subject_entity_id = e.id OR c.object_entity_id = e.id)
                        AND (m.expires_at IS NULL OR m.expires_at > statement_timestamp())
                )
        ), eligible AS NOT MATERIALIZED (
            SELECT r.claim_id, r.from_entity_id, r.to_entity_id, r.predicate
            FROM relationships r
            JOIN claims c ON (r.workspace_id, r.subject_id, r.claim_id)
                = (c.workspace_id, c.subject_id, c.id)
            JOIN memories m ON (m.workspace_id, m.subject_id, m.id)
                = (c.workspace_id, c.subject_id, c.memory_id)
            JOIN args a ON (r.workspace_id, r.subject_id) = (a.workspace_id, a.subject_id)
            WHERE (m.expires_at IS NULL OR m.expires_at > statement_timestamp())
                AND m.state IS DISTINCT FROM 'cancelled' AND m.state IS DISTINCT FROM 'superseded'
                AND c.status <> 'archived'
                AND (c.superseded_at IS NULL OR (a.valid_at IS NOT NULL AND c.verified_at IS NOT NULL))
                AND tstzrange(c.valid_from, c.valid_to, '[)')
                    @> COALESCE(a.valid_at, statement_timestamp())
        ), first_hop AS (
            SELECT e.* FROM eligible e, root
            WHERE root.id IN (e.from_entity_id, e.to_entity_id)
            ORDER BY e.claim_id LIMIT (SELECT edge_limit FROM args)
        ), neighbors AS (
            SELECT from_entity_id AS id FROM first_hop
            UNION SELECT to_entity_id FROM first_hop
        ), second_hop AS (
            SELECT e.* FROM eligible e
            WHERE (SELECT depth FROM args) = 2
                AND (e.from_entity_id IN (SELECT id FROM neighbors)
                    OR e.to_entity_id IN (SELECT id FROM neighbors))
                AND e.claim_id NOT IN (SELECT claim_id FROM first_hop)
            ORDER BY e.claim_id
            LIMIT ((SELECT edge_limit FROM args) - (SELECT count(*) FROM first_hop))
        ), edges AS (
            SELECT 1 AS hop, * FROM first_hop
            UNION ALL SELECT 2, * FROM second_hop
        ), nodes AS (
            SELECT id FROM root
            UNION SELECT from_entity_id FROM edges
            UNION SELECT to_entity_id FROM edges
        )
        SELECT root.id AS root_id,
            ARRAY(
                SELECT jsonb_build_object('id', e.id, 'kind', e.kind, 'reference', e.reference)
                FROM args a JOIN entities e USING (workspace_id, subject_id)
                JOIN nodes n ON n.id = e.id
                ORDER BY (e.id = root.id) DESC, e.id
            ) AS nodes,
            ARRAY(
                SELECT jsonb_build_object(
                    'from_entity_id', edge.from_entity_id, 'to_entity_id', edge.to_entity_id,
                    'predicate', edge.predicate, 'claim_id', edge.claim_id, 'memory_id', c.memory_id,
                    'source_id', c.source_id, 'status', c.evidence->>'status',
                    'valid_from', c.valid_from, 'valid_to', c.valid_to
                )
                FROM edges edge
                JOIN claim_evidence c ON c.id = edge.claim_id
                JOIN args a ON (c.workspace_id, c.subject_id) = (a.workspace_id, a.subject_id)
                ORDER BY edge.hop, edge.claim_id
            ) AS edges
        FROM root
        """,
        (*scope, entity_id, depth, edge_limit, valid_at),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entity unavailable")
    return Neighborhood.model_validate(row)
