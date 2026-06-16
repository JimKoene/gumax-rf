from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_CHANNEL_PREFIX, DEFAULT_CHANNEL_PREFIX, DOMAIN

PLATFORMS = ["binary_sensor", "cover", "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if CONF_CHANNEL_PREFIX not in entry.options:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_CHANNEL_PREFIX: DEFAULT_CHANNEL_PREFIX}
        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
