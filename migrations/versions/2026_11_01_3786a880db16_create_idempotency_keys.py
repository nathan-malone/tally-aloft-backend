"""
Create idempotency_keys, whose results the application stores and deletes.

Revision ID: 3786a880db16
Revises: 324761a14781
Create Date: 2026-11-01 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3786a880db16"
down_revision: str | tuple[str, ...] | None = "324761a14781"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Create idempotency_keys and its index, and grant UPDATE and DELETE."""
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("pilot_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(none_as_null=False), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint(
            "pilot_id",
            "operation",
            "key",
            name=op.f("uq_idempotency_keys_pilot_id_operation_key"),
        ),
    )
    # The table is new and empty, so its index builds in this transaction
    # rather than CONCURRENTLY.
    op.create_index(
        "ix_idempotency_keys_created_at",
        "idempotency_keys",
        ["created_at"],
        unique=False,
    )
    # run_once stores the action's result on the key row it inserted, before
    # the commit; no other column ever changes. The grant also covers the
    # cleanup's FOR UPDATE SKIP LOCKED, which needs UPDATE on some column.
    op.execute("GRANT UPDATE (result) ON idempotency_keys TO tally_aloft_app")
    # The hourly cleanup deletes keys older than 24 hours.
    op.execute("GRANT DELETE ON idempotency_keys TO tally_aloft_app")


def downgrade() -> None:
    """Drop idempotency_keys and its index."""
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
