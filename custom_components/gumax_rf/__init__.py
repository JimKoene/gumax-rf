from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_CHANNEL_PREFIX,
    CONF_DEVICE_ID,
    CONF_ESPHOME_NODE,
    CONF_X_DEV,
    DEFAULT_CHANNEL_PREFIX,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["binary_sensor", "cover", "sensor"]


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    if config_entry.version == 1:
        device_id: str = config_entry.data[CONF_DEVICE_ID]
        node_name: str = config_entry.data[CONF_ESPHOME_NODE]
        new_unique_id = f"{device_id}_{node_name}"

        entity_registry = er.async_get(hass)
        old_prefix = f"{DOMAIN}_{device_id}_"
        new_prefix = f"{DOMAIN}_{device_id}_{node_name}_"
        for entity_entry in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id):
            if entity_entry.unique_id.startswith(old_prefix):
                new_uid = new_prefix + entity_entry.unique_id[len(old_prefix):]
                entity_registry.async_update_entity(entity_entry.entity_id, new_unique_id=new_uid)

        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, device_id)})
        if device is not None:
            device_registry.async_update_device(device.id, new_identifiers={(DOMAIN, new_unique_id)})

        hass.config_entries.async_update_entry(
            config_entry,
            unique_id=new_unique_id,
            title=f"Gumax RF ({device_id}) @ {node_name}",
            version=2,
        )
        _LOGGER.info("Migrated Gumax RF entry %s to version 2", config_entry.entry_id)

    return True


_LEGACY_ISSUE_PREFIX = "legacy_checksum_"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if CONF_CHANNEL_PREFIX not in entry.options:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_CHANNEL_PREFIX: DEFAULT_CHANNEL_PREFIX}
        )

    if CONF_X_DEV not in entry.data:
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{_LEGACY_ISSUE_PREFIX}{entry.entry_id}",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="legacy_checksum",
            translation_placeholders={"title": entry.title},
            data={"entry_id": entry.entry_id},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, f"{_LEGACY_ISSUE_PREFIX}{entry.entry_id}")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, f"{_LEGACY_ISSUE_PREFIX}{entry.entry_id}")
