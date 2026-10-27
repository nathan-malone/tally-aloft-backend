import dataclasses
from uuid import uuid4, uuid7

import pytest

from tally_aloft.domain.shared.ids import PilotId


def test_new_pilot_id_is_a_version_7_uuid() -> None:
    assert PilotId.new().value.version == 7


def test_pilot_id_accepts_a_version_7_uuid() -> None:
    value = uuid7()

    assert PilotId(value).value == value


def test_pilot_id_is_frozen() -> None:
    pilot_id = PilotId.new()

    with pytest.raises(dataclasses.FrozenInstanceError):
        pilot_id.value = uuid7()  # pyrefly: ignore[read-only]  # Checks the field is frozen.


def test_pilot_id_rejects_a_version_4_uuid() -> None:
    with pytest.raises(ValueError, match="version-7"):
        PilotId(uuid4())
