import os
from ipaddress import IPv4Network, IPv6Network

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError

from tally_aloft.bootstrap.settings import (
    Environment,
    Settings,
    load_settings,
    parse_error_field,
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith("TALLY_")]:
        monkeypatch.delenv(name)


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


def test_settings_fields_have_no_aliases() -> None:
    # variable_name() relies on it: an alias isn't prefixed by pydantic-settings.
    fields = Settings.model_fields.values()

    assert all(f.alias is None and f.validation_alias is None for f in fields)


def test_load_settings_names_a_missing_variable() -> None:
    with pytest.raises(SystemExit) as error:
        load_settings()

    assert _exit_message(error) == "Invalid settings: TALLY_ENVIRONMENT (missing)"


def test_load_settings_never_echoes_an_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TALLY_ENVIRONMENT", "marker-value")
    monkeypatch.setenv("TALLY_FORWARDED_ALLOW_IPS", "marker-value")

    with pytest.raises(SystemExit) as error:
        load_settings()

    assert _exit_message(error) == (
        "Invalid settings: TALLY_ENVIRONMENT (enum), "
        "TALLY_FORWARDED_ALLOW_IPS (value_error)"
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
