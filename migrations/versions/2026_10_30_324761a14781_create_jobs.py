"""
Create jobs, the job outbox, which the application role may update.

Revision ID: 324761a14781
Revises: 46ce00762d02
Create Date: 2026-10-30 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "324761a14781"
down_revision: str | tuple[str, ...] | None = "46ce00762d02"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create jobs and the sweeper's partial indexes, and grant UPDATE."""
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("pilot_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_dispatched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(none_as_null=False), nullable=False),
        sa.Column("result", postgresql.JSONB(none_as_null=False), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1", name=op.f("ck_jobs_attempts")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_jobs_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    # The table is new and empty, so its indexes build in this transaction
    # rather than CONCURRENTLY.
    op.create_index(
        "ix_jobs_last_dispatched_at",
        "jobs",
        ["last_dispatched_at"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_jobs_lease_until",
        "jobs",
        ["lease_until"],
        unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )
    # The lifecycle updates its jobs; nothing in the application deletes one.
    op.execute("GRANT UPDATE ON jobs TO tally_aloft_app")


def downgrade() -> None:
    """Drop jobs and its indexes."""
    op.drop_index(
        "ix_jobs_lease_until",
        table_name="jobs",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_index(
        "ix_jobs_last_dispatched_at",
        table_name="jobs",
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.drop_table("jobs")
