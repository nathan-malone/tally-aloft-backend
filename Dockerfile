# One image for the API, the workers and the scheduler; they differ by command.
# The base is pinned by its multi-arch index digest, so the same file builds
# linux/arm64 (CI and AWS) and linux/amd64 (x86 developer machines).
#
# Stages: `builder` installs the environment, `release` is the shipped image,
# and `dev` adds tests/ for compose.e2e.yaml's test worker. The last stage is
# the default target, so a plain build is always the release image.

FROM python:3.14-slim-trixie@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8 AS builder

# The version matches [tool.uv] required-version in pyproject.toml.
COPY --from=ghcr.io/astral-sh/uv:0.11.14@sha256:1025398289b62de8269e70c45b91ffa37c373f38118d7da036fb8bb8efc85d97 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so this layer is reused until the lockfile changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# uv_build reads README.md as the package's readme, so the build needs it.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.14-slim-trixie@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8 AS release

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
# Only the virtual environment and the migrations ship: no source tree, tests,
# uv or .env. `alembic upgrade head`, run from /app, is the migration command.
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH"

USER 10001
EXPOSE 8000

# Uvicorn runs as PID 1 and handles SIGTERM itself: it stops accepting
# connections and drains in-flight requests for up to 25 seconds. Its own
# proxy handling is off; create_app applies the trusted proxies from settings.
CMD ["uvicorn", "tally_aloft.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--timeout-graceful-shutdown", "25"]


# The release image plus tests/, so the test worker's job kinds can run in
# Compose. Never shipped.
FROM release AS dev

COPY tests ./tests


# The default target: the release image, with no tests.
FROM release
