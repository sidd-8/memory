from __future__ import annotations

import os
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import sleep
from types import TracebackType
from typing import Literal, Self
from uuid import UUID, uuid4

import httpx2
from pydantic import BaseModel, TypeAdapter, ValidationError

from agent_memory.errors import ProtocolError, TransportError, response_error
from agent_memory.models import (
    Claim,
    ClaimReview,
    MemoryCreate,
    MemoryDetail,
    MemoryPage,
    MemoryPatch,
    MemorySnapshot,
    Neighborhood,
    SearchRequest,
    SearchResult,
)

_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_CLAIMS = TypeAdapter(list[Claim])


def _retry_delay(value: str | None, attempt: int) -> float:
    if value is None:
        return 0.25 * 2**attempt
    try:
        if value.isdigit():
            return float(value)
        until = parsedate_to_datetime(value)
        return max(0, (until - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0.25 * 2**attempt


def _params(**values: str | UUID | int | datetime | None) -> dict[str, str]:
    params = {}
    for name, value in values.items():
        if isinstance(value, datetime):
            if value.utcoffset() is None:
                raise ValueError(f"{name} must have a timezone")
            params[name] = value.isoformat()
        elif value is not None:
            params[name] = str(value)
    return params


def _decode[T: BaseModel](model: type[T], response: httpx2.Response) -> T:
    try:
        return model.model_validate_json(response.content)
    except ValidationError:
        raise ProtocolError(f"Invalid {model.__name__} response") from None


class MemoryClient:
    """Synchronous client scoped to one workspace and subject."""

    def __init__(
        self,
        *,
        base_url: str,
        workspace_id: UUID | str,
        subject_id: UUID | str,
        api_key: str,
        timeout: float = 15,
        max_retries: int = 2,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        url = httpx2.URL(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "base_url must be an HTTP(S) URL without credentials, query, or fragment"
            )
        if (
            not api_key.strip()
            or not api_key.isascii()
            or not api_key.isprintable()
            or any(char.isspace() for char in api_key)
        ):
            raise ValueError("api_key must be a nonempty bearer token")
        if not 0 <= max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5")
        self.workspace_id = UUID(str(workspace_id))
        self.subject_id = UUID(str(subject_id))
        self._max_retries = max_retries
        self._client = httpx2.Client(
            base_url=url,
            headers={"Authorization": "Bearer " + api_key},
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    @classmethod
    def from_env(cls, *, prefix: str = "AGENT_MEMORY_") -> Self:
        settings = {}
        for name in ("BASE_URL", "WORKSPACE_ID", "SUBJECT_ID", "API_KEY"):
            value = os.environ.get(prefix + name)
            if not value:
                raise ValueError(f"Set {prefix}{name}")
            settings[name] = value
        return cls(
            base_url=settings["BASE_URL"],
            workspace_id=settings["WORKSPACE_ID"],
            subject_id=settings["SUBJECT_ID"],
            api_key=settings["API_KEY"],
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        data: BaseModel | None = None,
        idempotency_key: str | None = None,
    ) -> httpx2.Response:
        query = _params(workspace_id=self.workspace_id, subject_id=self.subject_id)
        query.update(params or {})
        headers = (
            {"Idempotency-Key": idempotency_key} if idempotency_key is not None else {}
        )
        retries = (
            self._max_retries if method == "GET" or idempotency_key is not None else 0
        )
        body = (
            data.model_dump(mode="json", exclude_unset=True)
            if data is not None
            else None
        )
        for attempt in range(retries + 1):
            try:
                response = self._client.request(
                    method, path, params=query, headers=headers, json=body
                )
            except httpx2.RequestError:
                if attempt == retries:
                    raise TransportError(
                        "Could not complete the HTTP request"
                    ) from None
                sleep(_retry_delay(None, attempt))
                continue
            if response.is_success:
                return response
            if attempt < retries and response.status_code in _RETRY_STATUSES:
                delay = _retry_delay(response.headers.get("retry-after"), attempt)
                if delay <= 30:
                    sleep(delay)
                    continue
            raise response_error(response)
        raise AssertionError("Unreachable retry state")

    def add(
        self, memory: MemoryCreate, *, idempotency_key: str | None = None
    ) -> MemorySnapshot:
        response = self._request(
            "POST",
            "v1/memories",
            data=memory,
            idempotency_key=idempotency_key
            if idempotency_key is not None
            else str(uuid4()),
        )
        return _decode(MemorySnapshot, response)

    def list(self, *, limit: int = 20, after: UUID | str | None = None) -> MemoryPage:
        response = self._request(
            "GET",
            "v1/memories",
            params=_params(
                limit=limit,
                after=UUID(str(after)) if after is not None else None,
            ),
        )
        return _decode(MemoryPage, response)

    def get(
        self,
        memory_id: UUID | str,
        *,
        revision_limit: int = 20,
        revision_before: int | None = None,
    ) -> MemoryDetail:
        response = self._request(
            "GET",
            f"v1/memories/{UUID(str(memory_id))}",
            params=_params(
                revision_limit=revision_limit, revision_before=revision_before
            ),
        )
        return _decode(MemoryDetail, response)

    def update(
        self,
        memory_id: UUID | str,
        patch: MemoryPatch,
        *,
        idempotency_key: str | None = None,
    ) -> MemorySnapshot:
        response = self._request(
            "PATCH",
            f"v1/memories/{UUID(str(memory_id))}",
            data=patch,
            idempotency_key=idempotency_key
            if idempotency_key is not None
            else str(uuid4()),
        )
        return _decode(MemorySnapshot, response)

    def search(self, query: SearchRequest | str) -> SearchResult:
        data = SearchRequest(query=query) if isinstance(query, str) else query
        return _decode(
            SearchResult, self._request("POST", "v1/memories/search", data=data)
        )

    def list_claims(
        self,
        memory_id: UUID | str,
        *,
        valid_at: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Claim]:
        response = self._request(
            "GET",
            f"v1/memories/{UUID(str(memory_id))}/claims",
            params=_params(valid_at=valid_at, limit=limit, offset=offset),
        )
        try:
            return _CLAIMS.validate_json(response.content)
        except ValidationError:
            raise ProtocolError("Invalid claim list response") from None

    def verify(
        self,
        memory_id: UUID | str,
        review: ClaimReview,
        *,
        idempotency_key: str | None = None,
    ) -> Claim:
        response = self._request(
            "POST",
            f"v1/memories/{UUID(str(memory_id))}/verify",
            data=review,
            idempotency_key=idempotency_key
            if idempotency_key is not None
            else str(uuid4()),
        )
        return _decode(Claim, response)

    def delete(self, memory_id: UUID | str) -> None:
        response = self._request("DELETE", f"v1/memories/{UUID(str(memory_id))}")
        if response.status_code != 204:
            raise ProtocolError("Expected HTTP 204 from memory deletion")

    def graph(
        self,
        entity_id: UUID | str,
        *,
        depth: Literal[1, 2] = 1,
        edge_limit: int = 50,
        valid_at: datetime | None = None,
    ) -> Neighborhood:
        response = self._request(
            "GET",
            f"v1/entities/{UUID(str(entity_id))}/graph",
            params=_params(depth=depth, edge_limit=edge_limit, valid_at=valid_at),
        )
        return _decode(Neighborhood, response)
