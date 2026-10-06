# MetaSpike application image. One image, several Cloud Run jobs, each with its own
# command and identity. Private: it is never published and serves nothing.
#
# The image contains the application and its locked runtime dependencies. It contains
# no credential, no .env, no local state and no test fixture: see .dockerignore.

FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, exactly as locked, so this layer is reused until the lock changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-editable


FROM python:3.12-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Not root: the jobs need no privilege and write nothing to the image.
RUN groupadd --system --gid 10001 metaspike \
    && useradd --system --uid 10001 --gid metaspike --no-create-home metaspike

COPY --from=build --chown=root:root /app/.venv /app/.venv

USER 10001:10001
WORKDIR /tmp

ENTRYPOINT ["metaspike"]
CMD ["--help"]
