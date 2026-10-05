"""Syntactic host contract for pystove's ``http://{host}/...`` URLs.

These are connection addresses, never permanent controller identities. Do not
resolve names or equate DNS aliases, absolute/relative names, or address families.
"""

from ipaddress import IPv4Address, IPv6Address
import re

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)
_NUMERIC = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*")


def normalize_host(value: str) -> str:
    """Return an unambiguous URL host, or reject before any controller access.

    Strip surrounding ASCII spaces only; controls and Unicode are rejected.
    IPv6 is supported by the existing URL builder when enclosed in brackets.
    Scoped IPv6, ports, legacy IPv4 notations and URL components are unsupported.
    A trailing DNS root dot is retained (resolver search rules can differ).
    """
    if not isinstance(value, str):
        raise ValueError("Host must be text")
    host = value.strip(" ")
    if not host or not host.isascii() or any(c.isspace() for c in host):
        raise ValueError("Empty host or unsupported whitespace/Unicode")
    host = host.lower()
    if "%" in host:
        raise ValueError("Scoped or escaped hosts are unsupported")
    if ":" in host or "[" in host or "]" in host:
        literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
        return f"[{IPv6Address(literal).compressed}]"
    try:
        return str(IPv4Address(host))
    except ValueError:
        pass
    name = host.removesuffix(".")
    if (
        len(name) > 253
        or _NUMERIC.fullmatch(name)
        or not all(_LABEL.fullmatch(label) for label in name.split("."))
    ):
        raise ValueError("Invalid hostname or ambiguous IPv4 notation")
    return host


def host_key(value: str | None) -> tuple[bool, str | None]:
    """Compare stored/YAML values without changing existing entry data.

    Invalid historical values retain exact-string comparison; they are not
    silently repaired or confused with a valid newly configured host.
    """
    try:
        return True, normalize_host(value)
    except ValueError:
        return False, value
