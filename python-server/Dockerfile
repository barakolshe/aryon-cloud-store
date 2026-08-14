FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Dependencies first, so the install layer is cached until the lock file changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8081

# Migrate, then serve. Running the migrations here rather than in a separate service keeps
# the schema and the code that expects it in one deployable unit: the server cannot come up
# against a schema it does not match, because a failed `upgrade` stops the container. `exec`
# hands the process over to uvicorn so it still receives docker's stop signal.
# A shell string rather than an entrypoint script -- a script file needs an executable bit
# that a Windows checkout does not reliably carry.
CMD ["sh", "-c", "uv run --frozen --no-dev alembic upgrade head && exec uv run --frozen --no-dev uvicorn app.api.main:app --host 0.0.0.0 --port 8081"]
