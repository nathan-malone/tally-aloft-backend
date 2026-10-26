# tally-aloft-backend

The Tally Aloft backend: one async Python 3.14 codebase for the API, the Taskiq workers and the scheduler.

## Design docs

The design lives in [tally-aloft-docs](../tally-aloft-docs/README.md). Start at its index, and read the backend structure and tech-stack documents before writing backend code.

## Checks

Run these from the repository root, in this order. Every one must pass before a commit, and CI runs the same commands.

```
uv run ruff check
uv run ruff format --check
uv run pyrefly check
uv run lint-imports
uv run pytest
```

`pytest` needs Docker running. Tests marked `container` build the image (`tally-aloft-backend:test`), or use the one named by `BACKEND_TEST_IMAGE`, and run it. For a quick loop without Docker, run `uv run pytest -m "not container"`, but run the full suite before a commit.

## Conventions

- Ruff runs every rule. Each ignore in `pyproject.toml` has a reason comment, and the global list stays at 10 or fewer.
- Pyrefly runs the strict preset with every optional error kind on. An inline suppression is `# pyrefly: ignore[<kind>]  # <reason>`, and `src/` holds 10 at most.
- Add each newly adopted framework or vendor package to Import Linter's forbidden list.
