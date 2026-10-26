"""
Test-only routes, mounted on the app by tests and by the image tests' probe app.

This module imports only `fastapi` and the standard library, because the image
tests mount it into the release image, which has no test dependencies.
"""

import asyncio
import logging

from fastapi import APIRouter, Request

router = APIRouter(prefix="/test")
_logger = logging.getLogger("uvicorn.error")


@router.get("/client")
async def client(request: Request) -> str:
    """Return the client address the app sees, after proxy-header handling."""
    return "" if request.client is None else request.client.host


@router.get("/sleep")
async def sleep(seconds: float) -> str:
    """Log a start line, then wait, so a test can signal mid-request."""
    _logger.info("sleep started: %s seconds", seconds)
    await asyncio.sleep(seconds)
    return "done"
