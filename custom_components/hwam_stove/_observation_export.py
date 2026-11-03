"""Explicit private file exports; never serve household observations via /local."""

import asyncio
from contextlib import contextmanager, suppress
from datetime import timedelta
import json
import os
import re
import stat
import time
from uuid import uuid4

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt
import voluptuous as vol

from ._analytics import _settle
from .const import DATA_STOVES, DOMAIN

DIRECTORY = "hwam_observation_exports"
PREFIX = "/api/hwam_stove/observation_exports/"
NAME = re.compile(r"hwam_observations_\d{8}T\d{6}Z_[0-9a-f]{32}\.json")
MAX_FILES = 5
MAX_AGE = 24 * 60 * 60
MAX_BYTES = 8_000_000
LINK_SECONDS = 300


@contextmanager
def private_directory(storage):
    """Use directory descriptors; refuse symlinks and unsafe existing permissions."""
    parent = os.open(storage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory = None
    try:
        with suppress(FileExistsError):
            os.mkdir(DIRECTORY, mode=0o700, dir_fd=parent)
        directory = os.open(
            DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise OSError("Unsafe export directory")
        yield directory
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


def retained(directory, now):
    """Inspect our strict filename inventory only; never follow links."""
    result = []
    for name in os.listdir(directory):
        temporary = (
            name.startswith(".")
            and name.endswith(".tmp")
            and NAME.fullmatch(name[1:-4])
        )
        if not NAME.fullmatch(name) and not temporary:
            continue
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
        ):
            raise OSError("Unsafe export file")
        if temporary or now - info.st_mtime >= MAX_AGE:
            os.unlink(name, dir_fd=directory)
        else:
            result.append((info.st_mtime, name))
    return sorted(result)


def write_export(storage, filename, payload):
    """Serialize and atomically publish locally in an executor, not on the loop."""
    body = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(body) > MAX_BYTES:
        raise ValueError("Export exceeds bound")
    with private_directory(storage) as directory:
        inventory = retained(directory, time.time())
        # Only explicit export/download requests perform retention maintenance.
        for _, old in inventory[: max(0, len(inventory) - MAX_FILES + 1)]:
            os.unlink(old, dir_fd=directory)
        temporary = "." + filename + ".tmp"
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory)
    return len(body)


def read_export(storage, filename):
    """Bounded bytes from an opened regular file; no FileResponse path race."""
    if not NAME.fullmatch(filename):
        raise FileNotFoundError
    with private_directory(storage) as directory:
        retained(directory, time.time())
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > MAX_BYTES
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
            ):
                raise OSError("Unsafe export file")
            body = source.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise OSError("Export exceeds bound")
            return body


async def disk_job(hass, function, *args):
    """Keep the export lock until owned filesystem work has finished."""

    async def run():
        return await hass.async_add_executor_job(function, *args)

    return await _settle(run())


class ObservationDownload(HomeAssistantView):
    url = PREFIX + "{filename}"
    name = "api:hwam_stove:observation_exports"
    requires_auth = True

    def __init__(self, hass, lock):
        self.hass, self.lock = hass, lock

    async def get(self, request, filename):
        if not request["hass_user"].is_admin:
            raise web.HTTPForbidden
        try:
            async with self.lock:
                body = await disk_job(
                    self.hass, read_export, self.hass.config.path(".storage"), filename
                )
        except (OSError, ValueError):
            raise web.HTTPNotFound from None
        return web.Response(
            body=body,
            content_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )


@callback
def register_export(hass):
    if hass.services.has_service(DOMAIN, "export_observations"):
        return
    lock = asyncio.Lock()
    view_registered = False

    async def export(call):
        nonlocal view_registered
        user = (
            await hass.auth.async_get_user(call.context.user_id)
            if call.context.user_id
            else None
        )
        if user is None or not user.is_admin or not user.is_active:
            raise HomeAssistantError("Observation export requires an administrator")
        # Bind the download to an existing user token, never a content/system user.
        token = next(iter(user.refresh_tokens.values()), None)
        if token is None or getattr(hass, "http", None) is None:
            raise HomeAssistantError("Authenticated HTTP download unavailable")
        stoves = hass.data.get(DOMAIN, {}).get(DATA_STOVES, {})
        entry_id = call.data.get("config_entry_id")
        coordinator = (
            stoves.get(entry_id)
            if entry_id
            else (next(iter(stoves.values())) if len(stoves) == 1 else None)
        )
        if coordinator is None:
            raise HomeAssistantError("Select exactly one loaded HWAM config entry")
        async with lock:
            if not view_registered:
                hass.http.register_view(ObservationDownload(hass, lock))
                view_registered = True
            filename = (
                f"hwam_observations_{dt.utcnow():%Y%m%dT%H%M%SZ}_{uuid4().hex}.json"
            )
            path = async_sign_path(
                hass,
                PREFIX + filename,
                timedelta(seconds=LINK_SECONDS),
                refresh_token_id=token.id,
            )
            try:
                payload = coordinator.observations.export()
                size = await disk_job(
                    hass, write_export, hass.config.path(".storage"), filename, payload
                )
            except (OSError, ValueError, TypeError):
                raise HomeAssistantError("Observation file export failed") from None
        return {
            "filename": filename,
            "records": len(payload["rows"]),
            "bytes": size,
            "download_path": path,
        }

    hass.services.async_register(
        DOMAIN,
        "export_observations",
        export,
        schema=vol.Schema({vol.Optional("config_entry_id"): str}),
        supports_response=SupportsResponse.ONLY,
    )
