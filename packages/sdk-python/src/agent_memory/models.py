from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

Category = Literal[
    "semantic", "episodic", "procedural", "project", "working", "commitment"
]
TrustClass = Literal["unverified", "agent_reported", "user_confirmed", "authoritative"]
ClaimStatus = Literal[
    "candidate", "active", "review_due", "stale", "superseded", "archived"
]
MemoryState = Literal["tentative", "confirmed", "cancelled", "superseded", "completed"]


class SourceInput(BaseModel):
    origin: str
    source_ref: str
    observed_at: AwareDatetime
    trust_class: TrustClass = "unverified"


class EntityInput(BaseModel):
    kind: str
    reference: str


class ClaimInput(BaseModel):
    predicate: str
    subject_entity: EntityInput | None = None
    object_value: str | None = None
    object_entity: EntityInput | None = None
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None


class TimeRange(BaseModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class TemporalFields(BaseModel):
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    timezone: str | None = None
    state: MemoryState | None = None


class MemoryCreate(TemporalFields):
    category: Category
    content: str
    source: SourceInput
    expires_at: AwareDatetime | None = None
    claims: list[ClaimInput] = Field(default_factory=list)
    extract_claims: bool = False


class MemoryPatch(TemporalFields):
    category: Category | None = None
    content: str | None = None
    expires_at: AwareDatetime | None = None
    source: SourceInput | None = None
    claims: list[ClaimInput] | None = None
    extract_claims: bool | None = None


class ClaimReview(BaseModel):
    claim_id: UUID
    outcome: Literal["verified", "needs_review", "archived"]
    supersedes_claim_id: UUID | None = None
    review_interval_days: Annotated[int, Field(ge=1, le=3650)] | None = None


class SearchRequest(BaseModel):
    query: str | None = None
    time_range: TimeRange | None = None
    category: Category | None = None
    status: MemoryState | None = None
    limit: Annotated[int, Field(ge=1, le=20)] = 5
    token_budget: Annotated[int, Field(ge=256, le=16384)] | None = None


class Source(SourceInput):
    id: UUID


class Entity(EntityInput):
    id: UUID


class Claim(ClaimInput):
    id: UUID
    source: Source
    subject_entity: Entity | None
    object_entity: Entity | None
    status: ClaimStatus
    created_at: AwareDatetime
    extraction_model: str | None = None
    superseded_at: AwareDatetime | None = None
    verified_at: AwareDatetime | None = None
    verified_by: UUID | None = None
    review_due_at: AwareDatetime | None = None
    reviewed_at: AwareDatetime | None = None
    reviewed_by: UUID | None = None


class MemorySummary(TemporalFields):
    id: UUID
    workspace_id: UUID
    subject_id: UUID
    category: Category
    content: str
    expires_at: AwareDatetime | None
    revision: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MemorySnapshot(MemorySummary):
    source: Source
    claims: list[Claim]
    changed_by: UUID


class MemoryDetail(MemorySnapshot):
    extraction_status: Literal["disabled", "pending", "complete", "rejected"]
    history: list[MemorySnapshot]
    next_revision_before: int | None


class MemoryPage(BaseModel):
    items: list[MemorySummary]
    next_after: UUID | None


class ClaimEvidence(BaseModel):
    id: UUID
    source_id: UUID
    predicate: str
    value: str | None
    status: ClaimStatus
    confidence: float | None
    valid_from: AwareDatetime | None
    valid_to: AwareDatetime | None
    verified_at: AwareDatetime | None
    review_due_at: AwareDatetime | None


class Evidence(TemporalFields):
    memory_id: UUID
    revision: int
    category: Category
    content: str
    source_id: UUID
    observed_at: AwareDatetime
    updated_at: AwareDatetime
    relevance: float
    claims: list[ClaimEvidence]


class SearchResult(BaseModel):
    mode: Literal["hybrid", "lexical", "filter"]
    token_budget: int
    context_bytes: int
    evidence: list[Evidence]


class Edge(BaseModel):
    from_entity_id: UUID
    to_entity_id: UUID
    predicate: str
    claim_id: UUID
    memory_id: UUID
    source_id: UUID
    status: ClaimStatus
    valid_from: AwareDatetime | None
    valid_to: AwareDatetime | None


class Neighborhood(BaseModel):
    root_id: UUID
    nodes: list[Entity]
    edges: list[Edge]
