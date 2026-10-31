from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tally_aloft.adapters.outbound.postgres.tables import Base


class JobRecord(Base):
    """
    A job: the outbox row a use case records, and the record of its runs.

    The two partial indexes serve the sweeper, which looks for stale `queued`
    jobs and expired `running` ones, so finished jobs never grow them.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="status"
        ),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="attempts"),
        Index(
            "ix_jobs_last_dispatched_at",
            "last_dispatched_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_jobs_lease_until",
            "lease_until",
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    # No foreign key until the accounts epic decides the keys and the purge.
    pilot_id: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # JSON `null` is a payload like any other, so None is stored as JSON.
    payload: Mapped[object] = mapped_column(JSONB(none_as_null=False), nullable=False)
    result: Mapped[object] = mapped_column(JSONB(none_as_null=False), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
