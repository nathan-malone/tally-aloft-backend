# tally-aloft-backend

The Tally Aloft backend: one async Python 3.14 codebase for the API, the background workers and the scheduler, shipped as one container image. The design is in [tally-aloft-docs](https://github.com/nathan-malone/tally-aloft-docs): start with its README, then the backend structure and tech-stack documents.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/), at exactly the version in `pyproject.toml`'s `required-version` (0.11.14 today). Any other version stops with an error. With the standalone installer, `uv self update 0.11.14` switches to it; otherwise install that version with your package manager. uv installs Python 3.14 itself.
- [Docker](https://docs.docker.com/get-started/get-docker/) with Compose, running. The test suite and the container mode need it.

Every command below works in PowerShell and in POSIX shells such as bash and zsh. Run them from the repository root.

## Run the API on the host

This is the faster loop: Uvicorn reloads when the code changes.

```
uv sync
cp .env.example .env
uv run --env-file .env uvicorn tally_aloft.main:app --reload --no-proxy-headers
```

Then open <http://127.0.0.1:8000/livez>, which answers `200` with an empty body. Stop the server with Ctrl+C.

`.env` is git-ignored. Edit it to change your local settings. `uv run --env-file` loads it, and the app itself never reads it.

## Run everything in containers

```
docker compose --profile app up --build
```

This builds the image from the `Dockerfile` and starts the API on <http://127.0.0.1:8000/livez>, bound to `127.0.0.1` only. The container's settings are in `compose.yaml`, not in `.env`. Stop it with Ctrl+C, and run `docker compose --profile app down` to remove the containers.

## Checks

Every one must pass before a commit, and CI runs the same commands on each pull request:

```
uv run ruff check
uv run ruff format --check
uv run pyrefly check
uv run lint-imports
uv run pytest
```

`pytest` builds and runs the image for the tests marked `container`, so Docker must be running. `uv run pytest -m "not container"` skips them for a quick loop.

## Settings

The processes read environment variables only, all prefixed `TALLY_`, and ignore unknown ones. Durations are integer seconds, lists are comma-separated, and maps are JSON. A missing or invalid variable stops the process before it serves, with one line that names the variable and its error type.

| Variable | Format | Default | Required |
| --- | --- | --- | --- |
| `TALLY_ENVIRONMENT` | `development`, `test`, `staging` or `production` | none | yes |
| `TALLY_FORWARDED_ALLOW_IPS` | Comma-separated addresses or CIDR networks of the proxies whose `X-Forwarded-*` headers are trusted. `*` and catch-all networks such as `0.0.0.0/0` are rejected, and an empty value trusts none. | `127.0.0.1` | no |
