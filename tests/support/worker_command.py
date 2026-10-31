"""
The image contract's worker command, written once for every test that runs it.

Compose, the Makefile and the container tests all run this command, so a
change to a flag changes here and each check follows.
"""

WORKER_MODULE = "tally_aloft.worker:broker"
TEST_WORKER_MODULE = "tests.support.worker:broker"
# How long a stopping worker waits for running tasks, in seconds.
WAIT_TASKS_SECONDS = 25
# Tasks each worker process runs at once, as many as its database pool holds.
MAX_ASYNC_TASKS = 10


def worker_command(
    module: str, *, wait_tasks_seconds: int = WAIT_TASKS_SECONDS
) -> list[str]:
    """Return the worker command for the broker at `module`."""
    return [
        "taskiq",
        "worker",
        module,
        "--wait-tasks-timeout",
        str(wait_tasks_seconds),
        "--max-fails",
        "1",
        "--max-async-tasks",
        str(MAX_ASYNC_TASKS),
    ]
