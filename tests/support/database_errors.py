"""Real asyncpg errors, built without a database, for tests of their scrubbing."""

from asyncpg.exceptions import PostgresError

# The value a statement failed on, which Postgres echoes in the error's DETAIL.
MARKER = "detail-marker"


def unique_violation() -> Exception:
    """Return asyncpg's error for a duplicate key, its DETAIL holding `MARKER`."""
    error: Exception = PostgresError.new(
        {
            "C": "23505",
            "M": 'duplicate key value violates unique constraint "uq_key"',
            "D": f"Key (key)=({MARKER}) already exists.",
            "n": "uq_key",
        }
    )
    return error
