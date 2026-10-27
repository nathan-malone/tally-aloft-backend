# tally-aloft-backend

The Tally Aloft backend: one async Python 3.14 codebase for the API, the Taskiq workers and the scheduler.

## Design docs

The design lives in [tally-aloft-docs](../tally-aloft-docs/README.md). Start at its index, and read the backend structure and tech-stack documents before writing backend code.

## Checks

Run `make check` from the repository root. It must pass before a commit, and CI runs the same targets. It runs `make lint.check`, `make types.check`, `make boundaries.check`, `make slop.check` and `make test`, in that order; `make lint.fix` applies the linter's safe fixes and formats the code. `make` alone lists every target.

`make test` needs Docker running. Tests marked `container` build the image (`tally-aloft-backend:test`), or use the one named by `BACKEND_TEST_IMAGE`, and run it. For a quick loop without Docker, run `make check.fast` or `make test.fast`, but run `make check` before a commit. `make test ARGS="..."` passes extra arguments to the test runner.

Every developer and CI command lives in the `Makefile`. Add or change a command there, following the [make commands](../tally-aloft-docs/technical/make-commands.md) conventions, and call the target from the README and the CI workflow rather than repeating the command.

## Conventions

- Ruff runs every rule. Each ignore in `pyproject.toml` has a reason comment, and the global list stays at 10 or fewer.
- Pyrefly runs the strict preset with every optional error kind on. An inline suppression is `# pyrefly: ignore[<kind>]  # <reason>`, and `src/` holds 10 at most.
- Add each newly adopted framework or vendor package to Import Linter's forbidden list.
- `make slop.check` fails on any aislop finding. Fix the code rather than suppress the finding. When a finding is wrong, suppress that one rule on that line, with the reason after `--`: `# aislop-ignore-next-line <rule-id> -- <reason>`.
