-- Local development and tests only. Creates the two login roles that infra
-- creates in AWS, and makes the owner role own the database, and so, on
-- Postgres 15 and later, its public schema. Every grant lives in migrations.
-- The Postgres image runs this once, on an empty data directory; the test
-- fixture mounts the same file.
CREATE ROLE tally_aloft_owner LOGIN PASSWORD 'tally_aloft_owner';
CREATE ROLE tally_aloft_app LOGIN PASSWORD 'tally_aloft_app';
ALTER DATABASE tally_aloft OWNER TO tally_aloft_owner;
