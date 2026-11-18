from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from apps.api import memory_store, search
from apps.api.auth import AgentAccess, Database, Scope, require_subject
from apps.api.memory_models import (
    MemoryCreate,
    MemoryDetail,
    MemoryPage,
    MemoryPatch,
    MemorySnapshot,
)

router = APIRouter(prefix="/v1/memories")
ReadAccess = Annotated[AgentAccess, Depends(require_subject(Scope.MEMORY_READ))]
WriteAccess = Annotated[AgentAccess, Depends(require_subject(Scope.MEMORY_WRITE))]
DeleteAccess = Annotated[AgentAccess, Depends(require_subject(Scope.MEMORY_DELETE))]
IdempotencyKey = Annotated[str | None, Header(min_length=1, max_length=128)]


@router.post("", status_code=201)
def create_memory(
    data: MemoryCreate,
    access: WriteAccess,
    db: Database,
    idempotency_key: IdempotencyKey = None,
) -> MemorySnapshot:
    return memory_store.write_memory(db, access, data, None, idempotency_key)


@router.get("")
def list_memories(
    access: ReadAccess,
    db: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    after: UUID | None = None,
) -> MemoryPage:
    return memory_store.list_memories(db, access, limit, after)


@router.post("/search")
def search_memories(
    data: search.SearchRequest, access: ReadAccess, db: Database
) -> search.SearchResult:
    return search.search(db, access, data)


@router.get("/{memory_id}")
def get_memory(
    memory_id: UUID,
    access: ReadAccess,
    db: Database,
    revision_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    revision_before: Annotated[int | None, Query(ge=1, le=2147483647)] = None,
) -> MemoryDetail:
    return memory_store.get_memory(
        db, access, memory_id, revision_limit, revision_before
    )


@router.patch("/{memory_id}")
def update_memory(
    memory_id: UUID,
    data: MemoryPatch,
    access: WriteAccess,
    db: Database,
    idempotency_key: IdempotencyKey = None,
) -> MemorySnapshot:
    return memory_store.write_memory(db, access, data, memory_id, idempotency_key)


@router.delete("/{memory_id}", status_code=204)
def delete_memory(memory_id: UUID, access: DeleteAccess, db: Database) -> None:
    memory_store.delete_memory(db, access, memory_id)
