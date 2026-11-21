import json
import os
from datetime import datetime
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID

from psycopg import Connection
from pydantic import AliasChoices, BaseModel, Field, model_validator

from apps.api.auth import AgentAccess
from apps.api.memory_models import (
    Category,
    ClaimStatus,
    Input,
    MemorySnapshot,
    MemoryState,
    Reference,
    TimeRange,
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
    query: Reference | None = None
    time_range: TimeRange | None = None
    category: Category | None = None
    status: MemoryState | None = Field(
        default=None, validation_alias=AliasChoices("status", "state")
    )
    limit: Annotated[int, Field(ge=1, le=20)] = 5
    token_budget: Annotated[int, Field(ge=256, le=16384)] | None = None

    @model_validator(mode="after")
    def require_criteria(self):
        if all(
            value is None
            for value in (self.query, self.time_range, self.status, self.category)
        ):
            raise ValueError("Provide a query or filter")
        return self


class ClaimEvidence(BaseModel):
    id: UUID
    source_id: UUID
    predicate: str
    value: str | None
    status: ClaimStatus
    confidence: float | None
    valid_from: datetime | None
    valid_to: datetime | None
    verified_at: datetime | None
    review_due_at: datetime | None


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
    starts_at: datetime | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    ends_at: datetime | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    timezone: str | None = Field(default=None, exclude_if=lambda value: value is None)
    state: MemoryState | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class SearchResult(BaseModel):
    mode: Literal["hybrid", "lexical", "filter"]
    token_budget: int
    context_bytes: int
    evidence: list[Evidence]


def _matches(
    db: Connection, access: AgentAccess, request: SearchRequest, vector: str | None
):
    scope = (access.workspace_id, access.subject_id)
    start = request.time_range.starts_at if request.time_range else None
    end = request.time_range.ends_at if request.time_range else None
    return db.execute(
        """
        WITH query AS (
            SELECT websearch_to_tsquery('english', %s::text) AS terms, %s::public.vector AS vector
        ), eligible AS NOT MATERIALIZED (
            SELECT m.* FROM memories m
            WHERE m.workspace_id = %s AND m.subject_id = %s
                AND (m.expires_at IS NULL OR m.expires_at > statement_timestamp())
                AND (%s::timestamptz IS NULL OR m.time_span && tstzrange(%s, %s, '[)'))
                AND (%s::text IS NULL OR m.state = %s)
                AND (%s::text IS NULL OR m.category = %s)
                AND (
                    NOT EXISTS (
                        SELECT 1 FROM claims c
                        WHERE (c.workspace_id, c.subject_id, c.memory_id)
                            = (m.workspace_id, m.subject_id, m.id)
                            AND c.verified_at IS NOT NULL AND c.status IN ('superseded', 'archived')
                    ) OR EXISTS (
                        SELECT 1 FROM claims c
                        WHERE (c.workspace_id, c.subject_id, c.memory_id)
                            = (m.workspace_id, m.subject_id, m.id)
                            AND c.status NOT IN ('superseded', 'archived')
                    )
                )
        ), lexical AS (
            SELECT id, row_number() OVER (
                ORDER BY ts_rank_cd(search_text, query.terms) DESC NULLS LAST,
                    starts_at NULLS LAST, id
            ) AS rank
            FROM eligible CROSS JOIN query
            WHERE query.terms IS NULL OR search_text @@ query.terms
            ORDER BY rank LIMIT 40
        ), distances AS (
            SELECT id, CASE WHEN public.vector_dims(embedding) = %s
                THEN embedding OPERATOR(public.<=>) query.vector END AS distance
            FROM eligible CROSS JOIN query
            WHERE query.vector IS NOT NULL AND embedding_model = %s
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
        JOIN current_memory_snapshots v ON (v.workspace_id, v.subject_id, v.memory_id, v.revision)
            = (m.workspace_id, m.subject_id, m.id, m.revision)
        ORDER BY fused.relevance DESC, m.id
        """,
        (
            request.query,
            vector,
            *scope,
            start,
            start,
            end,
            request.status,
            request.status,
            request.category,
            request.category,
            EMBEDDING_DIMENSIONS,
            EMBEDDING_MODEL,
            *scope,
        ),
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


def search(
    db: Connection, access: AgentAccess, request: SearchRequest
) -> tuple[SearchResult, float]:
    vector = None
    embedding_seconds = 0.0
    if request.query is not None and model_enabled():
        started = perf_counter()
        try:
            vector = embedding(request.query, query=True)
        except ModelUnavailable:
            pass
        finally:
            embedding_seconds = perf_counter() - started

    budget = min(CONTEXT_BUDGET, request.token_budget or CONTEXT_BUDGET)
    used = 2
    evidence = []
    for row in _matches(db, access, request, vector):
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
            starts_at=memory.starts_at,
            ends_at=memory.ends_at,
            timezone=memory.timezone,
            state=memory.state,
            claims=[
                ClaimEvidence(
                    id=claim.id,
                    source_id=claim.source.id,
                    predicate=claim.predicate,
                    value=claim.object_entity.reference
                    if claim.object_entity
                    else claim.object_value,
                    status=claim.status,
                    confidence=claim.confidence,
                    valid_from=claim.valid_from,
                    valid_to=claim.valid_to,
                    verified_at=claim.verified_at,
                    review_due_at=claim.review_due_at,
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

    mode = (
        "filter"
        if request.query is None
        else "hybrid"
        if vector is not None
        else "lexical"
    )
    result = SearchResult(
        mode=mode,
        token_budget=budget,
        context_bytes=used,
        evidence=evidence,
    )
    return result, embedding_seconds
