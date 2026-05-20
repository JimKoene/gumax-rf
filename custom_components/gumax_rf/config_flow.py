from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from ._protocol import decode_device_id, device_id_from_hex, encode, encode_cc
from .const import (
    CONF_DEVICE_ID,
    CONF_ESPHOME_NODE,
    DEFAULT_DEVICE_ID,
    DOMAIN,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

_TRANSMIT_RAW_SUFFIX = "_transmit_raw"
_RF_CAPTURE_EVENT = "esphome.gumax_rf_capture"
_CAPTURE_TIMEOUT = 10.0
_POLL_INTERVAL = 1.0


def _validate_device_id(value: str) -> str:
    cleaned = value.strip().upper().lstrip("0X") or "0"
    if len(cleaned) > 8:
        raise vol.Invalid("invalid_device_id")
    try:
        int(cleaned, 16)
    except ValueError:
        raise vol.Invalid("invalid_device_id")
    return cleaned.zfill(8)


def _get_rf_capable_nodes(hass: HomeAssistant) -> list[str]:
    """Return ESPHome node names that expose a transmit_raw action."""
    services = hass.services.async_services_for_domain("esphome")
    return sorted(
        name[: -len(_TRANSMIT_RAW_SUFFIX)]
        for name in services
        if name.endswith(_TRANSMIT_RAW_SUFFIX)
    )


def _node_schema_entry(hass: HomeAssistant, current: str = "") -> dict:
    """Return {vol.Required: selector} for the ESPHome node field."""
    rf_nodes = _get_rf_capable_nodes(hass)

    default = current or (rf_nodes[0] if rf_nodes else None)
    key = (
        vol.Required(CONF_ESPHOME_NODE, default=default)
        if default
        else vol.Required(CONF_ESPHOME_NODE)
    )

    if rf_nodes:
        field = SelectSelector(
            SelectSelectorConfig(
                options=rf_nodes,
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
    else:
        field = str

    return {key: field}


class GumaxRfConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "GumaxRfOptionsFlow":
        return GumaxRfOptionsFlow(config_entry)

    def __init__(self) -> None:
        self._unsub_rf: Callable[[], None] | None = None
        self._selected_node: str = ""
        self._learn_error: str | None = None
        self._capture_task: asyncio.Task | None = None
        self._captured_ids: dict[str, int] = {}
        self._capture_start: float | None = None

    # ------------------------------------------------------------------
    # Entry point — choose setup method
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="user",
            menu_options=["manual", "learn"],
        )

    # ------------------------------------------------------------------
    # Manual entry
    # ------------------------------------------------------------------

    async def async_step_manual(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                user_input[CONF_DEVICE_ID] = _validate_device_id(
                    user_input[CONF_DEVICE_ID]
                )
            except vol.Invalid as exc:
                errors[CONF_DEVICE_ID] = str(exc)
            else:
                user_input[CONF_ESPHOME_NODE] = (
                    user_input[CONF_ESPHOME_NODE].strip().replace("-", "_")
                )
                await self.async_set_unique_id(user_input[CONF_DEVICE_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Gumax RF ({user_input[CONF_DEVICE_ID]})",
                    data=user_input,
                )

        schema = vol.Schema(
            {
                **_node_schema_entry(self.hass),
                vol.Required(CONF_DEVICE_ID, default=DEFAULT_DEVICE_ID): str,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------
    # Learn — step 1: select node
    # ------------------------------------------------------------------

    async def async_step_learn(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._learn_error:
            errors["base"] = self._learn_error
            self._learn_error = None

        if user_input is not None:
            self._selected_node = user_input[CONF_ESPHOME_NODE].strip().replace("-", "_")
            self._captured_ids = {}
            self._capture_start = None
            self._cleanup_listener()
            self._unsub_rf = self.hass.bus.async_listen(
                _RF_CAPTURE_EVENT, self._on_rf_capture
            )
            return await self.async_step_learn_wait()

        if not _get_rf_capable_nodes(self.hass):
            return self.async_abort(reason="no_esphome_nodes")

        schema = vol.Schema(
            {
                **_node_schema_entry(self.hass, current=self._selected_node),
            }
        )
        return self.async_show_form(step_id="learn", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------
    # Learn — step 2: polling progress with live log
    # ------------------------------------------------------------------

    async def async_step_learn_wait(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        if self._capture_start is None:
            self._capture_start = time.monotonic()

        elapsed = time.monotonic() - self._capture_start
        remaining = max(0.0, _CAPTURE_TIMEOUT - elapsed)

        if remaining > 0:
            self._capture_task = self.hass.async_create_background_task(
                asyncio.sleep(min(_POLL_INTERVAL, remaining)),
                "gumax_rf_capture_poll",
            )
            return self.async_show_progress(
                step_id="learn_wait",
                progress_action="learn_wait",
                progress_task=self._capture_task,
                description_placeholders={
                    "log": self._format_capture_log(),
                    "remaining": str(int(remaining)),
                },
            )

        # Time is up
        self._cleanup_listener()
        self._capture_start = None

        if not self._captured_ids:
            self._learn_error = "no_signal_received"
            return self.async_show_progress_done(next_step_id="learn")

        return self.async_show_progress_done(next_step_id="learn_result")

    # ------------------------------------------------------------------
    # Learn — step 3: select or edit captured device ID
    # ------------------------------------------------------------------

    async def async_step_learn_result(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                device_id = _validate_device_id(user_input[CONF_DEVICE_ID])
            except vol.Invalid as exc:
                errors[CONF_DEVICE_ID] = str(exc)
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Gumax RF ({device_id})",
                    data={CONF_ESPHOME_NODE: self._selected_node, CONF_DEVICE_ID: device_id},
                )

        options = [
            {"value": did, "label": f"{did} ({count}×)"}
            for did, count in sorted(self._captured_ids.items(), key=lambda x: -x[1])
        ]
        default = options[0]["value"]
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID, default=default): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="learn_result",
            data_schema=schema,
            errors=errors,
            description_placeholders={"log": self._format_capture_log()},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_capture_log(self) -> str:
        if not self._captured_ids:
            return "–"
        return "\n".join(
            f"- {did} ({count}×)"
            for did, count in sorted(self._captured_ids.items(), key=lambda x: -x[1])
        )

    @callback
    def _on_rf_capture(self, event) -> None:
        pulses_str: str = event.data.get("pulses", "")
        if not pulses_str:
            return
        try:
            pulses = [int(x) for x in pulses_str.split(",") if x.strip()]
        except ValueError:
            return
        device_id = decode_device_id(pulses)
        if device_id:
            self._captured_ids[device_id] = self._captured_ids.get(device_id, 0) + 1

    def _cleanup_listener(self) -> None:
        if self._unsub_rf is not None:
            self._unsub_rf()
            self._unsub_rf = None

    def async_remove(self) -> None:
        self._cleanup_listener()
        if self._capture_task and not self._capture_task.done():
            self._capture_task.cancel()


class GumaxRfOptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    """Options flow — view raw pulse timings per channel/command."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__(config_entry)
        self._channel: str = "K1"

    # ------------------------------------------------------------------
    # Step 1: pick channel
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._channel = user_input["channel"]
            return await self.async_step_show_code()

        channel_options = [f"K{i}" for i in range(1, 17)] + ["CC"]
        schema = vol.Schema(
            {
                vol.Required("channel", default=self._channel): SelectSelector(
                    SelectSelectorConfig(
                        options=channel_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 2: display all three commands — "Next" returns to step 1
    # ------------------------------------------------------------------

    async def async_step_show_code(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_init()

        device_id_hex: str = self.config_entry.data[CONF_DEVICE_ID]
        device_id_bin = device_id_from_hex(device_id_hex)
        if self._channel == "CC":
            pulses_up = encode_cc("up", device_id_bin)
            pulses_down = encode_cc("down", device_id_bin)
            pulses_stop = encode_cc("stop", device_id_bin)
        else:
            channel_num = int(self._channel[1:])
            pulses_up = encode(channel_num, "up", device_id_bin)
            pulses_down = encode(channel_num, "down", device_id_bin)
            pulses_stop = encode(channel_num, "stop", device_id_bin)

        return self.async_show_form(
            step_id="show_code",
            data_schema=vol.Schema({}),
            last_step=False,
            description_placeholders={
                "channel": self._channel,
                "code_up": ", ".join(str(p) for p in pulses_up),
                "code_down": ", ".join(str(p) for p in pulses_down),
                "code_stop": ", ".join(str(p) for p in pulses_stop),
            },
        )
