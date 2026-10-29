"""
Checks for the data stores' connection URLs, which the settings models run.

Each takes whether the process is deployed, where every connection must be
encrypted and verify the server.
"""

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from redis.asyncio.connection import parse_url
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

if TYPE_CHECKING:
    from pydantic import SecretStr

_DATABASE_DRIVER = "postgresql+asyncpg"
_NOT_A_DATABASE_URL = (
    "must be a postgresql+asyncpg://user:password@host:port/database URL, with "
    "reserved characters in the user and password percent-encoded"
)
# asyncpg's `require` encrypts without checking the server, and `verify-ca`
# checks the certificate but not the host name, so deployments need
# `verify-full`. asyncpg reads the CA bundle from PGSSLROOTCERT.
_VERIFIED_SSL = "verify-full"
_NOT_VERIFIED = "must end in ?ssl=verify-full in staging and production"
# A `host` parameter replaces the URL's host, and asyncpg reads one starting
# with `/` as a Unix socket, which it connects to without TLS.
_HOST_PARAMETER = "must not have a host parameter in staging and production"

_VALKEY_SCHEMES = frozenset({"redis", "rediss"})
_ENCRYPTED_VALKEY_SCHEME = "rediss"
# An empty path, or a database number. redis-py reads any other path as
# database 0, or joins its digits, as `/1/2` into 12.
_VALKEY_DATABASE_PATH = re.compile(r"(/[0-9]*)?")
_NOT_A_VALKEY_URL = (
    "must be a redis:// or rediss://[user:password@]host[:port][/database] "
    "URL, with a numbered database if any, no query or fragment, and reserved "
    "characters in the user and password percent-encoded"
)
_NOT_ENCRYPTED = "must be a rediss:// URL in staging and production"
# The broker shares the instance, so anything that reached it unauthenticated
# could read the cache and queue tasks.
_NOT_AUTHENTICATED = "must carry a password in staging and production"


def check_database_url(value: SecretStr, *, deployed: bool) -> SecretStr:
    """
    Return `value` if it is a SQLAlchemy URL for asyncpg, else raise.

    SQLAlchemy's own parser decides, so the engine accepts exactly what settings
    accepted. Its errors quote the URL, password and all, so none is chained.
    When `deployed`, the URL must also verify the server, and connect to the
    host it names before its path.
    """
    try:
        url = make_url(value.get_secret_value())
    except ArgumentError, ValueError:
        raise ValueError(_NOT_A_DATABASE_URL) from None
    if (
        url.drivername != _DATABASE_DRIVER
        or url.host in {None, ""}
        or url.database in {None, ""}
    ):
        raise ValueError(_NOT_A_DATABASE_URL)
    if deployed and url.query.get("ssl") != _VERIFIED_SSL:
        raise ValueError(_NOT_VERIFIED)
    if deployed and "host" in url.query:
        raise ValueError(_HOST_PARAMETER)
    return value


def check_valkey_url(value: SecretStr, *, deployed: bool) -> SecretStr:
    """
    Return `value` if it is a Valkey URL the client reads as written, else raise.

    redis-py's own parser must accept it. That parser ignores a port of 0 and
    a path that isn't a database number, so both are checked here. A query
    could change the client's timeouts or turn off TLS verification, so none
    is allowed. redis-py's errors quote parts of the URL, so none is chained.
    When `deployed`, the URL must also use TLS and carry a password, an AUTH
    token or a user's.
    """
    url = value.get_secret_value()
    try:
        parts = urlsplit(url)
        port = parts.port
        _ = parse_url(url)
    except ValueError:
        raise ValueError(_NOT_A_VALKEY_URL) from None
    if (
        parts.scheme not in _VALKEY_SCHEMES
        or parts.hostname in {None, ""}
        or port == 0
        or parts.query != ""
        or parts.fragment != ""
        or _VALKEY_DATABASE_PATH.fullmatch(parts.path) is None
    ):
        raise ValueError(_NOT_A_VALKEY_URL)
    if deployed and parts.scheme != _ENCRYPTED_VALKEY_SCHEME:
        raise ValueError(_NOT_ENCRYPTED)
    if deployed and parts.password in {None, ""}:
        raise ValueError(_NOT_AUTHENTICATED)
    return value
