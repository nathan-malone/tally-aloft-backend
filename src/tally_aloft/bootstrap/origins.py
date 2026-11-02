"""
The check for a CORS origin, which the settings model runs on each entry.

Starlette compares origins as exact strings, so an origin must be exactly the
form a browser sends, or it would silently match nothing.
"""

import re
from ipaddress import IPv4Address, IPv6Address
from urllib.parse import urlsplit

_ALLOWS_EVERY_ORIGIN = "would allow every origin; list the web app's origins"
_NOT_AN_ORIGIN = (
    "must be a scheme://host[:port] as a browser sends it: lowercase, with no "
    "path and no default port"
)
_DEFAULT_PORTS = {"http": 80, "https": 443}
# A host label a browser parses as a number, which makes the host IPv4.
_NUMBER_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*")
# The URL Standard's forbidden domain code points. A browser can't parse a host
# with one, and decodes a percent-encoded one, so it never sends either form.
_FORBIDDEN_DOMAIN_CHARACTER = re.compile(r"[\x00-\x20#%/:<>?@\[\\\]^|\x7f]")


def check_origin(origin: str) -> str:
    """Return `origin` if it is exactly a browser's form of one, else raise."""
    if origin == "*":
        raise ValueError(_ALLOWS_EVERY_ORIGIN)
    parts = urlsplit(origin)
    try:
        port = parts.port
    except ValueError:
        raise ValueError(_NOT_AN_ORIGIN) from None
    host = "" if parts.hostname is None else _browser_host(parts.hostname)
    # urlsplit lowercases the scheme and host and leaves out any path, query,
    # fragment or user info, so an origin that differs from this rebuilt form
    # isn't one a browser sends. Browsers also send a non-ASCII host in its
    # punycode form.
    canonical = f"{parts.scheme}://{host}" + ("" if port is None else f":{port}")
    if (
        parts.scheme not in _DEFAULT_PORTS
        or host == ""
        or not host.isascii()
        or port in {0, _DEFAULT_PORTS[parts.scheme]}
        or canonical != origin
    ):
        raise ValueError(_NOT_AN_ORIGIN)
    return origin


def _browser_host(hostname: str) -> str:
    """
    Return `hostname` as a browser writes it in an origin, or "" if it can't be.

    A browser writes an IPv6 address in brackets, compressed, with no zone and
    no dotted IPv4 part, and reads a host whose last label is a number as an
    IPv4 address, which it writes as four decimal numbers: `127.1` is sent as
    `127.0.0.1`. A host with a forbidden or percent-encoded character can't be
    written. Other hosts are kept as they are.
    """
    if ":" in hostname:
        return _browser_ipv6_host(hostname)
    if _FORBIDDEN_DOMAIN_CHARACTER.search(hostname) is not None:
        return ""
    last_label = hostname.removesuffix(".").rpartition(".")[2]
    if _NUMBER_LABEL.fullmatch(last_label) is not None:
        try:
            return str(IPv4Address(hostname))
        except ValueError:
            return ""
    return hostname


def _browser_ipv6_host(hostname: str) -> str:
    """Return an IPv6 `hostname` as `_browser_host` describes."""
    try:
        address = IPv6Address(hostname)
    except ValueError:
        return ""
    if address.scope_id is not None:
        return ""
    mapped = address.ipv4_mapped
    if mapped is None:
        # Python compresses as browsers do: the first longest zero run.
        return f"[{address.compressed}]"
    # Python writes the IPv4 part dotted; browsers write it in hex.
    return f"[::ffff:{int(mapped) >> 16:x}:{int(mapped) & 0xFFFF:x}]"
