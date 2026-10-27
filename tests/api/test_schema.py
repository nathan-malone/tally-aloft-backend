import pytest
from fastapi import APIRouter, FastAPI

from tally_aloft.adapters.inbound.http.operation_ids import SchemaError, check_schema
from tally_aloft.bootstrap.app import create_app
from tally_aloft.bootstrap.settings import Settings
from tests.support import contract_routes


def _app(*routers: APIRouter) -> FastAPI:
    app = create_app(Settings.for_tests())
    for router in routers:
        app.include_router(router)
    return app


def _logbook_router(prefix: str = "") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["logbook"])

    @router.get("/flights")
    async def list_flights() -> list[str]:
        return []

    return router


def test_operation_id_is_the_tag_and_the_function_name() -> None:
    schema = _app(_logbook_router()).openapi()

    assert schema["paths"]["/flights"]["get"]["operationId"] == "logbook_list_flights"


def test_app_with_the_contract_routes_passes_the_check() -> None:
    check_schema(_app(contract_routes.router))


def test_untagged_route_fails_the_check_by_name() -> None:
    router = APIRouter()

    @router.get("/untagged")
    async def untagged() -> str:
        return ""

    with pytest.raises(SchemaError, match="GET /untagged must have exactly one tag"):
        check_schema(_app(router))


def test_route_with_two_tags_fails_the_check_by_name() -> None:
    router = APIRouter(tags=["logbook", "accounts"])

    @router.get("/both")
    async def both() -> str:
        return ""

    with pytest.raises(SchemaError, match="GET /both must have exactly one tag"):
        check_schema(_app(router))


def test_duplicate_operation_ids_fail_the_check_naming_both_routes() -> None:
    app = _app(_logbook_router("/one"), _logbook_router("/two"))

    with pytest.raises(SchemaError) as error:
        check_schema(app)

    message = str(error.value)
    assert "GET /one/flights shares its operation ID logbook_list_flights" in message
    assert "GET /two/flights shares its operation ID logbook_list_flights" in message
