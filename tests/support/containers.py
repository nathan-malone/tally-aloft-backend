import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docker.models.containers import Container

# CI builds the image once and names it here; locally a fixture builds one.
IMAGE_VARIABLE = "BACKEND_TEST_IMAGE"
LOCAL_TAG = "tally-aloft-backend:test"
API_APP = "tally_aloft.main:app"
PROBE_APP = "tests.support.probe_app:app"
READY_TIMEOUT_SECONDS = 60


def unique_name(purpose: str) -> str:
    """Return a container or network name no other test run uses."""
    return f"tally-aloft-test-{purpose}-{uuid.uuid4().hex[:8]}"


def wait_for_log(container: Container, text: str) -> None:
    """Wait until `text` appears in the container's output."""
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if text in container.logs().decode():
            return
        container.reload()
        if container.status == "exited":
            break
        time.sleep(0.2)
    logs = container.logs().decode()
    msg = f"{text!r} never appeared in the container's output:\n{logs}"
    raise AssertionError(msg)
