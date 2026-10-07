# MetaSpike application image. One image, several Cloud Run jobs, each with its own
# command and identity. Private: it is never published and serves nothing.
#
# The image contains the application and its locked runtime dependencies, and, beside
# them in a separate environment, the locked dbt that the warehouse job runs. The two
# environments are resolved and locked independently (uv.lock and warehouse/uv.lock)
# and neither imports the other: `metaspike warehouse` starts /opt/dbt/bin/dbt as its
# own process. The image contains no credential, no .env, no local state and no test
# fixture: see .dockerignore.
#
# The base is the official image pinned by its multi-architecture index digest, so a
# build never silently moves to a different base. The tag is kept for people; the
# digest is what is used. To refresh: resolve the tag's current digest from the
# registry, replace it in BOTH stages, rebuild with --pull --no-cache, and rescan.

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS build

# The uv installer, taken from its official image and pinned the same way as the base:
# tag for people, multi-architecture index digest for the build.
COPY --from=ghcr.io/astral-sh/uv:0.5@sha256:7bff3c3776ec467fc1437960f2c469d8beb30f536a6465a3350c647ccd260ec2 /uv /usr/local/bin/uv

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

# dbt, exactly as locked, in its own environment. Its development group (the offline
# DuckDB adapter the tests use) is left out: production needs only dbt and BigQuery.
COPY warehouse/pyproject.toml warehouse/uv.lock /dbt/
RUN UV_PROJECT_ENVIRONMENT=/opt/dbt uv sync --project /dbt --locked --no-dev --no-install-project


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
COPY --from=build --chown=root:root /opt/dbt /opt/dbt

# The dbt project: models, macros, tests and the profile. Read-only, like the rest of
# /app; dbt writes its working files under /tmp.
COPY --chown=root:root warehouse/dbt_project.yml warehouse/profiles.yml warehouse/selectors.yml /app/warehouse/
COPY --chown=root:root warehouse/macros /app/warehouse/macros
COPY --chown=root:root warehouse/models /app/warehouse/models
COPY --chown=root:root warehouse/tests /app/warehouse/tests

USER 10001:10001
WORKDIR /tmp

ENTRYPOINT ["metaspike"]
CMD ["--help"]
