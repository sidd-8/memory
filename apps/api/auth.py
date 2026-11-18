from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from apps.api.db import request_database


class Scope(StrEnum):
    SUBJECT_READ = "subject:read"
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    MEMORY_VERIFY = "memory:verify"
    MEMORY_DELETE = "memory:delete"
    GRAPH_READ = "graph:read"


@dataclass(frozen=True)
class AgentAccess:
    workspace_id: UUID
    subject_id: UUID
    agent_id: UUID
    key_id: UUID


Database = Annotated[psycopg.Connection, Depends(request_database, scope="function")]
BearerToken = Annotated[
    HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
]


def token_digest(token: str) -> bytes:
    return sha256(token.encode()).digest()


def require_subject(scope: Scope):
    def authorize(
        workspace_id: UUID, subject_id: UUID, credentials: BearerToken, db: Database
    ) -> AgentAccess:
        if credentials is not None:
            key = db.execute(
                """
                SELECT workspace_id, subject_id, agent_id, id AS key_id, scopes
                FROM api_keys
                WHERE token_digest = %s AND revoked_at IS NULL
                """,
                (token_digest(credentials.credentials),),
            ).fetchone()
            if key is not None:
                if (
                    key["workspace_id"] == workspace_id
                    and key["subject_id"] == subject_id
                    and scope in key["scopes"]
                ):
                    return AgentAccess(
                        workspace_id, subject_id, key["agent_id"], key["key_id"]
                    )
                raise HTTPException(status_code=403, detail="Access denied")

        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return authorize
