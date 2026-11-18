from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Category = Literal[
    "semantic", "episodic", "procedural", "project", "working", "commitment"
]
TrustClass = Literal["unverified", "agent_reported", "user_confirmed", "authoritative"]
ClaimStatus = Literal[
    "candidate", "active", "review_due", "stale", "superseded", "archived"
]
ExtractionStatus = Literal["disabled", "pending", "complete", "rejected"]
ShortText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=128, pattern=r"^[^\x00]+$"
    ),
]
Reference = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=2048, pattern=r"^[^\x00]+$"
    ),
]
Content = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=20000, pattern=r"^[^\x00]+$"
    ),
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceInput(Input):
    origin: ShortText
    source_ref: Reference
    observed_at: AwareDatetime
    trust_class: TrustClass = "unverified"


class EntityInput(Input):
    kind: ShortText
    reference: Reference


class ClaimInput(Input):
    predicate: ShortText
    subject_entity: EntityInput | None = None
    object_value: Reference | None = None
    object_entity: EntityInput | None = None
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None

    @model_validator(mode="after")
    def check_shape(self) -> Self:
        if (self.object_value is None) == (self.object_entity is None):
            raise ValueError("Provide exactly one of object_value and object_entity")
        if self.object_entity is not None and self.subject_entity is None:
            raise ValueError("An object_entity requires a subject_entity")
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


Claims = Annotated[list[ClaimInput], Field(max_length=32)]


class MemoryCreate(Input):
    category: Category
    content: Content
    expires_at: AwareDatetime | None = None
    source: SourceInput
    claims: Claims = Field(default_factory=list)
    extract_claims: bool = False


class MemoryPatch(Input):
    category: Category | None = None
    content: Content | None = None
    expires_at: AwareDatetime | None = None
    source: SourceInput | None = None
    claims: Claims | None = None
    extract_claims: bool | None = None

    @model_validator(mode="after")
    def check_changes(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for field in self.model_fields_set - {"expires_at"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if ("content" in self.model_fields_set or self.claims) and self.source is None:
            raise ValueError(
                "Provide a source when changing content or submitting claims"
            )
        return self


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
    created_at: datetime
    extraction_model: str | None = None


class MemorySummary(BaseModel):
    id: UUID
    workspace_id: UUID
    subject_id: UUID
    category: Category
    content: str
    expires_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime


class MemorySnapshot(MemorySummary):
    source: Source
    claims: list[Claim]
    changed_by: UUID


class MemoryDetail(MemorySnapshot):
    extraction_status: ExtractionStatus
    history: list[MemorySnapshot]
    next_revision_before: int | None


class MemoryPage(BaseModel):
    items: list[MemorySummary]
    next_after: UUID | None
