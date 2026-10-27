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
# Extra arguments for the test runner.
ARGS =
# The tag and, optionally, the platform for image.build.
IMAGE = tally-aloft-backend:local
PLATFORM =
# One package to upgrade with packages.update; empty upgrades every package.
PACKAGE =

# Names the running target, so `make check` output shows which check failed.
banner = $(info ==> $@)

# `$()` keeps the leading spaces, which $(info) would otherwise drop.
.PHONY: help
help:
	$(info Usage: make <target> [VARIABLE=value ...])
	$(info )
	$(info Packages)
	$(info $()  packages.install        Install the locked dependencies; fails if uv.lock is stale)
	$(info $()  packages.lock           Update uv.lock after editing pyproject.toml)
	$(info $()  packages.update         Upgrade every locked package, or PACKAGE=<name> only, to releases 7+ days old, then install)
	$(info $()  packages.outdated.show  List direct dependencies, marking those with newer releases)
	$(info )
	$(info Checks)
	$(info $()  lint.check              Check linting and formatting, changing nothing)
	$(info $()  lint.fix                Apply the linter's safe fixes and format, then report what is left)
	$(info $()  types.check             Check types)
	$(info $()  boundaries.check        Check the module-boundary rules)
	$(info $()  test                    The full test suite; needs Docker. ARGS="..." goes to the test runner)
	$(info $()  test.fast               The tests that don't need Docker. ARGS="..." goes to the test runner)
	$(info $()  check                   Every check and the full test suite, as CI runs them)
	$(info $()  check.fast              Every check and the tests that don't need Docker)
	$(info )
	$(info Running)
	$(info $()  image.build             Build the image as IMAGE (default $(IMAGE)), for PLATFORM if set)
	$(info $()  api.run                 Run the API on the host with reload, settings from .env)
	$(info $()  containers.up           Build and run every process in containers)
	$(info $()  containers.down         Stop and remove the containers)

# Only packages.lock and packages.update may rewrite uv.lock. Every other uv
# command passes --locked, so a stale lock fails locally as it does in CI.

.PHONY: packages.install
packages.install:
	uv sync --locked

.PHONY: packages.lock
packages.lock:
	uv lock

.PHONY: packages.update
packages.update:
	uv lock $(if $(PACKAGE),--upgrade-package $(PACKAGE),--upgrade)
	uv sync --locked

.PHONY: packages.outdated.show
packages.outdated.show:
	uv tree --locked --outdated --depth 1

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

.PHONY: test
test:
	$(banner)
	uv run --locked pytest $(ARGS)

.PHONY: test.fast
test.fast:
	$(banner)
	uv run --locked pytest -m "not container" $(ARGS)

.PHONY: check
check: lint.check types.check boundaries.check test

.PHONY: check.fast
check.fast: lint.check types.check boundaries.check test.fast

.PHONY: image.build
image.build:
	docker build$(if $(PLATFORM), --platform $(PLATFORM)) -t $(IMAGE) .

.PHONY: api.run
api.run:
	uv run --locked --env-file .env uvicorn tally_aloft.main:app --reload --no-proxy-headers

.PHONY: containers.up
containers.up:
	docker compose --profile app up --build

.PHONY: containers.down
containers.down:
	docker compose --profile app down
