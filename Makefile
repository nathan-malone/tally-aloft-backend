# Every developer and CI command, in one place. README, CLAUDE.md and the CI
# workflow call these targets rather than the tools, so a command changes here
# only. The rules are in tally-aloft-docs/technical/make-commands.md; the main
# one is that each recipe line is a plain command that runs unchanged under sh
# and under cmd.exe, which GNU Make on Windows uses when no sh.exe is on PATH.

# Recipes run without echoing their commands; `make -n <target>` prints them.
.SILENT:
# `make check` runs its checks one at a time, in order, even under -j.
.NOTPARALLEL:
.DEFAULT_GOAL := help

# Set these on the command line, as in `make test ARGS="-k settings"`. Plain `=`
# ignores same-named environment variables; Windows often sets PLATFORM.
#
# Extra arguments for the underlying tool: the test runner, or Compose for
# containers.down.
ARGS =
# The tag and, optionally, the platform for image.build.
IMAGE = tally-aloft-backend:local
PLATFORM =
# One package to upgrade with packages.update; empty upgrades every package.
PACKAGE =
# The message, and so the file name, of a new migration for migrations.create.
NAME =

# Names the running target, so `make check` output shows which check failed.
banner = $(info ==> $@)

# `$()` keeps the leading spaces, which $(info) would otherwise drop.
.PHONY: help
help:
	$(info Usage: make <target> [VARIABLE=value ...])
	$(info )
	$(info Packages)
	$(info $()  packages.install        Install the locked dependencies; fails if a lockfile is stale)
	$(info $()  packages.lock           Update the lockfiles after editing pyproject.toml or package.json)
	$(info $()  packages.update         Upgrade every locked package, or PACKAGE=<name> only, to releases 7+ days old, then install)
	$(info $()  packages.outdated.show  List direct dependencies, marking those with newer releases)
	$(info )
	$(info Checks)
	$(info $()  lint.check              Check linting and formatting, changing nothing)
	$(info $()  lint.fix                Apply the linter's safe fixes and format, then report what is left)
	$(info $()  types.check             Check types)
	$(info $()  boundaries.check        Check the module-boundary rules)
	$(info $()  slop.check              Check for patterns AI coding agents leave behind)
	$(info $()  openapi.check           Check that openapi.json matches the API's schema)
	$(info $()  openapi.fix             Write openapi.json from the API's schema)
	$(info $()  test                 The full test suite; needs Docker. ARGS="..." goes to the test runner)
	$(info $()  test.fast               The tests that don't need Docker. ARGS="..." goes to the test runner)
	$(info $()  check                   Every check and the full test suite, as CI runs them)
	$(info $()  check.fast              Every check and the tests that don't need Docker)
	$(info )
	$(info Running)
	$(info $()  image.build             Build the image as IMAGE (default $(IMAGE)), for PLATFORM if set)
	$(info $()  datastores.up           Start the local data stores in containers and wait until they're healthy)
	$(info $()  api.run                 Run the API on the host with reload, settings from .env)
	$(info $()  worker.run              Run a worker on the host, settings from .env)
	$(info $()  scheduler.run           Run the scheduler on the host, settings from .env; only one per environment)
	$(info $()  containers.up           Build and run the migrations and every process in containers)
	$(info $()  containers.down         Stop and remove the containers; ARGS=--volumes also deletes the data)
	$(info $()  containers.e2e.up       Like containers.up, with the test worker and its test-only jobs)
	$(info $()  containers.e2e.job.run  Run a dispatched and a never-dispatched test job on the e2e containers, and wait until both succeed)
	$(info )
	$(info Migrations)
	$(info $()  migrations.apply        Upgrade the database in .env to the latest migration)
	$(info $()  migrations.create       Write a migration for the table models' changes, as NAME="add flights")

# Python packages are in pyproject.toml and uv.lock; the npm tools, run with
# Node, are in package.json and pnpm-lock.yaml. Only packages.lock and
# packages.update may rewrite a lockfile. Every other uv command passes
# --locked, and pnpm installs with --frozen-lockfile, so a stale lock fails
# locally as it does in CI. Each package manager ignores a PACKAGE it doesn't
# manage.

.PHONY: packages.install
packages.install:
	uv sync --locked
	pnpm install --frozen-lockfile

.PHONY: packages.lock
packages.lock:
	uv lock
	pnpm install --lockfile-only

.PHONY: packages.update
packages.update:
	uv lock $(if $(PACKAGE),--upgrade-package $(PACKAGE),--upgrade)
	pnpm update$(if $(PACKAGE), $(PACKAGE))
	uv sync --locked
	pnpm install --frozen-lockfile

# pnpm lists only the outdated packages, and exits 1 when it lists any.
.PHONY: packages.outdated.show
packages.outdated.show:
	uv tree --locked --outdated --depth 1
	pnpm outdated

# Every *.check target is a prerequisite of check and check.fast, and CI runs
# each of check's prerequisites; tests/tooling checks both.

.PHONY: lint.check
lint.check:
	$(banner)
	uv run --locked ruff check
	uv run --locked ruff format --check

# Fix and format before reporting, so the formatter runs even when the linter
# finds problems it can't fix. The last command fails on whatever is left.
.PHONY: lint.fix
lint.fix:
	$(banner)
	uv run --locked ruff check --fix-only
	uv run --locked ruff format
	uv run --locked ruff check

.PHONY: types.check
types.check:
	$(banner)
	uv run --locked pyrefly check

.PHONY: boundaries.check
boundaries.check:
	$(banner)
	uv run --locked lint-imports

# The engines, gate and telemetry setting are in .aislop/config.yml.
.PHONY: slop.check
slop.check:
	$(banner)
	pnpm exec aislop ci --human

# openapi.json is the clients' contract, so a stale copy fails the check.
.PHONY: openapi.check
openapi.check:
	$(banner)
	uv run --locked python scripts/export_openapi.py --check

.PHONY: openapi.fix
openapi.fix:
	uv run --locked python scripts/export_openapi.py

.PHONY: test
test:
	$(banner)
	uv run --locked pytest $(ARGS)

.PHONY: test.fast
test.fast:
	$(banner)
	uv run --locked pytest -m "not container" $(ARGS)

.PHONY: check
check: lint.check types.check boundaries.check slop.check openapi.check test

.PHONY: check.fast
check.fast: lint.check types.check boundaries.check slop.check openapi.check test.fast

.PHONY: image.build
image.build:
	docker build$(if $(PLATFORM), --platform $(PLATFORM)) -t $(IMAGE) .

.PHONY: datastores.up
datastores.up:
	docker compose up --detach --wait

.PHONY: api.run
api.run:
	uv run --locked --env-file .env uvicorn tally_aloft.main:app --reload --no-proxy-headers

# One worker process without reload, which Taskiq offers only with an extra.
# The flags are the image contract's worker command.
.PHONY: worker.run
worker.run:
	uv run --locked --env-file .env taskiq worker tally_aloft.worker:broker --wait-tasks-timeout 25 --max-fails 1 --max-async-tasks 10 --workers 1

# The image contract's scheduler command. Only one scheduler may run against
# one set of data stores, or the sweeps double.
.PHONY: scheduler.run
scheduler.run:
	uv run --locked --env-file .env taskiq scheduler tally_aloft.worker:scheduler

.PHONY: containers.up
containers.up:
	docker compose --profile app up --build

.PHONY: containers.down
containers.down:
	docker compose --profile app down $(ARGS)

# compose.e2e.yaml runs the test worker, built with tests/, in place of the
# real one; containers.down stops it too.
.PHONY: containers.e2e.up
containers.e2e.up:
	docker compose -f compose.yaml -f compose.e2e.yaml --profile app up --build

.PHONY: containers.e2e.job.run
containers.e2e.job.run:
	docker compose -f compose.yaml -f compose.e2e.yaml exec worker python -m tests.support.e2e_job

# Migrations connect as the owner role, from TALLY_MIGRATION_DATABASE_URL in
# .env. Alembic accepts an empty message, so migrations.create checks NAME.
.PHONY: migrations.apply
migrations.apply:
	uv run --locked --env-file .env alembic upgrade head

.PHONY: migrations.create
migrations.create:
	$(if $(NAME),,$(error NAME is required, as in make migrations.create NAME="add flights"))
	uv run --locked --env-file .env alembic revision --autogenerate -m "$(NAME)"
