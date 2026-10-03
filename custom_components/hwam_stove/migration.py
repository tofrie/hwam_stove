"""B01: migrate only known 1.0.0b2 identities, preserving registry records."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Frozen release contract, checked against all 40 historical fixture rows.
# Keep the entity domain: sensor and switch share the night_lowering key.
LEGACY_ENTITY_KEYS = {
    "sensor": (
        "algorithm", "message_id", "new_fire_wood_estimate", "night_lowering",
        "operation_mode", "oxygen_level", "phase", "room_temperature",
        "stove_temperature", "time_since_remote_msg", "time_to_new_fire_wood",
        "valve1_position", "valve2_position", "valve3_position",
    ),
    "binary_sensor": (
        "refill_alarm", "maintenance_alarms", "maintenance_alarms_backup_battery_low",
        "maintenance_alarms_o2_sensor_fault", "maintenance_alarms_o2_sensor_offset",
        "maintenance_alarms_stove_temp_sensor_fault",
        "maintenance_alarms_room_temp_sensor_fault",
        "maintenance_alarms_communication_fault",
        "maintenance_alarms_room_temp_sensor_battery_low", "safety_alarms",
        "safety_alarms_valve_fault", "safety_alarms_bad_configuration",
        "safety_alarms_valve_disconnect", "safety_alarms_valve_calibration_error",
        "safety_alarms_stove_overheat", "safety_alarms_door_open_too_long",
        "safety_alarms_manual_safety_alarm", "safety_alarms_stove_sensor_fault",
    ),
    "button": ("start", "sync_clock"),
    "switch": ("night_lowering", "remote_refill_alarm"),
    "number": ("burn_level",),
    "time": ("night_begin_time", "night_end_time"),
    "datetime": ("date_time",),
}


@callback
def async_migrate_registry_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Preflight every identity, then update without yielding to other tasks.

    The registries schedule independent storage writes, not a transaction.
    Retain the legacy id for retry/recovery; mark version 2 only after success.
    Compensating updates on failure are best effort and preserve record IDs.
    """
    if CONF_ID not in entry.data:
        # Master-created v1 entries already use entry_id; leave registries alone.
        hass.config_entries.async_update_entry(entry, version=2)
        return True
    legacy_id = entry.data[CONF_ID]
    if not isinstance(legacy_id, str) or not legacy_id:
        _LOGGER.error("B01 migration stopped: invalid stored legacy id")
        return False

    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    device_changes: list[tuple[dr.DeviceEntry, set[tuple[str, str]]]] = []
    entity_changes: list[tuple[er.RegistryEntry, str]] = []
    try:
        seen_devices = set()
        for kind in ("stove", "remote"):
            old_identifier = (DOMAIN, f"{legacy_id}-{kind}")
            new_identifier = (DOMAIN, f"{entry.entry_id}-{kind}")
            old = devices.async_get_device_by_identifier(old_identifier, entry.entry_id)
            new = devices.async_get_device_by_identifier(new_identifier, entry.entry_id)
            if old and new and old.id != new.id:
                raise ValueError("Device target already belongs to another record")
            if device := old or new:
                if device.id in seen_devices:
                    raise ValueError("Stove and remote identities share one record")
                seen_devices.add(device.id)
            if old and old_identifier != new_identifier:
                device_changes.append((
                    old, (old.identifiers - {old_identifier}) | {new_identifier}
                ))

        for domain, keys in LEGACY_ENTITY_KEYS.items():
            for key in keys:
                old_id = entities.async_get_entity_id(
                    domain, DOMAIN, f"{legacy_id}-{key}"
                )
                target = f"{entry.entry_id}-{key}"
                new_id = entities.async_get_entity_id(domain, DOMAIN, target)
                old = entities.async_get(old_id) if old_id else None
                new = entities.async_get(new_id) if new_id else None
                if new and new.config_entry_id != entry.entry_id:
                    raise ValueError("Entity target belongs to another config entry")
                if old is None or old.config_entry_id != entry.entry_id:
                    continue
                if new and new.id != old.id:
                    raise ValueError("Entity target already belongs to another record")
                if old.unique_id != target:
                    entity_changes.append((old, target))
    except ValueError as err:
        _LOGGER.error("B01 migration stopped before registry changes: %s", err)
        return False

    try:
        # Existing device IDs stay intact, so entity/device associations never move.
        for old_device, identifiers in device_changes:
            devices.async_update_device(old_device.id, new_identifiers=identifiers)
        for old_entity, unique_id in entity_changes:
            entities.async_update_entity(old_entity.entity_id, new_unique_id=unique_id)
        hass.config_entries.async_update_entry(entry, version=2)
    except Exception:
        # Migration boundary: include failures after a registry has applied an update.
        # Do not catch cancellation/BaseException. No await occurs during mutation.
        _LOGGER.exception("B01 migration failed; restoring previous identities")
        if entry.version != 1:
            try:
                hass.config_entries.async_update_entry(entry, version=1)
            except Exception:
                # All registry updates completed before the version write. Keep
                # them if its marker cannot be reset; v2 must never point at v1 IDs.
                _LOGGER.exception(
                    "B01 version rollback failed; keeping migrated records"
                )
                return False
        for old_entity, _ in reversed(entity_changes):
            try:
                current = entities.async_get(old_entity.entity_id)
                if current and current.unique_id != old_entity.unique_id:
                    entities.async_update_entity(
                        old_entity.entity_id, new_unique_id=old_entity.unique_id
                    )
            except Exception:
                _LOGGER.exception("B01 entity rollback failed; retry migration")
        for old_device, _ in reversed(device_changes):
            try:
                current = devices.async_get(old_device.id)
                if current and current.identifiers != old_device.identifiers:
                    devices.async_update_device(
                        old_device.id, new_identifiers=old_device.identifiers
                    )
            except Exception:
                _LOGGER.exception("B01 device rollback failed; retry migration")
        return False
    return True
