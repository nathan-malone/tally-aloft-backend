import configparser

from alembic.config import Config
from alembic.script import ScriptDirectory

from tests.support.migrations import ALEMBIC_INI


def _ini() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    _ = parser.read(ALEMBIC_INI)
    return parser


def test_the_migrations_have_one_head() -> None:
    script = ScriptDirectory.from_config(Config(ALEMBIC_INI))

    assert len(script.get_heads()) == 1


def test_alembic_ini_holds_no_database_url() -> None:
    # The owner's URL comes from MigrationSettings only.
    assert not _ini().has_option("alembic", "sqlalchemy.url")


def test_alembic_ini_splits_paths_on_the_os_separator() -> None:
    assert _ini().get("alembic", "path_separator") == "os"


def test_new_revisions_are_fixed_and_formatted_by_ruff() -> None:
    hooks = _ini()["post_write_hooks"]

    assert [hook.strip() for hook in hooks["hooks"].split(",")] == [
        "ruff_fix",
        "ruff_format",
    ]
    assert hooks["ruff_fix.options"].startswith("check --fix")
    assert hooks["ruff_format.options"].startswith("format")
