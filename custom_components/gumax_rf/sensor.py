from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_entries_for_device, async_get as async_get_entity_registry
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_DEVICE_ID, CONF_ESPHOME_NODE, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class _MirrorConfig:
    name: str
    id_suffix: str
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None


_ESPHOME_MIRRORS: list[_MirrorConfig] = [
    _MirrorConfig("IP Address", "ip_address"),
    _MirrorConfig(
        "Uptime",
        "uptime",
        unit=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    _MirrorConfig(
        "Wi-Fi Signal",
        "wi_fi_signal",
        unit=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _MirrorConfig("Wi-Fi BSSID", "wi_fi_bssid"),
    _MirrorConfig("Wi-Fi SSID", "wi_fi_ssid"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    node_name: str = entry.data[CONF_ESPHOME_NODE]

    entities: list[SensorEntity] = [
        GumaxNodeSensor(entry),
        GumaxDeviceIdSensor(entry),
    ]

    ent_reg = async_get_entity_registry(hass)
    connectivity_entry = ent_reg.async_get(f"binary_sensor.{node_name}_connectivity")
    if connectivity_entry and connectivity_entry.device_id:
        esphome_ids = {
            e.entity_id
            for e in async_entries_for_device(ent_reg, connectivity_entry.device_id)
            if e.platform == "esphome"
        }
        for config in _ESPHOME_MIRRORS:
            source_id = next(
                (eid for eid in esphome_ids if eid.endswith(f"_{config.id_suffix}")),
                None,
            )
            if source_id:
                entities.append(GumaxMirrorSensor(entry, config, source_id))
            else:
                _LOGGER.debug(
                    "ESPHome entity with suffix '%s' not found for node '%s'",
                    config.id_suffix,
                    node_name,
                )
    else:
        _LOGGER.debug(
            "ESPHome device not found for node '%s' — diagnostic mirror sensors skipped",
            node_name,
        )

    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    device_id_hex: str = entry.data[CONF_DEVICE_ID]
    return DeviceInfo(
        identifiers={(DOMAIN, device_id_hex)},
        name=f"Gumax RF ({device_id_hex})",
        manufacturer="Gumax",
        model=f"{device_id_hex} (433.92 MHz)",
    )


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
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry)


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
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry)


class GumaxMirrorSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, entry: ConfigEntry, config: _MirrorConfig, source_id: str
    ) -> None:
        self._entry = entry
        self._source_id = source_id
        device_id_hex: str = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{DOMAIN}_{device_id_hex}_{config.id_suffix}"
        self._attr_name = config.name
        self._attr_native_unit_of_measurement = config.unit
        self._attr_device_class = config.device_class
        self._attr_state_class = config.state_class

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._entry)

    async def async_added_to_hass(self) -> None:
        state = self.hass.states.get(self._source_id)
        if state is not None:
            self._attr_native_value = state.state

        @callback
        def _source_changed(event) -> None:
            new_state = event.data.get("new_state")
            self._attr_native_value = new_state.state if new_state else None
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._source_id], _source_changed)
        )
