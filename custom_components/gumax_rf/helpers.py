from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_DEVICE_ID, DOMAIN


def device_info_for_entry(entry: ConfigEntry) -> DeviceInfo:
    device_id_hex: str = entry.data[CONF_DEVICE_ID]
    return DeviceInfo(
        identifiers={(DOMAIN, device_id_hex)},
        name=f"Gumax RF ({device_id_hex})",
        manufacturer="Gumax",
        model=f"{device_id_hex} (433.92 MHz)",
    )
