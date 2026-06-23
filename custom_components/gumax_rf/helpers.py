from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_DEVICE_ID, CONF_ESPHOME_NODE, DOMAIN


def device_info_for_entry(entry: ConfigEntry) -> DeviceInfo:
    device_id_hex: str = entry.data[CONF_DEVICE_ID]
    node_name: str = entry.data[CONF_ESPHOME_NODE]
    return DeviceInfo(
        identifiers={(DOMAIN, f"{device_id_hex}_{node_name}")},
        name=f"Gumax RF ({device_id_hex}) @ {node_name}",
        manufacturer="Gumax",
        model=f"{device_id_hex} (433.92 MHz)",
    )
