"""
Give the application role SELECT and INSERT on new tables, and no DDL.

Revision ID: 46ce00762d02
Revises:
Create Date: 2026-10-27 21:49:11.221709
"""

from alembic import op

revision: str = "46ce00762d02"
down_revision: str | tuple[str, ...] | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

# The database's name differs between environments, so it's formatted in.
_REVOKE_TEMPORARY = """
DO $$
BEGIN
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
END
$$
"""
# Default privileges cover only the tables their role creates, so migrations
# run as the owner role and nothing else.
_REQUIRE_OWNER = """
DO $$
BEGIN
    IF current_user <> 'tally_aloft_owner' THEN
        RAISE EXCEPTION 'migrations must run as tally_aloft_owner, not %', current_user;
    END IF;
END
$$
"""
# A REVOKE by a role without the grant option only warns, which happens on a
# database whose public schema isn't the owner's, such as one upgraded from
# Postgres 14, and a grant or role attribute set outside the migrations would
# go unnoticed. So the revision checks the outcome, and fails rather than leave
# the application role able to create objects.
_REQUIRE_NO_DDL = """
DO $$
BEGIN
    IF has_database_privilege('tally_aloft_app', current_database(), 'CREATE')
        OR has_database_privilege('tally_aloft_app', current_database(), 'TEMPORARY')
        OR has_schema_privilege('tally_aloft_app', 'public', 'CREATE')
        OR EXISTS (
            SELECT FROM pg_roles
            WHERE rolname = 'tally_aloft_app'
                AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls)
        )
    THEN
        RAISE EXCEPTION 'tally_aloft_app can still create objects: make '
            'tally_aloft_owner the owner of the database and its public schema, '
            'and grant tally_aloft_app nothing else';
    END IF;
END
$$
"""


def upgrade() -> None:
    """Grant schema use and default SELECT and INSERT, and close DDL to PUBLIC."""
    op.execute(_REQUIRE_OWNER)
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute(_REVOKE_TEMPORARY)
    op.execute("GRANT USAGE ON SCHEMA public TO tally_aloft_app")
    # Only tables the owner creates get these. A migration that creates a
    # mutable table grants UPDATE or DELETE on it itself.
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE tally_aloft_owner IN SCHEMA public "
        "GRANT SELECT, INSERT ON TABLES TO tally_aloft_app"
    )
    op.execute(_REQUIRE_NO_DDL)


def downgrade() -> None:
    """Revoke the grants. CREATE and TEMPORARY stay closed to PUBLIC."""
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE tally_aloft_owner IN SCHEMA public "
        "REVOKE SELECT, INSERT ON TABLES FROM tally_aloft_app"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM tally_aloft_app")
