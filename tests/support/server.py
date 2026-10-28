import asyncio
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

import httpx

from tally_aloft.bootstrap.settings import UNUSED_DATABASE_URL
from tests.support.paths import ROOT

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator, Mapping
    from typing import IO

API_APP = "tally_aloft.main:app"
PROBE_APP = "tests.support.probe_app:app"
# How long a server, on the host or in a container, may take to start serving.
STARTUP_TIMEOUT_SECONDS = 60
# How long a terminated server may take to exit. Windows tears processes down
# one after another, so under a parallel test run this takes seconds.
STOP_TIMEOUT_SECONDS = 60
# The fewest variables the API starts with. The routes these servers answer
# never touch the database, so the URL that refuses connections will do.
REQUIRED_SETTINGS = {
    "TALLY_ENVIRONMENT": "test",
    "TALLY_DATABASE_URL": UNUSED_DATABASE_URL,
}


def without_tally_variables(environ: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of `environ` with every `TALLY_` variable removed."""
    return {
        name: value for name, value in environ.items() if not name.startswith("TALLY_")
    }


def free_port() -> int:
    """Return a TCP port on 127.0.0.1 that was free a moment ago."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@asynccontextmanager
async def silent_server() -> AsyncGenerator[int]:
    """
    Yield the port of a local server that accepts connections and never answers.

    It stands in for a stalled database, which a client only escapes through
    its own timeout.
    """
    writers: list[asyncio.StreamWriter] = []

    async def hold(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writers.append(writer)

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    try:
        yield int(server.sockets[0].getsockname()[1])
    finally:
        server.close()
        for writer in writers:
            writer.close()
        await server.wait_closed()


def uvicorn_command(app: str, port: int) -> list[str]:
    """Return the host command that serves `app` with the image's proxy flag."""
    return [
        sys.executable,
        "-m",
        "uvicorn",
        app,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-proxy-headers",
    ]


@contextmanager
def running_server(app: str, env: Mapping[str, str]) -> Generator[str]:
    """Serve `app` in a Uvicorn subprocess, and yield its base URL once it's live."""
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    # A file, not a pipe, so a chatty server can never block on a full pipe.
    with tempfile.TemporaryFile(mode="w+") as stderr:
        process = subprocess.Popen(
            uvicorn_command(app, port),
            cwd=ROOT,
            env=dict(env),
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            text=True,
        )
        try:
            _wait_until_live(base_url, process, stderr)
            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                # Never leave a server running after the test.
                process.kill()
                process.wait()
                raise


def _wait_until_live(
    base_url: str, process: subprocess.Popen[str], stderr: IO[str]
) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr.seek(0)
            msg = f"server exited with {process.returncode}: {stderr.read()}"
            raise RuntimeError(msg)
        try:
            if httpx.get(f"{base_url}/livez").status_code == httpx.codes.OK:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    msg = f"server at {base_url} wasn't live within {STARTUP_TIMEOUT_SECONDS}s"
    raise TimeoutError(msg)
