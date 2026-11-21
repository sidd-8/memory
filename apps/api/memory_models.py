from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
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
MemoryState = Literal["tentative", "confirmed", "cancelled", "superseded", "completed"]
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


class TimeRange(Input):
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def check_order(self) -> Self:
        if self.starts_at >= self.ends_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class TemporalFields(Input):
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    timezone: ShortText | None = None
    state: MemoryState | None = None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise ValueError("Use a valid IANA time zone") from exc
        return value


class MemoryTiming(TemporalFields):
    @model_validator(mode="after")
    def check_window(self) -> Self:
        if any(
            value is not None
            for value in (self.starts_at, self.ends_at, self.timezone, self.state)
        ):
            if self.starts_at is None or self.ends_at is None or self.timezone is None:
                raise ValueError(
                    "A time window requires starts_at, ends_at, and timezone"
                )
            if self.starts_at >= self.ends_at:
                raise ValueError("ends_at must be after starts_at")
        return self


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


class MemoryCreate(MemoryTiming):
    category: Category
    content: Content
    expires_at: AwareDatetime | None = None
    source: SourceInput
    claims: Claims = Field(default_factory=list)
    extract_claims: bool = False


class MemoryPatch(TemporalFields):
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
        for field in self.model_fields_set - {
            "expires_at",
            *TemporalFields.model_fields,
        }:
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
    superseded_at: datetime | None = None
    verified_at: datetime | None = None
    verified_by: UUID | None = None
    review_due_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewed_by: UUID | None = None


class ClaimReview(Input):
    claim_id: UUID
    outcome: Literal["verified", "needs_review", "archived"]
    supersedes_claim_id: UUID | None = None
    review_interval_days: Annotated[int, Field(ge=1, le=3650)] | None = None

    @model_validator(mode="after")
    def check_review(self) -> Self:
        if self.outcome != "verified" and (
            self.supersedes_claim_id is not None
            or "review_interval_days" in self.model_fields_set
        ):
            raise ValueError("Supersession and review intervals require verification")
        if self.claim_id == self.supersedes_claim_id:
            raise ValueError("A claim cannot supersede itself")
        return self


class MemorySummary(MemoryTiming):
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
