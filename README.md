# tally-aloft-backend

The Tally Aloft backend: one async Python 3.14 codebase for the API, the background workers and the scheduler, shipped as one container image. The design is in [tally-aloft-docs](https://github.com/nathan-malone/tally-aloft-docs): start with its README, then the backend structure and tech-stack documents.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/), at exactly the version in `pyproject.toml`'s `required-version` (0.11.14 today). Any other version stops with an error. With the standalone installer, `uv self update 0.11.14` switches to it; otherwise install that version with your package manager. uv installs Python 3.14 itself.
- [Node.js](https://nodejs.org/) 20 or later, and pnpm at the version in `package.json`'s `packageManager` (12.14.0 today). Node runs the npm tools that some checks use. Run `corepack enable pnpm` once (Corepack ships with Node 24 and earlier). On Windows, run `corepack enable --install-directory "$env:APPDATA\npm" pnpm` from PowerShell instead, which needs no administrator shell. Without Corepack, run `npm install --global pnpm@12.14.0`.
- [Docker](https://docs.docker.com/get-started/get-docker/) with Compose, running. The test suite and the container mode need it.
- [GNU Make](https://www.gnu.org/software/make/) 3.81 or later. macOS has it with the Xcode Command Line Tools, and Linux distributions package it as `make`. On Windows, run `winget install ezwinports.make` or `choco install make`; it works from PowerShell, with no bash needed.

Every command below works in PowerShell and in POSIX shells such as bash and zsh. Run them from the repository root.

## Commands

The `Makefile` holds every developer and CI command. Run `make` on its own to list the targets. The most used are:

| Command | What it does |
| --- | --- |
| `make packages.install` | Install the locked dependencies |
| `make lint.fix` | Apply the linter's safe fixes and format the code |
| `make check` | Every check and the full test suite, as CI runs them |
| `make check.fast` | The same, without the tests that need Docker |
| `make test ARGS="-k settings -x"` | Run tests, passing extra arguments to the test runner |
| `make packages.outdated.show` | Show which direct dependencies have newer releases |
| `make packages.update PACKAGE=ruff` | Upgrade one package in `uv.lock` or `pnpm-lock.yaml`, or every package without `PACKAGE` |

Make doesn't print the commands it runs. `make -n <target>` prints them without running them. The conventions behind the targets are in the docs' [make commands](https://github.com/nathan-malone/tally-aloft-docs/blob/main/technical/make-commands.md).

## Run the API on the host

This is the faster loop: Uvicorn reloads when the code changes.

```
make packages.install
cp .env.example .env
make api.run
```

Then open <http://127.0.0.1:8000/livez>, which answers `200` with an empty body. Stop the server with Ctrl+C.

`.env` is git-ignored. Edit it to change your local settings. `make api.run` loads it with `uv run --env-file`, and the app itself never reads it.

## Run everything in containers

```
make containers.up
```

This builds the image from the `Dockerfile` and starts the API on <http://127.0.0.1:8000/livez>, bound to `127.0.0.1` only. The container's settings are in `compose.yaml`, not in `.env`. Stop it with Ctrl+C, and run `make containers.down` to remove the containers.

## Checks

`make check` must pass before a commit, and CI runs the same targets on each pull request. It runs, in order, `make lint.check` (linting and formatting), `make types.check` (types), `make boundaries.check` (module boundaries), `make slop.check` (patterns AI coding agents leave behind) and `make test` (tests).

`make test` builds and runs the image for the tests marked `container`, so Docker must be running. `make check.fast` and `make test.fast` skip those tests for a quick loop.

Tests run in parallel, one pytest-xdist worker per logical CPU. `make test ARGS="-n0"` runs them in one process, which `--pdb` needs, and `PYTEST_XDIST_AUTO_NUM_WORKERS=<n>` caps the worker count.

## Settings

The processes read environment variables only, all prefixed `TALLY_`, and ignore unknown ones. Durations are integer seconds, lists are comma-separated, and maps are JSON. A missing or invalid variable stops the process before it serves, with one line that names the variable and its error type.

| Variable | Format | Default | Required |
| --- | --- | --- | --- |
| `TALLY_ENVIRONMENT` | `development`, `test`, `staging` or `production` | none | yes |
| `TALLY_FORWARDED_ALLOW_IPS` | Comma-separated addresses or CIDR networks of the proxies whose `X-Forwarded-*` headers are trusted. `*` and catch-all networks such as `0.0.0.0/0` are rejected, and an empty value trusts none. | `127.0.0.1` | no |
