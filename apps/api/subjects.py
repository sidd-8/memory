from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.auth import AgentAccess, Database, Scope, require_subject

router = APIRouter()


class Subject(BaseModel):
    id: UUID
    workspace_id: UUID
    kind: Literal["user", "project", "team"]
    reference: str


@router.get("/v1/workspaces/{workspace_id}/subjects/{subject_id}")
def get_subject(
    access: Annotated[AgentAccess, Depends(require_subject(Scope.SUBJECT_READ))],
    db: Database,
) -> Subject:
    row = db.execute(
        """
        SELECT id, workspace_id, kind, reference
        FROM subjects WHERE workspace_id = %s AND id = %s
        """,
        (access.workspace_id, access.subject_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Subject unavailable")
    return Subject.model_validate(row)
