from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tally_aloft.adapters.outbound.postgres.tables import Base


class IdempotencyKeyRecord(Base):
    """
    An idempotency key: one write's identity, and the result it returned.

    The unique constraint is what a repeat's insert conflicts with, and waits
    on while the first call's transaction is open. The `created_at` index
    serves the hourly cleanup.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("pilot_id", "operation", "key"),
        Index("ix_idempotency_keys_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    # No foreign key until the accounts epic decides the keys and the purge.
    pilot_id: Mapped[UUID] = mapped_column()
    operation: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    # SQL NULL until `run_once` stores the result, in the same transaction. A
    # JSON `null` result is stored as JSON.
    result: Mapped[object] = mapped_column(JSONB(none_as_null=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
