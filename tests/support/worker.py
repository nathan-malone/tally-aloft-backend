"""
The test worker: the production worker's wiring with the test-only tasks.

Run it as `taskiq worker tests.support.worker:broker`, in the image suite and
in the `compose.e2e.yaml` override. Settings come from the environment, as
for the real worker.
"""

from tally_aloft.bootstrap.settings import load_settings
from tally_aloft.bootstrap.worker import create_worker
from tests.support.tasks import TEST_TASKS

broker = create_worker(load_settings(), TEST_TASKS)
