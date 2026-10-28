import pytest

from tally_aloft.bootstrap.settings import MigrationSettings, Settings, variable_name
from tests.support.paths import ROOT

VARIABLES = [
    variable_name(field)
    for model in (Settings, MigrationSettings)
    for field in model.model_fields
]


@pytest.mark.parametrize("variable", VARIABLES)
def test_env_example_lists_every_setting(variable: str) -> None:
    lines = (ROOT / ".env.example").read_text().splitlines()

    assert any(line.startswith(f"{variable}=") for line in lines)


@pytest.mark.parametrize("variable", VARIABLES)
def test_readme_settings_table_lists_every_setting(variable: str) -> None:
    lines = (ROOT / "README.md").read_text().splitlines()

    assert any(line.startswith(f"| `{variable}` |") for line in lines)
