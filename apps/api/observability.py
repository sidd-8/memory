import json
import logging
from collections import defaultdict
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_METHODS = {"GET", "POST", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def install_observability(app: FastAPI) -> None:
    logger = logging.getLogger("agent_memory.requests")
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
        logger.setLevel(logging.INFO)
        logger.propagate = False
    metrics = defaultdict(lambda: [0, 0.0])
    lock = Lock()

    @app.middleware("http")
    async def observe(request: Request, call_next):
        started = perf_counter()
        request_id = uuid4().hex
        failure = None
        try:
            response = await call_next(request)
        except Exception as exc:
            failure = type(exc).__name__
            response = JSONResponse(
                {"detail": "Internal server error"}, status_code=500
            )

        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        elapsed = perf_counter() - started
        route = getattr(request.scope.get("route"), "path", "unmatched")
        method = request.method if request.method in _METHODS else "OTHER"
        status = response.status_code
        if route != "/metrics":
            with lock:
                series = metrics[method, route, status]
                series[0] += 1
                series[1] += elapsed
            if route not in {"/healthz", "/readyz"} or status >= 400:
                logger.info(
                    json.dumps(
                        {
                            "time": datetime.now(UTC).isoformat(),
                            "event": "http_request",
                            "request_id": request_id,
                            "method": method,
                            "route": route,
                            "status": status,
                            "duration_ms": round(elapsed * 1000, 3),
                            **({"error_type": failure} if failure else {}),
                        },
                        separators=(",", ":"),
                    )
                )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, error):
        return JSONResponse(
            {
                "detail": [
                    {"loc": issue["loc"], "msg": issue["msg"], "type": issue["type"]}
                    for issue in error.errors()
                ]
            },
            status_code=422,
        )

    @app.get("/metrics", include_in_schema=False)
    def export_metrics():
        lines = [
            "# TYPE agent_memory_http_requests_total counter",
            "# TYPE agent_memory_http_duration_seconds summary",
        ]
        with lock:
            for (method, route, status), (count, seconds) in sorted(metrics.items()):
                labels = f'method="{method}",route="{route}",status="{status}"'
                lines.extend(
                    (
                        f"agent_memory_http_requests_total{{{labels}}} {count}",
                        f"agent_memory_http_duration_seconds_count{{{labels}}} {count}",
                        f"agent_memory_http_duration_seconds_sum{{{labels}}} {seconds:.6f}",
                    )
                )
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
