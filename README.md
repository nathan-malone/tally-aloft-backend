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
| `make openapi.fix` | Write `openapi.json` from the API's schema, after changing a route |
| `make datastores.up` | Start the local data stores in containers and wait until they're healthy |
| `make migrations.apply` | Upgrade the database in `.env` to the latest migration |
| `make migrations.create NAME="add flights"` | Write a migration for the table models' changes |
| `make packages.outdated.show` | Show which direct dependencies have newer releases |
| `make packages.update PACKAGE=ruff` | Upgrade one package in `uv.lock` or `pnpm-lock.yaml`, or every package without `PACKAGE` |

Make doesn't print the commands it runs. `make -n <target>` prints them without running them. The conventions behind the targets are in the docs' [make commands](https://github.com/nathan-malone/tally-aloft-docs/blob/main/technical/make-commands.md).

## Run the API on the host

This is the faster loop: Uvicorn reloads when the code changes.

```
make packages.install
cp .env.example .env
make datastores.up
make migrations.apply
make api.run
```

`make datastores.up` starts Postgres and Valkey in containers, on `127.0.0.1:5432` and `127.0.0.1:6379`, so nothing else may use those ports. `make migrations.apply` brings its database up to the latest migration. Then open <http://127.0.0.1:8000/readyz>, which answers `200` when the API can reach Postgres and Valkey. Stop the server with Ctrl+C.

`.env` is git-ignored. Edit it to change your local settings. `make api.run` and the migration targets load it with `uv run --env-file`, and the app itself never reads it.

## Run everything in containers

```
make containers.up
```

This builds the image from the `Dockerfile`, starts Postgres and Valkey, runs the migrations in a `migrate` container once Postgres is healthy, and then, once Valkey is healthy too, starts the API on <http://127.0.0.1:8000/readyz>, bound to `127.0.0.1` only. The containers' settings are in `compose.yaml`, not in `.env`. Stop them with Ctrl+C, and run `make containers.down` to remove the containers.

The database lives in a Docker volume, so it survives `make containers.down`. `make containers.down ARGS=--volumes` deletes it as well, and the next start creates the roles again from `compose/postgres-init.sql`.

## Migrations

Alembic runs the migrations in `migrations/`, as the owner role, `tally_aloft_owner`, from `TALLY_MIGRATION_DATABASE_URL`. The services connect as `tally_aloft_app` and never read that variable. The image's migration command is `alembic upgrade head`, run from `/app` before the services update.

- **Grants live in migrations.** The first revision gives `tally_aloft_app` `SELECT` and `INSERT` on every table the owner creates, and no DDL. A revision that creates a table the application must change grants it `UPDATE` or `DELETE` itself, as in `op.execute("GRANT UPDATE ON flights TO tally_aloft_app")`, so a table is append-only unless its migration says otherwise. A `serial` column or a `DEFAULT nextval()` also needs `GRANT USAGE` on its sequence; identity columns and `uuidv7()` keys don't.
- **Expand, then contract.** Old and new code run side by side during a deploy, so add, backfill and switch in one release, and remove in a later one.
- **Timeouts.** Every migration session sets `lock_timeout` (`TALLY_MIGRATION_LOCK_TIMEOUT`, 5 seconds by default), so a migration that waits on a lock fails instead of blocking the tables behind it. Migrations run with no `statement_timeout`.
- **Concurrent indexes.** `CREATE INDEX CONCURRENTLY` can't run in a transaction. Build it in its own revision, inside an autocommit block, where the lock timeout still applies:

  ```python
  def upgrade() -> None:
      with op.get_context().autocommit_block():
          valid: bool | None = op.get_bind().scalar(
              sa.text(
                  "SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"
              ),
              {"name": "ix_flights_pilot_id"},
          )
          if valid is False:
              op.drop_index("ix_flights_pilot_id", postgresql_concurrently=True)
          if valid is not True:
              op.create_index(
                  "ix_flights_pilot_id",
                  "flights",
                  ["pilot_id"],
                  postgresql_concurrently=True,
              )
  ```

  A concurrent build that fails, on the lock timeout for example, leaves an `INVALID` index behind, so a rerun drops it and builds again. A valid index of that name is kept, not rebuilt, such as one an operator built ahead of the deploy, or one whose build committed before the task stopped. Only the name is checked, so an index built ahead of a deploy must match the revision's definition exactly. Each revision runs in its own transaction, because the block commits everything before it.
- **New revisions.** Change the table models in `src/tally_aloft/adapters/outbound/postgres/tables/`, then run `make migrations.create NAME="add flights"` against a database at the latest migration. Alembic compares the two, writes `migrations/versions/<date>_<revision>_add_flights.py` from `migrations/script.py.mako`, and Ruff fixes and formats it. Read it before committing: autogenerate misses some changes, such as renames, and it never writes grants.

## Checks

`make check` must pass before a commit, and CI runs the same targets on each pull request. It runs, in order, `make lint.check` (linting and formatting), `make types.check` (types), `make boundaries.check` (module boundaries), `make slop.check` (patterns AI coding agents leave behind), `make openapi.check` (whether `openapi.json` matches the API's schema) and `make test` (tests).

`make test` builds and runs the image for the tests marked `container`, so Docker must be running. `make check.fast` and `make test.fast` skip those tests for a quick loop.

Tests run in parallel, one pytest-xdist worker per logical CPU. `make test ARGS="-n0"` runs them in one process, which `--pdb` needs, and `PYTEST_XDIST_AUTO_NUM_WORKERS=<n>` caps the worker count.

## Settings

The processes read environment variables only, all prefixed `TALLY_`, and ignore unknown ones. Durations are integer seconds, lists are comma-separated, and maps are JSON. A missing or invalid variable stops the process before it serves, with one line that names the variable and its error type.

| Variable | Format | Default | Required |
| --- | --- | --- | --- |
| `TALLY_ENVIRONMENT` | `development`, `test`, `staging` or `production`. The migration command reads it too. | none | yes |
| `TALLY_FORWARDED_ALLOW_IPS` | Comma-separated addresses or CIDR networks of the proxies whose `X-Forwarded-*` headers are trusted. `*` and catch-all networks such as `0.0.0.0/0` are rejected, and an empty value trusts none. | `127.0.0.1` | no |
| `TALLY_CORS_ORIGINS` | Comma-separated browser origins that CORS allows, such as `https://tallyaloft.app`. Each is exactly a lowercase `scheme://host[:port]`, with no path and no default port. `*` is rejected. | none | no |
| `TALLY_DATABASE_URL` | The application role's database, as a SQLAlchemy URL for asyncpg: `postgresql+asyncpg://user:password@host:port/database`, with reserved characters in the user and password percent-encoded. In `staging` and `production` it must end in `?ssl=verify-full`, with no `host` parameter, and `PGSSLROOTCERT` names the server's CA bundle. Secret. | none | yes |
| `TALLY_DATABASE_POOL_SIZE` | Connections each process keeps in its pool, at least 1. | `5` | no |
| `TALLY_DATABASE_MAX_OVERFLOW` | Extra connections a process may open when its pool is busy, at least 0. A process opens at most pool size plus overflow. | `5` | no |
| `TALLY_DATABASE_STATEMENT_TIMEOUT` | Seconds a statement may run before Postgres cancels it, at least 1. | `30` | no |
| `TALLY_VALKEY_URL` | The cache's Valkey: `redis://[user:password@]host[:port][/database]`, or `rediss://` for TLS, with reserved characters in the user and password percent-encoded and no query. In `staging` and `production` it must be `rediss://` and carry a password: an AUTH token (`rediss://:token@host`) or a user's. Secret. | none | yes |
| `TALLY_BROKER_IDLE_TIMEOUT` | Seconds a worker's task message may go unacknowledged before another worker may reclaim it, at least 1. It must be greater than every task's timeout, or the worker fails at import, naming both values. | `900` | no |
| `TALLY_BROKER_STREAM_MAXLEN` | The approximate number of messages the broker's stream is trimmed to, at least 1. A trimmed message's job is recovered from its row. | `100000` | no |
| `TALLY_MIGRATION_DATABASE_URL` | The owner role's database, in `TALLY_DATABASE_URL`'s format and with its `ssl` and `host` rules, for migrations only. Secret. | none | for migrations |
| `TALLY_MIGRATION_LOCK_TIMEOUT` | Seconds a migration waits for a lock before it fails, at least 1. | `5` | no |

Only the migration command reads the `TALLY_MIGRATION_*` variables, so the services never get the owner role's credentials. On the host they share `.env` with the rest, for convenience; in Compose's `app` profile and in AWS only the migration task gets them.
