from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tally_aloft.application.shared.codec import JsonValue


class JsonCodec:
    """A codec for values that are already JSON, stored as they are."""

    def encode(self, value: JsonValue) -> JsonValue:
        """Return `value` unchanged."""
        return value

    def decode(self, data: JsonValue) -> JsonValue:
        """Return `data` unchanged."""
        return data


JSON = JsonCodec()
