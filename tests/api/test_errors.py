import importlib
import json
import logging
import pkgutil
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

import tally_aloft
from tally_aloft.adapters.inbound.http.errors import (
    ERROR_STATUSES,
    HTTP_ERROR_CODES,
    unmapped_errors,
)
from tally_aloft.application.shared.errors import ApplicationError
from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.container import Overrides
from tally_aloft.bootstrap.settings import Settings
from tally_aloft.domain.shared.errors import CodedError, DomainError
from tests.support.contract_routes import (
    LEAK_MARKER,
    MAPPED_MESSAGE,
    MAPPED_STATUS,
    ORIGIN,
    ContractChildError,
    ContractMappedError,
    contract_app,
)
from tests.support.http import request
from tests.support.paths import ROOT

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

# Entry points read settings at import, and define no errors.
ENTRY_POINTS = {"tally_aloft.main", "tally_aloft.worker"}
BODY_KEYS = {"code", "message", "details"}


async def test_validation_error_uses_the_shared_body_without_input() -> None:
    response = await request(
        contract_app(), "GET", "/test/query", params={"count": "abc"}
    )

    body = response.json()
    assert response.status_code == 422
    assert set(body) == BODY_KEYS
    assert body["code"] == "validation_failed"
    [error] = body["details"]["errors"]
    assert set(error) == {"loc", "message", "type"}
    assert error["loc"] == ["query", "count"]
    assert "abc" not in response.text


async def test_validation_keeps_list_indices_as_integers() -> None:
    response = await request(
        contract_app(), "POST", "/test/body", json={"count": 1, "items": [1, "x"]}
    )

    [error] = response.json()["details"]["errors"]
    assert error["loc"] == ["body", "items", 1]


async def test_unknown_path_is_not_found() -> None:
    response = await request(contract_app(), "GET", "/test/nowhere")

    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "Not Found",
        "details": {},
    }


async def test_wrong_method_is_method_not_allowed() -> None:
    response = await request(contract_app(), "POST", "/test/query")

    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"
    assert response.headers["allow"] == "GET"


def test_framework_codes_are_pinned() -> None:
    assert dict(HTTP_ERROR_CODES) == {404: "not_found", 405: "method_not_allowed"}


def _raising(status: int, detail: str | None = None) -> FastAPI:
    router = APIRouter()

    @router.get("/raise")
    async def raise_http() -> None:
        raise HTTPException(status, detail)

    app = create_app(Settings.for_tests())
    app.include_router(router)
    return app


async def test_other_client_errors_use_the_fallback_code() -> None:
    response = await request(_raising(418), "GET", "/raise")

    assert response.status_code == 418
    assert response.json()["code"] == "http_error"


async def test_server_http_errors_become_internal_errors_without_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = await request(_raising(503, LEAK_MARKER), "GET", "/raise")

    assert response.status_code == 500
    assert response.json() == {
        "code": "internal_error",
        "message": "An internal error occurred.",
        "details": {},
    }
    [record] = caplog.records
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None


async def test_statuses_without_a_body_stay_empty() -> None:
    response = await request(_raising(304), "GET", "/raise")

    assert response.status_code == 304
    assert response.content == b""


async def test_mapped_error_uses_its_status_code_and_message() -> None:
    app = contract_app(Overrides(error_statuses={ContractMappedError: MAPPED_STATUS}))

    response = await request(app, "GET", "/test/mapped")

    assert response.status_code == MAPPED_STATUS
    assert response.json() == {
        "code": "contract_mapped",
        "message": MAPPED_MESSAGE,
        "details": {},
    }


@pytest.mark.parametrize("path", ["/test/unmapped", "/test/crash"])
async def test_unhandled_errors_become_internal_errors(
    path: str, caplog: pytest.LogCaptureFixture
) -> None:
    response = await request(contract_app(), "GET", path, headers={"Origin": ORIGIN})

    assert response.status_code == 500
    assert response.json() == {
        "code": "internal_error",
        "message": "An internal error occurred.",
        "details": {},
    }
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert LEAK_MARKER not in response.text
    assert "Traceback" not in response.text
    [record] = caplog.records
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None


async def test_unhandled_error_log_names_the_exception_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    await request(contract_app(), "GET", "/test/crash")

    assert "RuntimeError" in caplog.text


def test_unmapped_errors_finds_a_class_missing_from_the_table() -> None:
    class LocalError(ApplicationError):
        code = "local"

    assert unmapped_errors([LocalError], {}) == [LocalError]
    assert unmapped_errors([LocalError], {LocalError: HTTPStatus.CONFLICT}) == []


def test_a_subclass_of_a_mapped_error_needs_its_own_entry() -> None:
    table: dict[type[CodedError], HTTPStatus] = {ContractMappedError: MAPPED_STATUS}

    assert unmapped_errors([ContractMappedError, ContractChildError], table) == [
        ContractChildError
    ]


def test_grouping_bases_are_never_unmapped() -> None:
    class GroupError(DomainError, abstract=True):
        pass

    assert unmapped_errors([GroupError], {}) == []


def _source_modules() -> set[str]:
    package = ROOT / "src" / "tally_aloft"
    names: set[str] = set()
    for path in package.rglob("*.py"):
        parts = path.relative_to(package.parent).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join(parts))
    return names - {"tally_aloft"} - ENTRY_POINTS


def _import_every_module() -> set[str]:
    walked = {
        module.name
        for module in pkgutil.walk_packages(tally_aloft.__path__, "tally_aloft.")
        if module.name not in ENTRY_POINTS
    }
    for name in walked:
        importlib.import_module(name)
    return walked


def _subclasses(base: type[CodedError]) -> Iterator[type[CodedError]]:
    for subclass in base.__subclasses__():
        yield subclass
        yield from _subclasses(subclass)


def test_every_production_error_is_mapped() -> None:
    walked = _import_every_module()
    errors = [
        error_class
        for base in (DomainError, ApplicationError)
        for error_class in _subclasses(base)
        if error_class.__module__.startswith("tally_aloft.")
    ]

    assert walked == _source_modules()
    assert unmapped_errors(errors, ERROR_STATUSES) == []


def test_schema_documents_errors_with_the_shared_body() -> None:
    schema = contract_app().openapi()
    paths = schema["paths"]
    error_ref = {"$ref": "#/components/schemas/ErrorBody"}

    def response_schema(path: str, method: str, status: str) -> object:
        response = paths[path][method]["responses"][status]
        return response["content"]["application/json"]["schema"]

    assert response_schema("/test/body", "post", "422") == error_ref
    assert response_schema("/test/mapped", "get", "404") == error_ref
    assert "HTTPValidationError" not in json.dumps(schema)
    error_body = schema["components"]["schemas"]["ErrorBody"]
    assert set(error_body["required"]) == BODY_KEYS


async def test_error_after_the_response_started_is_not_answered_again() -> None:
    router = APIRouter()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"first"
        raise RuntimeError(LEAK_MARKER)

    @router.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(chunks())

    app = create_app(Settings.for_tests())
    app.include_router(router)

    with pytest.raises(RuntimeError, match=LEAK_MARKER):
        await request(app, "GET", "/stream")
