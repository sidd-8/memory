FROM python:3.13-slim-bookworm AS backend
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
COPY packages/sdk-python/pyproject.toml packages/sdk-python/
RUN uv sync --locked --no-dev --no-install-workspace

COPY apps/api apps/api
COPY worker worker
COPY alembic.ini .
COPY migrations migrations

FROM backend AS test
COPY packages/sdk-python/src packages/sdk-python/src
RUN uv sync --locked --all-packages
COPY tests tests
CMD ["python", "-m", "pytest", "-q"]

FROM backend AS runtime
