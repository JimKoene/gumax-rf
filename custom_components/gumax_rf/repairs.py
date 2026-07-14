"""One-click fix for the 'legacy_checksum' repair issue.

Runs the same capture_k1/k2/k9 calibration steps as the config flow's
reconfigure path (via CalibrationFlowMixin), directly against the affected
config entry, so the user doesn't have to separately find Reconfigure.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from ._calibration_flow import CalibrationFlowMixin
from .const import CONF_DEVICE_ID


class GumaxRfLegacyChecksumRepairFlow(CalibrationFlowMixin, RepairsFlow):
    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._device_id: str = entry.data[CONF_DEVICE_ID]
        self._init_calibration_state()

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        # Fixable issues can't have a top-level "description" in strings.json
        # (mutually exclusive with fix_flow in HA's translation schema), so
        # the explanation that used to live there is shown here instead.
        if user_input is not None:
            return await self.async_step_capture_k1()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({}),
            description_placeholders={"title": self._entry.title},
        )

    def async_remove(self) -> None:
        self._cleanup_calib_listener()
        if self._calib_task and not self._calib_task.done():
            self._calib_task.cancel()

    def _extra_share_placeholders(self) -> dict[str, str]:
        return {"title": self._entry.title}

    async def _finish_calibration(self, calibration: dict) -> FlowResult:
        # The entry's update listener (registered in __init__.py) reloads it
        # automatically on any data change — no explicit reload needed here,
        # same as e.g. the options flow's configure_prefix step.
        self.hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, **calibration}
        )
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    entry_id = (data or {}).get("entry_id")
    entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
    if entry is None:
        raise ValueError(f"No config entry found for repair issue {issue_id}")
    return GumaxRfLegacyChecksumRepairFlow(entry)
