import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from ipaddress import IPv4Network, IPv6Network
from typing import Annotated, override

from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
)

from tally_aloft.adapters.inbound.http.version_policy import (
    ClientPlatform,
    ClientVersion,
)
from tally_aloft.bootstrap.connection_urls import (
    check_database_url,
    check_valkey_url,
)
from tally_aloft.bootstrap.origins import check_origin

_ENV_PREFIX = "TALLY_"
# pydantic-settings 2.16 words a parse error as `error parsing value for field
# "<name>" from source "<source>"`: it names the field, never the value. A test
# raises a real one, so a change in that wording fails the suite.
_PARSE_ERROR_FIELD = re.compile(r'field "(?P<field>\w+)"')


_TRUSTS_EVERYONE = "would trust every address; list the proxies' networks"
_NOT_A_VERSION = "must be a MAJOR.MINOR.PATCH string, such as 1.4.0"

# For profiles and tests that never connect. Port 9 on localhost refuses
# connections, so a stray one fails at once instead of reaching a developer's
# data stores.
UNUSED_DATABASE_URL = "postgresql+asyncpg://unused@127.0.0.1:9/unused"
UNUSED_VALKEY_URL = "redis://127.0.0.1:9/0"


class Environment(StrEnum):
    """The deployment a process runs in."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


# The deployments whose data store connections must be encrypted and verify
# the server.
_DEPLOYED = frozenset({Environment.STAGING, Environment.PRODUCTION})


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

    # The lowest app version each platform still supports, as a JSON object
    # such as {"ios": "1.4.0"}. Older apps get `400 client_version_unsupported`;
    # a platform without an entry has no minimum.
    min_client_versions: Annotated[Mapping[ClientPlatform, ClientVersion], NoDecode] = (
        Field(default_factory=dict)
    )

    # The application role's connection. Migrations connect as the owner role,
    # through MigrationSettings, which this model never reads.
    database_url: SecretStr
    # Each process opens at most pool size plus overflow connections.
    database_pool_size: Annotated[int, Field(ge=1)] = 5
    database_max_overflow: Annotated[int, Field(ge=0)] = 5
    database_statement_timeout: Annotated[int, Field(ge=1)] = 30

    # The Valkey the cache uses. The broker shares the instance, under its own
    # keys.
    valkey_url: SecretStr

    # How long a worker's message may go unacknowledged before another worker
    # may reclaim it. It must exceed every task's timeout, which the worker
    # checks at import, or a slow task would run twice.
    broker_idle_timeout: Annotated[int, Field(ge=1)] = 900
    # The stream is trimmed to about this many messages. A trimmed message's
    # job is recovered from its row.
    broker_stream_maxlen: Annotated[int, Field(ge=1)] = 100000

    # How long after its last dispatch the sweeper dispatches a job again: a
    # `queued` job that no worker took, or a `running` one whose lease expired.
    # Retries wait for it too.
    job_stale_after: Annotated[int, Field(ge=1)] = 300
    # The most rows each of the sweeper's steps handles in one run.
    sweep_batch_size: Annotated[int, Field(ge=1)] = 500

    @field_validator("database_url")
    @classmethod
    def _parse_database_url(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        """Accept only a SQLAlchemy URL for asyncpg, verified when deployed."""
        return check_database_url(value, deployed=_deployed(info))

    @field_validator("valkey_url")
    @classmethod
    def _parse_valkey_url(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        """Accept only a redis:// or rediss:// URL, with TLS when deployed."""
        return check_valkey_url(value, deployed=_deployed(info))

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
        return tuple(check_origin(origin) for origin in origins if origin != "")

    @field_validator("min_client_versions", mode="before")
    @classmethod
    def _parse_min_versions(cls, value: object) -> object:
        """
        Parse a JSON object of versions; an empty value means no minimums.

        Only a `ValueError` or a pydantic error may escape, so a bad value stops
        the process with the variable's name rather than a traceback.
        """
        if isinstance(value, str):
            if value.strip() == "":
                return dict[ClientPlatform, ClientVersion]()
            value = json.loads(value)
        versions = _mapping(value)
        if versions is None:
            return value
        return {key: _parse_version(version) for key, version in versions.items()}

    @classmethod
    def for_tests(cls, **values: object) -> Settings:
        """
        Return settings for the test suite, built only from `values`.

        Required values are filled in, and the process environment is never
        read, so a developer's shell can't change a test.
        """
        return _ExplicitSettings.model_validate(
            {
                "environment": Environment.TEST,
                "database_url": UNUSED_DATABASE_URL,
                "valkey_url": UNUSED_VALKEY_URL,
                **values,
            }
        )

    @classmethod
    def for_schema_export(cls) -> Settings:
        """
        Return settings for exporting the OpenAPI schema.

        It needs no variables and no secrets, and never reads the environment.
        """
        return _ExplicitSettings.model_validate(
            {
                "environment": Environment.DEVELOPMENT,
                "database_url": UNUSED_DATABASE_URL,
                "valkey_url": UNUSED_VALKEY_URL,
            }
        )


class MigrationSettings(BaseSettings):
    """
    The migration task's configuration, read by Alembic's `env.py` only.

    It holds the owner role's credentials, so the services never read it, and
    `Settings` has no field for any of its `TALLY_MIGRATION_` variables.
    """

    model_config = SettingsConfigDict(
        env_prefix=_ENV_PREFIX,
        extra="ignore",
        frozen=True,
    )

    # Decides whether the owner's connection must verify the server.
    environment: Environment
    # The owner role's connection, which runs the DDL and makes every grant.
    migration_database_url: SecretStr
    migration_lock_timeout: Annotated[int, Field(ge=1)] = 5

    @field_validator("migration_database_url")
    @classmethod
    def _parse_database_url(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        """Accept only a SQLAlchemy URL for asyncpg, verified when deployed."""
        return check_database_url(value, deployed=_deployed(info))

    @classmethod
    def for_tests(cls, **values: object) -> MigrationSettings:
        """Return migration settings built only from `values`, never the environment."""
        return _ExplicitMigrationSettings.model_validate(
            {"environment": Environment.TEST, **values}
        )


class _FromValuesOnly(BaseSettings):
    """
    A mixin for settings built only from the values passed in.

    Each subclass sets `extra="forbid"` itself, because pydantic merges the
    bases' configs in order and the settings model's own would win.
    """

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


class _ExplicitSettings(_FromValuesOnly, Settings):
    """
    Settings built only from the values passed in, never the environment.

    An unknown value is an error here, so a misspelled field in a test fails
    instead of silently keeping the default.
    """

    model_config = SettingsConfigDict(extra="forbid")


class _ExplicitMigrationSettings(_FromValuesOnly, MigrationSettings):
    """Migration settings built only from the values passed in, as above."""

    model_config = SettingsConfigDict(extra="forbid")


def _deployed(info: ValidationInfo) -> bool:
    """
    Return whether the model's environment is a deployed one.

    An invalid environment is reported on its own, so a URL isn't held to it.
    """
    return info.data.get("environment") in _DEPLOYED


def _list_parts(value: object) -> Sequence[object] | None:
    """Return a list setting's parts: a string split on commas, or a sequence."""
    if isinstance(value, str):
        return value.split(",")
    if isinstance(value, Sequence):
        return value
    return None


def _mapping(value: object) -> Mapping[object, object] | None:
    """
    Return a map setting's value as a mapping, or `None` if it isn't one.

    It narrows `object` for the type checker, as `_list_parts` does for lists.
    """
    if isinstance(value, Mapping):
        return value
    return None


def _parse_version(version: object) -> ClientVersion:
    """Return a minimum version, from its text or as already parsed."""
    if isinstance(version, ClientVersion):
        return version
    parsed = ClientVersion.parse(version) if isinstance(version, str) else None
    if parsed is None:
        raise ValueError(_NOT_A_VERSION)
    return parsed


def load_settings() -> Settings:
    """
    Read the settings from the environment, or stop the process.

    On a missing or invalid variable, the process exits with one line that
    names each failing variable and its error type. The line is built from
    each error's location and type only, so no value, secret or not, appears.
    """
    return _load(Settings)


def load_migration_settings() -> MigrationSettings:
    """Read the migration settings from the environment, as `load_settings` does."""
    return _load(MigrationSettings)


def _load[ModelT: BaseSettings](model: type[ModelT]) -> ModelT:
    """Build `model` from the environment, or exit naming each bad variable."""
    try:
        return model()
    except ValidationError as error:
        problems = [
            (_location_variable(model, detail["loc"]), detail["type"])
            for detail in error.errors()
        ]
    except SettingsError as error:
        location = (parse_error_field(error),)
        problems = [(_location_variable(model, location), "parse_error")]
    raise SystemExit(_describe(problems)) from None


def variable_name(field: str) -> str:
    """
    Return the environment variable that sets the settings field `field`.

    Neither settings model's fields have aliases, which a test checks, so the
    variable is always the prefix plus the field's name.
    """
    return f"{_ENV_PREFIX}{field.upper()}"


def parse_error_field(error: SettingsError) -> str:
    """Return the field a pydantic-settings parse error names, or `""`."""
    match = _PARSE_ERROR_FIELD.search(str(error))
    return "" if match is None else match.group("field")


def _location_variable(
    model: type[BaseSettings], location: tuple[int | str, ...]
) -> str:
    """Return the environment variable for a validation error's location."""
    field = str(location[0]) if len(location) > 0 else ""
    if field not in model.model_fields:
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
