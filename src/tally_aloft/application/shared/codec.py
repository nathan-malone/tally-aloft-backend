from typing import Protocol

# A value that JSON can hold, as stored in a jsonb column.
type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)


class Codec[T](Protocol):
    """
    Turns a use case's typed value into JSON and back.

    Stored values, such as a job's payload and result, keep their types under
    strict type checking, and the storage never sees the application's types.
    """

    def encode(self, value: T) -> JsonValue:
        """Return `value` as JSON-compatible data."""
        ...

    def decode(self, data: JsonValue) -> T:
        """Return the value that `encode` turned into `data`."""
        ...
