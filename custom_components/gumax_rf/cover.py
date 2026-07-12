from __future__ import annotations

import logging

from homeassistant.components import logbook
from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from ._protocol import device_id_from_hex, encode, encode_cc
from .const import (
    CHANNELS,
    CONF_CHANNEL_PREFIX,
    CONF_DEVICE_ID,
    CONF_ESPHOME_NODE,
    DEFAULT_CHANNEL_PREFIX,
    DOMAIN,
)
from .helpers import device_info_for_entry, device_profile_for_entry

_LOGGER = logging.getLogger(__name__)

_REPEAT = 3
_COMMAND_LABEL: dict[str, str] = {"up": "opened", "down": "closed", "stop": "stopped"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[CoverEntity] = [GumaxCover(entry, ch) for ch in CHANNELS]
    entities.append(GumaxCCCover(entry))

    node_name: str = entry.data[CONF_ESPHOME_NODE]
    connectivity_id = f"binary_sensor.{node_name}_connectivity"

    state = hass.states.get(connectivity_id)
    if state is not None:
        initial_available = state.state == "on"
        for entity in entities:
            entity._attr_available = initial_available

    @callback
    def _connectivity_changed(event) -> None:
        new_state = event.data.get("new_state")
        available = new_state is not None and new_state.state == "on"
        for entity in entities:
            entity.set_available(available)

    entry.async_on_unload(
        async_track_state_change_event(hass, [connectivity_id], _connectivity_changed)
    )

    async_add_entities(entities)


class _GumaxCoverBase(CoverEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_is_closed = None
    _attr_translation_key = "gumax_cover"
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    @property
    def device_info(self):
        return device_info_for_entry(self._entry)

    async def async_added_to_hass(self) -> None:
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state == "closed":
                self._attr_is_closed = True
            elif last_state.state in ("open", "opening", "closing"):
                self._attr_is_closed = False

    @callback
    def set_available(self, available: bool) -> None:
        self._attr_available = available
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs) -> None:
        await self._transmit("up")

    async def async_close_cover(self, **kwargs) -> None:
        await self._transmit("down")

    async def async_stop_cover(self, **kwargs) -> None:
        await self._transmit("stop")

    def _build_pulses(self, command: str) -> list[int]:
        raise NotImplementedError

    async def _transmit(self, command: str) -> None:
        pulses = self._build_pulses(command)
        pulses_str = ",".join(str(p) for p in pulses)
        _LOGGER.debug(
            "Transmitting %s via esphome.%s_transmit_raw (%d pulses)",
            command,
            self._node_name,
            len(pulses),
        )
        for _ in range(_REPEAT):
            try:
                await self.hass.services.async_call(
                    "esphome",
                    f"{self._node_name}_transmit_raw",
                    {"pulses": pulses_str},
                    blocking=True,
                )
            except ServiceNotFound:
                esphome_services = self.hass.services.async_services().get("esphome", {})
                node_online = any(s.startswith(f"{self._node_name}_") for s in esphome_services)
                if node_online:
                    _LOGGER.error(
                        "ESPHome node '%s' is online but does not expose 'transmit_raw' — "
                        "check remote_transmitter configuration in ESPHome YAML; command not sent",
                        self._node_name,
                    )
                else:
                    _LOGGER.error(
                        "ESPHome node '%s' is offline — command not sent",
                        self._node_name,
                    )
                return
            except Exception:
                _LOGGER.exception(
                    "Failed to send RF command (%s) via esphome.%s_transmit_raw",
                    command,
                    self._node_name,
                )
                return
        if command == "up":
            self._attr_is_closed = False
        elif command == "down":
            self._attr_is_closed = True
        self.async_write_ha_state()
        if "logbook" in self.hass.config.components:
            logbook.async_log_entry(
                self.hass,
                name=self._attr_name,
                message=_COMMAND_LABEL.get(command, command),
                domain=DOMAIN,
                entity_id=self.entity_id,
            )


class GumaxCover(_GumaxCoverBase):
    def __init__(self, entry: ConfigEntry, channel: int) -> None:
        self._entry = entry
        self._channel = channel
        device_id_hex: str = entry.data[CONF_DEVICE_ID]
        self._device_id_bin = device_id_from_hex(device_id_hex)
        self._profile = device_profile_for_entry(entry)
        self._node_name: str = entry.data[CONF_ESPHOME_NODE]
        prefix = entry.options.get(CONF_CHANNEL_PREFIX, DEFAULT_CHANNEL_PREFIX)
        self._attr_unique_id = f"{DOMAIN}_{device_id_hex}_{self._node_name}_{channel}"
        self._attr_name = f"{prefix}{channel}"

    def _build_pulses(self, command: str) -> list[int]:
        return encode(self._channel, command, self._device_id_bin, self._profile)


class GumaxCCCover(_GumaxCoverBase):
    """Broadcast cover — sends CC command to all paired channels simultaneously."""

    _attr_name = "CC"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._node_name: str = entry.data[CONF_ESPHOME_NODE]
        device_id_hex: str = entry.data[CONF_DEVICE_ID]
        self._device_id_bin = device_id_from_hex(device_id_hex)
        self._profile = device_profile_for_entry(entry)
        self._attr_unique_id = f"{DOMAIN}_{device_id_hex}_{self._node_name}_cc"

    def _build_pulses(self, command: str) -> list[int]:
        return encode_cc(command, self._device_id_bin, self._profile)
