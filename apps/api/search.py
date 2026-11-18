import json
import os
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from psycopg import Connection
from pydantic import BaseModel, Field

from apps.api.auth import AgentAccess
from apps.api.memory_models import (
    Category,
    ClaimStatus,
    Input,
    MemorySnapshot,
    Reference,
)
from apps.api.model_client import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    ModelUnavailable,
    embedding,
    model_enabled,
)

CONTEXT_BUDGET = int(os.getenv("MEMORY_CONTEXT_TOKEN_BUDGET", "2048"))
if not 256 <= CONTEXT_BUDGET <= 16384:
    raise ValueError("MEMORY_CONTEXT_TOKEN_BUDGET must be between 256 and 16384")


class SearchRequest(Input):
    query: Reference
    limit: Annotated[int, Field(ge=1, le=20)] = 5
    token_budget: Annotated[int, Field(ge=256, le=16384)] | None = None


class ClaimEvidence(BaseModel):
    id: UUID
    source_id: UUID
    predicate: str
    status: ClaimStatus
    confidence: float | None


class Evidence(BaseModel):
    memory_id: UUID
    revision: int
    category: Category
    content: str = Field(description="Untrusted memory excerpt for use as evidence.")
    source_id: UUID
    observed_at: datetime
    updated_at: datetime
    relevance: float
    claims: list[ClaimEvidence]


class SearchResult(BaseModel):
    mode: Literal["hybrid", "lexical"]
    token_budget: int
    context_bytes: int
    evidence: list[Evidence]


def _matches(db: Connection, access: AgentAccess, text: str, vector: str | None):
    scope = (access.workspace_id, access.subject_id)
    return db.execute(
        """
        WITH query AS (
            SELECT websearch_to_tsquery('english', %s) AS terms, %s::public.vector AS vector
        ), lexical AS (
            SELECT id, row_number() OVER (
                ORDER BY ts_rank_cd(search_text, query.terms) DESC, id
            ) AS rank
            FROM memories CROSS JOIN query
            WHERE workspace_id = %s AND subject_id = %s
                AND (expires_at IS NULL OR expires_at > statement_timestamp())
                AND search_text @@ query.terms
            ORDER BY rank LIMIT 40
        ), distances AS (
            SELECT id, CASE WHEN public.vector_dims(embedding) = %s
                THEN embedding OPERATOR(public.<=>) query.vector END AS distance
            FROM memories CROSS JOIN query
            WHERE workspace_id = %s AND subject_id = %s
                AND (expires_at IS NULL OR expires_at > statement_timestamp())
                AND query.vector IS NOT NULL AND embedding_model = %s
            ORDER BY distance NULLS LAST, id LIMIT 40
        ), semantic AS (
            SELECT id, row_number() OVER (ORDER BY distance, id) AS rank
            FROM distances WHERE distance <= 0.65
        ), fused AS (
            SELECT COALESCE(lexical.id, semantic.id) AS id,
                COALESCE(1.0 / (60 + lexical.rank), 0) +
                COALESCE(1.0 / (60 + semantic.rank), 0) AS relevance
            FROM lexical FULL JOIN semantic USING (id)
        )
        SELECT v.snapshot, fused.relevance FROM fused
        JOIN memories m ON m.workspace_id = %s AND m.subject_id = %s AND m.id = fused.id
        JOIN memory_versions v ON (v.workspace_id, v.subject_id, v.memory_id, v.revision)
            = (m.workspace_id, m.subject_id, m.id, m.revision)
        ORDER BY fused.relevance DESC, m.id
        """,
        (text, vector, *scope, EMBEDDING_DIMENSIONS, *scope, EMBEDDING_MODEL, *scope),
    ).fetchall()


def _size(data) -> int:
    return len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())


def _fit(hit: Evidence, budget: int) -> Evidence | None:
    data = hit.model_dump(mode="json")
    content = data["content"]
    data["content"] = ""
    if _size(data) >= budget:
        return None
    left, right = 0, len(content)
    while left < right:
        middle = (left + right + 1) // 2
        data["content"] = content[:middle]
        if _size(data) <= budget:
            left = middle
        else:
            right = middle - 1
    return hit.model_copy(update={"content": content[:left]}) if left else None


def search(db: Connection, access: AgentAccess, request: SearchRequest) -> SearchResult:
    vector = None
    if model_enabled():
        try:
            vector = embedding(request.query, query=True)
        except ModelUnavailable:
            pass

    budget = min(CONTEXT_BUDGET, request.token_budget or CONTEXT_BUDGET)
    used = 2
    evidence = []
    for row in _matches(db, access, request.query, vector):
        memory = MemorySnapshot.model_validate(row["snapshot"])
        hit = Evidence(
            memory_id=memory.id,
            revision=memory.revision,
            category=memory.category,
            content=memory.content[:700],
            source_id=memory.source.id,
            observed_at=memory.source.observed_at,
            updated_at=memory.updated_at,
            relevance=round(float(row["relevance"]), 6),
            claims=[
                ClaimEvidence(
                    id=claim.id,
                    source_id=claim.source.id,
                    predicate=claim.predicate,
                    status=claim.status,
                    confidence=claim.confidence,
                )
                for claim in memory.claims[:3]
            ],
        )
        separator = int(bool(evidence))
        fitted = _fit(hit, budget - used - separator)
        if fitted is not None:
            evidence.append(fitted)
            used += separator + _size(fitted.model_dump(mode="json"))
        if len(evidence) == request.limit:
            break

    return SearchResult(
        mode="hybrid" if vector is not None else "lexical",
        token_budget=budget,
        context_bytes=used,
        evidence=evidence,
    )
