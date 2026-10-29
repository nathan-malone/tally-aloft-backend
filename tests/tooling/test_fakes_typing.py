from dataclasses import dataclass

import pytest

from tests.support.pyrefly import pyrefly

PROBE = """\
{imports}


class Probe:
{body}


_probe: {port} = Probe()
"""


@dataclass(frozen=True, slots=True)
class PortCase:
    """A port, a probe class that matches it, and one that drifts from it."""

    imports: str
    port: str
    matching: str
    drifted: str

    def source(self, body: str) -> str:
        return PROBE.format(imports=self.imports, port=self.port, body=body)


CASES = [
    pytest.param(
        PortCase(
            imports="from tally_aloft.application.ports.health import DependencyCheck",
            port="DependencyCheck",
            matching="""\
    name = "probe"

    async def check(self) -> None:
        return None""",
            drifted="""\
    name = "probe"

    async def check(self, timeout: float) -> None:
        return None""",
        ),
        id="DependencyCheck",
    ),
    pytest.param(
        PortCase(
            imports="from datetime import timedelta\n\n"
            "from tally_aloft.application.ports.cache import Cache",
            port="Cache",
            matching="""\
    async def get(self, key: str) -> bytes | None:
        return None

    async def set(self, key: str, value: bytes, ttl: timedelta) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None""",
            drifted="""\
    async def get(self, key: str) -> bytes | None:
        return None

    async def set(self, key: str, value: bytes) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None""",
        ),
        id="Cache",
    ),
]


@pytest.mark.parametrize("case", CASES)
def test_a_fake_matching_its_port_passes(case: PortCase) -> None:
    result = pyrefly(source=case.source(case.matching), filename="probe.py")

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", CASES)
def test_a_fake_that_drifts_from_its_port_fails(case: PortCase) -> None:
    result = pyrefly(source=case.source(case.drifted), filename="probe.py")

    assert result.returncode != 0
    assert "bad-assignment" in result.stdout + result.stderr
