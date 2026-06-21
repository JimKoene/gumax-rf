from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    EVENT_HOMEASSISTANT_STARTED,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_DEVICE_ID, CONF_ESPHOME_NODE, DOMAIN
from .helpers import device_info_for_entry

_LOGGER = logging.getLogger(__name__)


@dataclass
class _MirrorConfig:
    name: str
    id_suffix: str
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    icon: str | None = None


_ESPHOME_MIRRORS: list[_MirrorConfig] = [
    _MirrorConfig("IP Address", "ip_address", icon="mdi:ip-network"),
    _MirrorConfig(
        "Uptime",
        "uptime",
        unit=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:timer-outline",
    ),
    _MirrorConfig(
        "Wi-Fi Signal",
        "wi_fi_signal",
        unit=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:wifi",
    ),
    _MirrorConfig("Wi-Fi BSSID", "wi_fi_bssid", icon="mdi:router-wireless"),
    _MirrorConfig("Wi-Fi SSID", "wi_fi_ssid", icon="mdi:wifi-settings"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    node_name: str = entry.data[CONF_ESPHOME_NODE]
    async_add_entities([
        GumaxNodeSensor(entry),
        GumaxDeviceIdSensor(entry),
        *[
            GumaxMirrorSensor(entry, config, f"sensor.{node_name}_{config.id_suffix}")
            for config in _ESPHOME_MIRRORS
        ],
    ])


class GumaxNodeSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "ESPHome Node"
    _attr_icon = "mdi:chip"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        device_id_hex: str = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{DOMAIN}_{device_id_hex}_esphome_node"
        self._attr_native_value = entry.data[CONF_ESPHOME_NODE]

    @property
    def device_info(self):
        return device_info_for_entry(self._entry)


class GumaxDeviceIdSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Device ID"
    _attr_icon = "mdi:identifier"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        device_id_hex: str = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{DOMAIN}_{device_id_hex}_device_id"
        self._attr_native_value = device_id_hex

    @property
    def device_info(self):
        return device_info_for_entry(self._entry)


class GumaxMirrorSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, entry: ConfigEntry, config: _MirrorConfig, source_id: str
    ) -> None:
        self._entry = entry
        self._source_id = source_id
        self._numeric = config.state_class is not None
        device_id_hex: str = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{DOMAIN}_{device_id_hex}_{config.id_suffix}"
        self._attr_name = config.name
        self._attr_native_unit_of_measurement = config.unit
        self._attr_device_class = config.device_class
        self._attr_state_class = config.state_class
        self._attr_icon = config.icon

    @property
    def device_info(self):
        return device_info_for_entry(self._entry)

    async def async_added_to_hass(self) -> None:
        node_name: str = self._entry.data[CONF_ESPHOME_NODE]
        connectivity_id = f"binary_sensor.{node_name}_connectivity"

        @callback
        def _apply_source(state_str: str | None) -> None:
            if state_str is None or state_str in ("unavailable", "unknown"):
                self._attr_available = False
                self._attr_native_value = None
            else:
                self._attr_available = True
                if self._numeric:
                    try:
                        self._attr_native_value = float(state_str)
                    except ValueError:
                        self._attr_native_value = None
                else:
                    self._attr_native_value = state_str

        @callback
        def _apply_all() -> None:
            conn_state = self.hass.states.get(connectivity_id)
            if conn_state is not None:
                self._attr_available = conn_state.state == "on"
            if self._attr_available is not False:
                src_state = self.hass.states.get(self._source_id)
                _apply_source(src_state.state if src_state else None)
            self.async_write_ha_state()

        _apply_all()

        if self._attr_native_value is None and not self.hass.is_running:
            self.async_on_remove(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, lambda _: _apply_all()
                )
            )

        @callback
        def _connectivity_changed(event) -> None:
            new_state = event.data.get("new_state")
            self._attr_available = new_state is not None and new_state.state == "on"
            if not self._attr_available:
                self._attr_native_value = None
            self.async_write_ha_state()

        @callback
        def _source_changed(event) -> None:
            new_state = event.data.get("new_state")
            _apply_source(new_state.state if new_state else None)
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(self.hass, [connectivity_id], _connectivity_changed)
        )
        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._source_id], _source_changed)
        )
