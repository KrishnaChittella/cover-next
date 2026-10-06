# MetaSpike application image. One image, several Cloud Run jobs, each with its own
# command and identity. Private: it is never published and serves nothing.
#
# The image contains the application and its locked runtime dependencies. It contains
# no credential, no .env, no local state and no test fixture: see .dockerignore.
#
# The base is the official image pinned by its multi-architecture index digest, so a
# build never silently moves to a different base. The tag is kept for people; the
# digest is what is used. To refresh: resolve the tag's current digest from the
# registry, replace it in BOTH stages, rebuild with --pull --no-cache, and rescan.

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS build

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


FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Security updates for the two base packages the image scan found fixable HIGH and
# CRITICAL vulnerabilities in. The current official base still ships the unfixed
# versions, so they are upgraded here, and only they: this is not a general upgrade.
# No versions are named, so a later Debian revision does not break the build. Remove
# this once a refreshed base digest carries the fixes and the scan passes without it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade \
        libpcre2-8-0 \
        perl-base \
    && rm -rf /var/lib/apt/lists/*

# Not root: the jobs need no privilege and write nothing to the image.
RUN groupadd --system --gid 10001 metaspike \
    && useradd --system --uid 10001 --gid metaspike --no-create-home metaspike

COPY --from=build --chown=root:root /app/.venv /app/.venv

USER 10001:10001
WORKDIR /tmp

ENTRYPOINT ["metaspike"]
CMD ["--help"]
