from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import CTE, Select

# ORM-enabled updates and deletes would otherwise look for the rows in the
# session.
NO_SYNC: Final = {"synchronize_session": False}


def locked_ids(candidates: Select[UUID], limit: int) -> CTE:
    """
    Return the IDs of up to `limit` of `candidates`, locked, skipping locked rows.

    `MATERIALIZED` makes Postgres run the locking select once, so the
    statement joined to it can't touch more than `limit` rows.
    """
    return (
        candidates.limit(limit)
        .with_for_update(skip_locked=True)
        .cte("locked")
        .prefix_with("MATERIALIZED")
    )
