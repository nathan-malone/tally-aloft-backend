"""
${(message or "").rstrip(".")}.

Revision ID: ${up_revision}
${("Revises: " + (", ".join(down_revision) if isinstance(down_revision, (tuple, list)) else (down_revision or ""))).rstrip()}
Create Date: ${create_date}
"""

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | tuple[str, ...] | None = ${repr(down_revision)}
branch_labels: str | tuple[str, ...] | None = ${repr(branch_labels)}
depends_on: str | tuple[str, ...] | None = ${repr(depends_on)}


def upgrade() -> None:
    """Apply this revision."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Revert this revision."""
    ${downgrades if downgrades else "pass"}
