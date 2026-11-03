"""
The test worker: the production worker's wiring with the test-only tasks.

Run it as `taskiq worker tests.support.worker:broker`, in the image suite and
in the `compose.e2e.yaml` override. Its scheduler kicks the sweeper every
second; the image suite runs it as `taskiq scheduler
tests.support.worker:scheduler`. Settings come from the environment, as for
the real worker.
"""

from tally_aloft.bootstrap.observability import WORKER_SERVICE, configure_observability
from tally_aloft.bootstrap.settings import load_settings
from tally_aloft.bootstrap.worker import create_scheduler, create_worker
from tests.support.tasks import TEST_TASKS

settings = load_settings()
configure_observability(settings, WORKER_SERVICE)
broker = create_worker(settings, TEST_TASKS)
scheduler = create_scheduler(broker)
