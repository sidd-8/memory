from dataclasses import dataclass
from http import HTTPStatus

from httpx2 import Response


class AgentMemoryError(Exception):
    """Base class for SDK request errors."""


class TransportError(AgentMemoryError):
    """The request failed before a complete HTTP response was received."""


class ProtocolError(AgentMemoryError):
    """A successful HTTP response failed validation against the SDK contract."""


class APIError(AgentMemoryError):
    """The API returned an unexpected HTTP status."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.request_id = request_id
        super().__init__(f"HTTP {status_code}: {detail}")


class AuthenticationError(APIError):
    """The API key is missing, invalid, or revoked."""


class PermissionDenied(APIError):
    """The key does not grant the requested operation or scope."""


class NotFound(APIError):
    """The resource is unavailable in the authorized scope."""


class Conflict(APIError):
    """The requested state change or idempotency key conflicts with current state."""


@dataclass(frozen=True)
class ValidationIssue:
    location: tuple[str | int, ...]
    message: str
    code: str


class RequestValidationError(APIError):
    """The API rejected one or more request fields."""

    issues: tuple[ValidationIssue, ...] = ()


class RateLimitError(APIError):
    """The API rate limited the request."""


_ERRORS = {
    401: AuthenticationError,
    403: PermissionDenied,
    404: NotFound,
    409: Conflict,
    422: RequestValidationError,
    429: RateLimitError,
}


def response_error(response: Response) -> APIError:
    status = response.status_code
    try:
        detail = HTTPStatus(status).phrase
    except ValueError:
        detail = "Request failed"
    try:
        payload = response.json()
    except ValueError:
        payload = None
    message = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(message, str):
        detail = message
    error = _ERRORS.get(status, APIError)(
        status, detail, response.headers.get("x-request-id")
    )
    if isinstance(error, RequestValidationError) and isinstance(message, list):
        error.issues = tuple(
            ValidationIssue(tuple(issue["loc"]), issue["msg"], issue["type"])
            for issue in message
            if isinstance(issue, dict)
            and isinstance(issue.get("loc"), list)
            and all(isinstance(part, (str, int)) for part in issue["loc"])
            and isinstance(issue.get("msg"), str)
            and isinstance(issue.get("type"), str)
        )
    return error
