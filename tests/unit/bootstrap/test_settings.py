import os
from ipaddress import IPv4Network, IPv6Network

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError

from tally_aloft.bootstrap.settings import (
    Environment,
    MigrationSettings,
    Settings,
    load_migration_settings,
    load_settings,
    parse_error_field,
    variable_name,
)

DATABASE_URL = "postgresql+asyncpg://app:app-password@db.example:5432/tally"
URL_VARIABLES = ["TALLY_DATABASE_URL", "TALLY_MIGRATION_DATABASE_URL"]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every `TALLY_` variable, then set a valid database URL."""
    for name in [name for name in os.environ if name.startswith("TALLY_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("TALLY_DATABASE_URL", DATABASE_URL)


def _load(variable: str) -> object:
    """Load the settings model that reads `variable`."""
    if variable.startswith("TALLY_MIGRATION_"):
        return MigrationSettings()
    return Settings()


def _exit_message(error: pytest.ExceptionInfo[SystemExit]) -> str:
    return str(error.value.code)


def test_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "production")

    assert Settings().environment is Environment.PRODUCTION


def test_trusts_only_localhost_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")

    assert Settings().forwarded_allow_ips == (IPv4Network("127.0.0.1/32"),)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "10.0.0.0/8, 192.168.0.0/16",
            (IPv4Network("10.0.0.0/8"), IPv4Network("192.168.0.0/16")),
        ),
        ("10.1.2.3", (IPv4Network("10.1.2.3/32"),)),
        ("2001:db8::/32", (IPv6Network("2001:db8::/32"),)),
        ("10.0.0.0/8,", (IPv4Network("10.0.0.0/8"),)),
        ("", ()),
    ],
)
def test_parses_forwarded_allow_ips(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: tuple[IPv4Network | IPv6Network, ...],
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")
    monkeypatch.setenv("TALLY_FORWARDED_ALLOW_IPS", value)

    assert Settings().forwarded_allow_ips == expected


@pytest.mark.parametrize(
    "value", ["*", "10.0.0.0/8, *", "0.0.0.0/0", "::/0", "10.1.2.3/8", "proxy"]
)
def test_rejects_wildcards_and_malformed_networks(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")
    monkeypatch.setenv("TALLY_FORWARDED_ALLOW_IPS", value)

    with pytest.raises(ValidationError):
        Settings()


def test_allows_no_origin_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")

    assert Settings().cors_origins == ()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://tallyaloft.app", ("https://tallyaloft.app",)),
        (
            "https://tallyaloft.app, http://localhost:5173",
            ("https://tallyaloft.app", "http://localhost:5173"),
        ),
        ("http://[::1]:5173", ("http://[::1]:5173",)),
        ("http://[2001:db8::1:0:0:1]", ("http://[2001:db8::1:0:0:1]",)),
        ("http://[::ffff:7f00:1]", ("http://[::ffff:7f00:1]",)),
        ("http://127.0.0.1:5173", ("http://127.0.0.1:5173",)),
        ("http://app.example.:5173", ("http://app.example.:5173",)),
        ("https://tallyaloft.app,", ("https://tallyaloft.app",)),
        ("", ()),
    ],
)
def test_parses_cors_origins(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: tuple[str, ...]
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")
    monkeypatch.setenv("TALLY_CORS_ORIGINS", value)

    assert Settings().cors_origins == expected


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "https://tallyaloft.app, *",
        "https://tallyaloft.app/",
        "https://tallyaloft.app/app",
        "https://tallyaloft.app?x=1",
        "https://user@tallyaloft.app",
        "https://TallyAloft.app",
        "HTTPS://tallyaloft.app",
        "https://tallyaloft.app:443",
        "http://localhost:80",
        "https://tallyaloft.app:99999",
        "https://tallyaloft.app:0",
        "https://bücher.example",
        "http://[0:0:0:0:0:0:0:1]",
        "http://[::0001]",
        "http://[2001:DB8::1]",
        "http://[2001:db8:0:0:1:0:0:1]",
        "http://[::ffff:127.0.0.1]",
        "http://[fe80::1%25eth0]",
        "http://[v1.x]",
        "http://127.1",
        "http://0x7f.0.0.1",
        "http://127.000.0.1",
        "http://127.0.0.1.",
        "http://tally%61loft.app",
        "http://tally aloft.app",
        "http://tally<aloft.app",
        "http://tally^aloft.app",
        "http://tally|aloft.app",
        "http://tally\\aloft.app",
        "http://tally\x01aloft.app",
        "http://tally\x7faloft.app",
        "ftp://tallyaloft.app",
        "tallyaloft.app",
        "null",
    ],
)
def test_rejects_wildcards_and_origins_that_match_nothing(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")
    monkeypatch.setenv("TALLY_CORS_ORIGINS", value)

    with pytest.raises(ValidationError):
        Settings()


def test_ignores_unknown_tally_variables_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")
    monkeypatch.setenv("TALLY_SOMETHING", "marker-value")

    Settings()

    captured = capsys.readouterr()
    assert "marker-value" not in caplog.text + captured.out + captured.err
    assert all("marker-value" not in str(warning.message) for warning in recwarn)


def test_schema_export_needs_no_variables() -> None:
    assert Settings.for_schema_export().environment is Environment.DEVELOPMENT


def test_test_settings_ignore_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "production")
    monkeypatch.setenv("TALLY_FORWARDED_ALLOW_IPS", "10.0.0.0/8")

    settings = Settings.for_tests()

    assert settings.environment is Environment.TEST
    assert settings.forwarded_allow_ips == (IPv4Network("127.0.0.1/32"),)


def test_test_settings_take_values() -> None:
    settings = Settings.for_tests(forwarded_allow_ips="10.0.0.0/8")

    assert settings.forwarded_allow_ips == (IPv4Network("10.0.0.0/8"),)


def test_test_settings_still_validate() -> None:
    with pytest.raises(ValidationError):
        Settings.for_tests(forwarded_allow_ips="*")


def test_test_settings_reject_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Settings.for_tests(forwarded_allow_ip="10.0.0.0/8")


@pytest.mark.parametrize("model", [Settings, MigrationSettings])
def test_settings_fields_have_no_aliases(
    model: type[Settings | MigrationSettings],
) -> None:
    # variable_name() relies on it: an alias isn't prefixed by pydantic-settings.
    fields = model.model_fields.values()

    assert all(f.alias is None and f.validation_alias is None for f in fields)


def test_load_settings_names_a_missing_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TALLY_DATABASE_URL")

    with pytest.raises(SystemExit) as error:
        load_settings()

    assert _exit_message(error) == (
        "Invalid settings: TALLY_ENVIRONMENT (missing), TALLY_DATABASE_URL (missing)"
    )


def test_load_settings_never_echoes_an_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "marker-value")
    monkeypatch.setenv("TALLY_FORWARDED_ALLOW_IPS", "marker-value")
    monkeypatch.setenv("TALLY_DATABASE_URL", "marker-value")

    with pytest.raises(SystemExit) as error:
        load_settings()

    assert _exit_message(error) == (
        "Invalid settings: TALLY_ENVIRONMENT (enum), "
        "TALLY_FORWARDED_ALLOW_IPS (value_error), "
        "TALLY_DATABASE_URL (value_error)"
    )


def test_database_settings_have_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")

    settings = Settings()

    assert settings.database_url.get_secret_value() == DATABASE_URL
    assert settings.database_pool_size == 5
    assert settings.database_max_overflow == 5
    assert settings.database_statement_timeout == 30


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("TALLY_DATABASE_POOL_SIZE", "0"),
        ("TALLY_DATABASE_MAX_OVERFLOW", "-1"),
        ("TALLY_DATABASE_STATEMENT_TIMEOUT", "0"),
        ("TALLY_MIGRATION_LOCK_TIMEOUT", "0"),
    ],
)
def test_rejects_out_of_range_database_numbers(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")
    monkeypatch.setenv("TALLY_MIGRATION_DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        _load(variable)


@pytest.mark.parametrize("variable", URL_VARIABLES)
@pytest.mark.parametrize(
    "value",
    [
        "postgresql+asyncpg://app:app-password@db.example:5432/tally",
        "postgresql+asyncpg://app:p%40ss%3Aw%2Frd@db.example/tally",
        "postgresql+asyncpg://app@127.0.0.1/tally?ssl=require",
    ],
)
def test_accepts_asyncpg_database_urls(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")
    monkeypatch.setenv("TALLY_MIGRATION_DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv(variable, value)

    _load(variable)


@pytest.mark.parametrize("variable", URL_VARIABLES)
@pytest.mark.parametrize(
    "value",
    [
        "not-a-url",
        "postgresql://app@db.example/tally",
        "postgresql+psycopg://app@db.example/tally",
        "postgresql+asyncpg:///tally",
        "postgresql+asyncpg://app@db.example",
        "postgresql+asyncpg://app@db.example:port/tally",
    ],
)
def test_rejects_other_database_urls_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")
    monkeypatch.setenv("TALLY_MIGRATION_DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv(variable, value)
    load = (
        load_migration_settings
        if variable.startswith("TALLY_MIGRATION_")
        else load_settings
    )

    with pytest.raises(SystemExit) as error:
        load()

    assert _exit_message(error) == f"Invalid settings: {variable} (value_error)"


@pytest.mark.parametrize("model", [Settings, MigrationSettings])
def test_repr_hides_the_database_url(
    monkeypatch: pytest.MonkeyPatch, model: type[Settings | MigrationSettings]
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "development")
    monkeypatch.setenv("TALLY_MIGRATION_DATABASE_URL", DATABASE_URL)

    assert "app-password" not in repr(model())


def test_migration_settings_read_only_their_own_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_url = "postgresql+asyncpg://owner:owner-password@db.example/tally"
    monkeypatch.setenv("TALLY_MIGRATION_DATABASE_URL", owner_url)
    monkeypatch.setenv("TALLY_MIGRATION_LOCK_TIMEOUT", "2")

    settings = MigrationSettings()

    assert settings.migration_database_url.get_secret_value() == owner_url
    assert settings.migration_lock_timeout == 2
    assert set(MigrationSettings.model_fields) == {
        "migration_database_url",
        "migration_lock_timeout",
    }


def test_load_migration_settings_names_a_missing_url() -> None:
    with pytest.raises(SystemExit) as error:
        load_migration_settings()

    assert _exit_message(error) == (
        "Invalid settings: TALLY_MIGRATION_DATABASE_URL (missing)"
    )


def test_services_never_read_a_migration_variable() -> None:
    variables = [variable_name(field) for field in Settings.model_fields]

    assert [v for v in variables if v.startswith("TALLY_MIGRATION_")] == []


def test_migration_test_settings_ignore_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TALLY_MIGRATION_LOCK_TIMEOUT", "9")

    settings = MigrationSettings.for_tests(migration_database_url=DATABASE_URL)

    assert settings.migration_lock_timeout == 5


def test_migration_test_settings_reject_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        MigrationSettings.for_tests(
            migration_database_url=DATABASE_URL, migration_lock_timout=1
        )


class _JsonSettings(BaseSettings):
    """A settings model whose `environment` is a JSON map, so it can fail to parse."""

    model_config = SettingsConfigDict(env_prefix="TALLY_")

    environment: dict[str, str] = {}


def _real_parse_error(monkeypatch: pytest.MonkeyPatch) -> SettingsError:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "{not json")
    with pytest.raises(SettingsError) as error:
        _JsonSettings()
    return error.value


def test_parse_error_field_reads_a_real_pydantic_settings_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert parse_error_field(_real_parse_error(monkeypatch)) == "environment"


def test_load_settings_names_a_variable_that_fails_to_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = _real_parse_error(monkeypatch)

    def fail_to_parse(_self: Settings) -> None:
        raise error

    monkeypatch.setattr(Settings, "__init__", fail_to_parse)

    with pytest.raises(SystemExit) as raised:
        load_settings()

    assert _exit_message(raised) == "Invalid settings: TALLY_ENVIRONMENT (parse_error)"
