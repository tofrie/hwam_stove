"""Constants for hwam_stove."""

from enum import StrEnum

from aiohttp import (
    ClientConnectionError,
    ClientPayloadError,
    ClientProxyConnectionError,
    ClientSSLError,
    ServerFingerprintMismatch,
)

DATA_STOVES = "stoves"

DOMAIN = "hwam_stove"

# Only for the Stove.create boundary, never polling, commands or owned cleanup.
CREATE_TRANSPORT_ERRORS = (TimeoutError, ClientConnectionError, ClientPayloadError)
# These inherit socket errors but are not proven temporary direct-HTTP failures.
EXCLUDED_CREATE_ERRORS = (
    ClientSSLError, ClientProxyConnectionError, ServerFingerprintMismatch,
)


class StoveDeviceIdentifier(StrEnum):
    """Device identification strings."""

    REMOTE = "remote"
    STOVE = "stove"
