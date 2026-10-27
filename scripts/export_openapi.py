"""
Write the API's OpenAPI schema to `openapi.json`, or check it is current.

`make openapi.fix` writes it and `make openapi.check` checks it. The app is
built from settings that need no variables, and nothing connects to anything.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from tally_aloft.adapters.inbound.http.operation_ids import SchemaError, check_schema
from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "openapi.json"


def schema_text() -> str:
    """Return the app's schema as `openapi.json` holds it."""
    app = create_app(Settings.for_schema_export())
    check_schema(app)
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Write or check the schema file, returning the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the file differs from the schema, changing nothing",
    )
    _ = parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    output: Path = arguments.output
    try:
        text = schema_text()
    except SchemaError as error:
        print(f"The API schema breaks the rules: {error}", file=sys.stderr)  # noqa: T201  # A CLI reports on stderr.
        return 1
    if not arguments.check:
        _ = output.write_text(text, encoding="utf-8", newline="\n")
        return 0
    if not output.is_file() or output.read_text(encoding="utf-8") != text:
        print(f"{output} is out of date; run `make openapi.fix`.", file=sys.stderr)  # noqa: T201  # A CLI reports on stderr.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
