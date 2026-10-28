"""
Operation IDs, which the generated clients use as function names.

A schema route has exactly one tag, its domain or capability, and its operation
ID is `<tag>_<endpoint function name>`, such as `logbook_list_flights`.
"""

import warnings
from collections import Counter
from typing import TYPE_CHECKING, cast

from fastapi.utils import generate_unique_id

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.routing import APIRoute

# FastAPI warns on a duplicate while it builds the schema; the check reports it.
_DUPLICATE_WARNING = "Duplicate Operation ID"


class SchemaError(ValueError):
    """The app's schema breaks the operation-ID rules."""


def operation_id(route: APIRoute) -> str:
    """
    Return `<tag>_<endpoint function name>` for a route with exactly one tag.

    FastAPI also calls this for routes outside the schema and for routes with
    no tag or several, so it never raises: those get FastAPI's default ID, and
    `check_schema` reports any that are in the schema. Tags are strings, which
    includes `StrEnum` members; a plain `Enum` tag gets the default ID too.
    """
    if len(route.tags) != 1 or not isinstance(route.tags[0], str):
        return generate_unique_id(route)
    # A `StrEnum` tag is a `str` whose format is its value. The function's own
    # name is used, not `route.name`, which `name=` can override for `url_for`.
    return f"{route.tags[0]}_{route.endpoint.__name__}"


def check_schema(app: FastAPI) -> None:
    """
    Raise `SchemaError` naming each schema route that breaks the rules.

    A route breaks them when it doesn't have exactly one tag, when its operation
    ID doesn't start with that tag (it got FastAPI's default ID, as a plain
    `Enum` tag does), or when its operation ID is also another route's.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=_DUPLICATE_WARNING)
        schema = app.openapi()
    operations = [
        (f"{method.upper()} {path}", operation)
        for path, methods in cast(
            "dict[str, dict[str, dict[str, object]]]", schema.get("paths", {})
        ).items()
        for method, operation in methods.items()
    ]
    counts = Counter(operation.get("operationId") for _, operation in operations)
    problems: list[str] = []
    for route, operation in operations:
        tags = cast("list[str]", operation.get("tags", []))
        identifier = str(operation.get("operationId"))
        if len(tags) != 1:
            problems.append(f"{route} must have exactly one tag")
        elif not identifier.startswith(f"{tags[0]}_"):
            problems.append(f"{route} has operation ID {identifier}, not {tags[0]}_*")
        if counts[operation.get("operationId")] > 1:
            problems.append(f"{route} shares its operation ID {identifier}")
    if len(problems) > 0:
        raise SchemaError("; ".join(problems))
