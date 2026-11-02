import pytest

from tally_aloft.adapters.inbound.http.version_policy import (
    ClientPlatform,
    ClientVersion,
    ClientVersionPolicy,
)


def test_parses_major_minor_patch() -> None:
    assert ClientVersion.parse("1.4.0") == ClientVersion(1, 4, 0)
    assert ClientVersion.parse("123456789.0.10") == ClientVersion(123456789, 0, 10)


@pytest.mark.parametrize(
    "text",
    [
        "banana",
        "1.4",
        "1.4.0.1",
        "1.4.0 (45)",
        "v1.4.0",
        " 1.4.0",
        "",
        "1234567890.0.0",
        "1.\N{ARABIC-INDIC DIGIT FOUR}.0",
        "1.-4.0",
    ],
)
def test_rejects_anything_else(text: str) -> None:
    assert ClientVersion.parse(text) is None


def test_versions_order_by_their_numbers() -> None:
    assert ClientVersion(1, 3, 9) < ClientVersion(1, 4, 0) < ClientVersion(2, 0, 0)
    assert ClientVersion(1, 10, 0) > ClientVersion(1, 9, 9)


def test_a_version_prints_as_major_minor_patch() -> None:
    assert str(ClientVersion(1, 4, 0)) == "1.4.0"


def test_the_policy_finds_a_platform_minimum_by_its_header_value() -> None:
    policy = ClientVersionPolicy({ClientPlatform.IOS: ClientVersion(1, 4, 0)})

    assert policy.minimum_for("ios") == ClientVersion(1, 4, 0)
    assert policy.minimum_for("android") is None
    assert policy.minimum_for("iOS") is None
    assert policy.minimum_for("desktop") is None


def test_the_policy_keeps_its_own_read_only_minimums() -> None:
    minimums = {ClientPlatform.IOS: ClientVersion(1, 4, 0)}
    policy = ClientVersionPolicy(minimums)

    minimums[ClientPlatform.WEB] = ClientVersion(9, 9, 9)

    assert policy.minimum_for("web") is None
    with pytest.raises(TypeError):
        policy.minimums[ClientPlatform.WEB] = ClientVersion(9, 9, 9)  # pyrefly: ignore[unsupported-operation]  # Proves the map is read-only.
