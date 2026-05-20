from __future__ import annotations

import logging

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._protocol import device_id_from_hex, encode, encode_cc
from .const import (
    CHANNELS,
    CONF_DEVICE_ID,
    CONF_ESPHOME_NODE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_REPEAT = 3  # transmit 3× for reliable reception


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[CoverEntity] = [GumaxCover(entry, ch) for ch in CHANNELS]
    entities.append(GumaxCCCover(entry))
    async_add_entities(entities)


class GumaxCover(CoverEntity):
    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_is_closed = None  # one-way RF: position unknown
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(self, entry: ConfigEntry, channel: int) -> None:
        self._entry = entry
        self._channel = channel
        device_id_hex: str = entry.data[CONF_DEVICE_ID]
        self._device_id_bin = device_id_from_hex(device_id_hex)
        self._node_name: str = entry.data[CONF_ESPHOME_NODE]
        self._attr_unique_id = f"{DOMAIN}_{device_id_hex}_{channel}"
        self._attr_name = f"K{channel}"

    @property
    def device_info(self) -> DeviceInfo:
        device_id_hex: str = self._entry.data[CONF_DEVICE_ID]
        return DeviceInfo(
            identifiers={(DOMAIN, device_id_hex)},
            name=f"Gumax RF ({device_id_hex})",
            manufacturer="Gumax",
            model=f"{device_id_hex} (433.92 MHz)",
        )

    async def async_open_cover(self, **kwargs) -> None:
        await self._transmit("up")

    async def async_close_cover(self, **kwargs) -> None:
        await self._transmit("down")

    async def async_stop_cover(self, **kwargs) -> None:
        await self._transmit("stop")

    async def _transmit(self, command: str) -> None:
        pulses = encode(self._channel, command, self._device_id_bin)
        pulses_str = ",".join(str(p) for p in pulses)
        for _ in range(_REPEAT):
            try:
                await self.hass.services.async_call(
                    "esphome",
                    f"{self._node_name}_transmit_raw",
                    {"pulses": pulses_str},
                    blocking=True,
                )
            except Exception:
                _LOGGER.exception(
                    "Failed to send RF command via %s (channel %d, %s)",
                    self._node_name,
                    self._channel,
                    command,
                )
                return


class GumaxCCCover(CoverEntity):
    """Broadcast cover — sends CC command to all paired channels simultaneously."""

    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_is_closed = None
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._node_name: str = entry.data[CONF_ESPHOME_NODE]
        device_id_hex: str = entry.data[CONF_DEVICE_ID]
        self._device_id_bin = device_id_from_hex(device_id_hex)
        self._attr_unique_id = f"{DOMAIN}_{device_id_hex}_cc"
        self._attr_name = "CC"

    @property
    def device_info(self) -> DeviceInfo:
        device_id_hex: str = self._entry.data[CONF_DEVICE_ID]
        return DeviceInfo(
            identifiers={(DOMAIN, device_id_hex)},
            name=f"Gumax RF ({device_id_hex})",
            manufacturer="Gumax",
            model=f"{device_id_hex} (433.92 MHz)",
        )

    async def async_open_cover(self, **kwargs) -> None:
        await self._transmit("up")

    async def async_close_cover(self, **kwargs) -> None:
        await self._transmit("down")

    async def async_stop_cover(self, **kwargs) -> None:
        await self._transmit("stop")

    async def _transmit(self, command: str) -> None:
        pulses = encode_cc(command, self._device_id_bin)
        pulses_str = ",".join(str(p) for p in pulses)
        for _ in range(_REPEAT):
            try:
                await self.hass.services.async_call(
                    "esphome",
                    f"{self._node_name}_transmit_raw",
                    {"pulses": pulses_str},
                    blocking=True,
                )
            except Exception:
                _LOGGER.exception(
                    "Failed to send CC RF command via %s (%s)",
                    self._node_name,
                    command,
                )
                return
