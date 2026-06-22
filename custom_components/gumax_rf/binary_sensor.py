from __future__ import annotations

import logging

from homeassistant.components import logbook
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_DEVICE_ID, CONF_ESPHOME_NODE, DOMAIN
from .helpers import device_info_for_entry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    node_name: str = entry.data[CONF_ESPHOME_NODE]
    connectivity_id = f"binary_sensor.{node_name}_connectivity"
    if er.async_get(hass).async_get(connectivity_id) is not None:
        async_add_entities([GumaxBridgeSensor(entry)])


class GumaxBridgeSensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "ESPHome Status"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._node_name: str = entry.data[CONF_ESPHOME_NODE]
        device_id_hex: str = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{DOMAIN}_{device_id_hex}_bridge_status"

    @property
    def device_info(self):
        return device_info_for_entry(self._entry)

    async def async_added_to_hass(self) -> None:
        connectivity_id = f"binary_sensor.{self._node_name}_connectivity"

        @callback
        def _apply_state() -> None:
            state = self.hass.states.get(connectivity_id)
            if state is not None:
                self._attr_is_on = state.state == "on"
                self.async_write_ha_state()

        _apply_state()

        if self._attr_is_on is None:
            @callback
            def _on_ha_started(_event=None) -> None:
                _apply_state()

            if self.hass.is_running:
                _on_ha_started()
            else:
                self.async_on_remove(
                    self.hass.bus.async_listen_once(
                        EVENT_HOMEASSISTANT_STARTED, _on_ha_started
                    )
                )

        @callback
        def _connectivity_changed(event) -> None:
            new_state = event.data.get("new_state")
            available = new_state is not None and new_state.state == "on"
            self._attr_is_on = available
            self.async_write_ha_state()

            if available:
                _LOGGER.info("ESPHome node '%s' is back online", self._node_name)
                message = "ESPHome online"
            else:
                _LOGGER.warning("ESPHome node '%s' is offline", self._node_name)
                message = "ESPHome offline"

            if "logbook" in self.hass.config.components:
                logbook.async_log_entry(
                    self.hass,
                    name="Gumax RF",
                    message=message,
                    domain=DOMAIN,
                    entity_id=self.entity_id,
                )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [connectivity_id], _connectivity_changed
            )
        )
