import ipaddress
import re
from collections.abc import Sequence
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Annotated, override
from urllib.parse import urlsplit

from pydantic import ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
)

_ENV_PREFIX = "TALLY_"
# pydantic-settings 2.16 words a parse error as `error parsing value for field
# "<name>" from source "<source>"`: it names the field, never the value. A test
# raises a real one, so a change in that wording fails the suite.
_PARSE_ERROR_FIELD = re.compile(r'field "(?P<field>\w+)"')


_TRUSTS_EVERYONE = "would trust every address; list the proxies' networks"
_ALLOWS_EVERY_ORIGIN = "would allow every origin; list the web app's origins"
_NOT_AN_ORIGIN = (
    "must be a scheme://host[:port] as a browser sends it: lowercase, with no "
    "path and no default port"
)
_DEFAULT_PORTS = {"http": 80, "https": 443}
# A host label a browser parses as a number, which makes the host IPv4.
_NUMBER_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*")


class Environment(StrEnum):
    """The deployment a process runs in."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """
    The API's, worker's and scheduler's configuration.

    It reads `TALLY_` environment variables and nothing else: a local `.env`
    file is loaded by the entry command, never here. Durations are integer
    seconds, lists are comma-separated, and maps are JSON.
    """

    model_config = SettingsConfigDict(
        env_prefix=_ENV_PREFIX,
        extra="ignore",
        frozen=True,
    )

    environment: Environment
    # The proxies, such as the load balancer, whose X-Forwarded-* headers are
    # trusted. Uvicorn silently ignores an entry it can't parse, so every entry
    # is validated here, and `*` and catch-all networks are rejected.
    forwarded_allow_ips: Annotated[tuple[IPv4Network | IPv6Network, ...], NoDecode] = (
        IPv4Network("127.0.0.1/32"),
    )

    # The browser origins, such as https://tallyaloft.app, that CORS allows.
    # Starlette compares origins as exact strings, and browsers send them in
    # lowercase without a default port, so any other form is rejected rather
    # than silently matching nothing.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = ()

    @field_validator("forwarded_allow_ips", mode="before")
    @classmethod
    def _parse_networks(cls, value: object) -> object:
        """Parse comma-separated addresses or networks, dropping empty parts."""
        parts = _list_parts(value)
        if parts is None:
            return value
        networks: list[IPv4Network | IPv6Network] = []
        for part in parts:
            if isinstance(part, IPv4Network | IPv6Network):
                networks.append(part)
                continue
            text = str(part).strip()
            if text == "*":
                raise ValueError(_TRUSTS_EVERYONE)
            if text != "":
                network = ipaddress.ip_network(text)
                if network.prefixlen == 0:
                    raise ValueError(_TRUSTS_EVERYONE)
                networks.append(network)
        return tuple(networks)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: object) -> object:
        """Parse comma-separated origins, dropping empty parts."""
        parts = _list_parts(value)
        if parts is None:
            return value
        origins = [str(part).strip() for part in parts]
        return tuple(_check_origin(origin) for origin in origins if origin != "")

    @classmethod
    def for_tests(cls, **values: object) -> Settings:
        """
        Return settings for the test suite, built only from `values`.

        Required values are filled in, and the process environment is never
        read, so a developer's shell can't change a test.
        """
        return _ExplicitSettings.model_validate(
            {"environment": Environment.TEST, **values}
        )

    @classmethod
    def for_schema_export(cls) -> Settings:
        """
        Return settings for exporting the OpenAPI schema.

        It needs no variables and no secrets, and never reads the environment.
        """
        return _ExplicitSettings.model_validate(
            {"environment": Environment.DEVELOPMENT}
        )


class _ExplicitSettings(Settings):
    """
    Settings built only from the values passed in, never the environment.

    An unknown value is an error here, so a misspelled field in a test fails
    instead of silently keeping the default.
    """

    model_config = SettingsConfigDict(extra="forbid")

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def _list_parts(value: object) -> Sequence[object] | None:
    """Return a list setting's parts: a string split on commas, or a sequence."""
    if isinstance(value, str):
        return value.split(",")
    if isinstance(value, Sequence):
        return value
    return None


def _check_origin(origin: str) -> str:
    """Return `origin` if it is exactly a browser's form of one, else raise."""
    if origin == "*":
        raise ValueError(_ALLOWS_EVERY_ORIGIN)
    parts = urlsplit(origin)
    try:
        port = parts.port
    except ValueError:
        raise ValueError(_NOT_AN_ORIGIN) from None
    host = "" if parts.hostname is None else _browser_host(parts.hostname)
    # urlsplit lowercases the scheme and host and leaves out any path, query,
    # fragment or user info, so an origin that differs from this rebuilt form
    # isn't one a browser sends. Browsers also send a non-ASCII host in its
    # punycode form.
    canonical = f"{parts.scheme}://{host}" + ("" if port is None else f":{port}")
    if (
        parts.scheme not in _DEFAULT_PORTS
        or host == ""
        or not host.isascii()
        or port in {0, _DEFAULT_PORTS[parts.scheme]}
        or canonical != origin
    ):
        raise ValueError(_NOT_AN_ORIGIN)
    return origin


def _browser_host(hostname: str) -> str:
    """
    Return `hostname` as a browser writes it in an origin, or "" if it can't be.

    A browser writes an IPv6 address in brackets, compressed, with no zone and
    no dotted IPv4 part, and reads a host whose last label is a number as an
    IPv4 address, which it writes as four decimal numbers: `127.1` is sent as
    `127.0.0.1`. Other hosts are kept as they are.
    """
    if ":" in hostname:
        return _browser_ipv6_host(hostname)
    last_label = hostname.removesuffix(".").rpartition(".")[2]
    if _NUMBER_LABEL.fullmatch(last_label) is not None:
        try:
            return str(IPv4Address(hostname))
        except ValueError:
            return ""
    return hostname


def _browser_ipv6_host(hostname: str) -> str:
    """Return an IPv6 `hostname` as `_browser_host` describes."""
    try:
        address = IPv6Address(hostname)
    except ValueError:
        return ""
    if address.scope_id is not None:
        return ""
    mapped = address.ipv4_mapped
    if mapped is None:
        # Python compresses as browsers do: the first longest zero run.
        return f"[{address.compressed}]"
    # Python writes the IPv4 part dotted; browsers write it in hex.
    return f"[::ffff:{int(mapped) >> 16:x}:{int(mapped) & 0xFFFF:x}]"


def load_settings() -> Settings:
    """
    Read the settings from the environment, or stop the process.

    On a missing or invalid variable, the process exits with one line that
    names each failing variable and its error type. The line is built from
    each error's location and type only, so no value, secret or not, appears.
    """
    try:
        return Settings()
    except ValidationError as error:
        problems = [
            (_location_variable(detail["loc"]), detail["type"])
            for detail in error.errors()
        ]
    except SettingsError as error:
        problems = [(_location_variable((parse_error_field(error),)), "parse_error")]
    raise SystemExit(_describe(problems)) from None


def variable_name(field: str) -> str:
    """
    Return the environment variable that sets the `Settings` field `field`.

    `Settings` fields have no aliases, which a test checks, so the variable is
    always the prefix plus the field's name.
    """
    return f"{_ENV_PREFIX}{field.upper()}"


def parse_error_field(error: SettingsError) -> str:
    """Return the field a pydantic-settings parse error names, or `""`."""
    match = _PARSE_ERROR_FIELD.search(str(error))
    return "" if match is None else match.group("field")


def _location_variable(location: tuple[int | str, ...]) -> str:
    """Return the environment variable for a validation error's location."""
    field = str(location[0]) if len(location) > 0 else ""
    if field not in Settings.model_fields:
        return f"{_ENV_PREFIX}<UNKNOWN>"
    return variable_name(field)


def _describe(problems: list[tuple[str, str]]) -> str:
    """Join each variable, once and in order, with its error types."""
    types_by_variable: dict[str, list[str]] = {}
    for variable, error_type in problems:
        types = types_by_variable.setdefault(variable, [])
        if error_type not in types:
            types.append(error_type)
    described = ", ".join(
        f"{variable} ({', '.join(types)})"
        for variable, types in types_by_variable.items()
    )
    return f"Invalid settings: {described}"
