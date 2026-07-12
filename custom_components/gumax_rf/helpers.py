from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from ._protocol import LEGACY_PROFILE, DeviceProfile
from .const import (
    CONF_B9_DEFAULT,
    CONF_B9_K1,
    CONF_B9_K9,
    CONF_DEVICE_ID,
    CONF_ESPHOME_NODE,
    CONF_K1_EXTRA,
    CONF_K9_EXTRA,
    CONF_X_DEV,
    DOMAIN,
)


def device_info_for_entry(entry: ConfigEntry) -> DeviceInfo:
    device_id_hex: str = entry.data[CONF_DEVICE_ID]
    node_name: str = entry.data[CONF_ESPHOME_NODE]
    return DeviceInfo(
        identifiers={(DOMAIN, f"{device_id_hex}_{node_name}")},
        name=f"Gumax RF ({device_id_hex}) @ {node_name}",
        manufacturer="Gumax",
        model=f"{device_id_hex} (433.92 MHz)",
    )


def device_profile_for_entry(entry: ConfigEntry) -> DeviceProfile:
    """Build the calibration profile for an entry, falling back to the
    pre-calibration behaviour for entries that predate the capture flow."""
    data = entry.data
    if CONF_X_DEV not in data:
        return LEGACY_PROFILE
    return DeviceProfile(
        x_dev=data[CONF_X_DEV],
        k1_extra=data.get(CONF_K1_EXTRA, 0),
        k9_extra=data.get(CONF_K9_EXTRA, 0),
        b9_default=data.get(CONF_B9_DEFAULT, 1),
        b9_k1=data.get(CONF_B9_K1, 1),
        b9_k9=data.get(CONF_B9_K9, 1),
    )
