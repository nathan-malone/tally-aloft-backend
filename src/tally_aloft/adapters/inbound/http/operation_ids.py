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
    includes `StrEnum` members.
    """
    if len(route.tags) != 1 or not isinstance(route.tags[0], str):
        return generate_unique_id(route)
    # A `StrEnum` tag is a `str` whose format is its value.
    return f"{route.tags[0]}_{route.name}"


def check_schema(app: FastAPI) -> None:
    """
    Raise `SchemaError` naming each schema route that breaks the rules.

    A route breaks them when it doesn't have exactly one tag, or when its
    operation ID is also another route's.
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
    problems = [
        f"{route} must have exactly one tag"
        for route, operation in operations
        if len(cast("list[str]", operation.get("tags", []))) != 1
    ] + [
        f"{route} shares its operation ID {operation.get('operationId')}"
        for route, operation in operations
        if counts[operation.get("operationId")] > 1
    ]
    if len(problems) > 0:
        raise SchemaError("; ".join(problems))
