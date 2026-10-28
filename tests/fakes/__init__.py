from typing import TYPE_CHECKING

from tests.fakes.health import FakeDependencyCheck

if TYPE_CHECKING:
    from tally_aloft.application.ports.health import DependencyCheck

# Each fake is assigned to a variable typed as its port, so the type check
# fails when a fake drifts from the Protocol it stands in for.
_dependency_check: DependencyCheck = FakeDependencyCheck(name="fake", outcome="pass")
