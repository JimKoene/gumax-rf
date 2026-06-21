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
from ._protocol import decode_device_id, decode_signal, device_id_from_hex, encode, encode_cc
from .const import (
    CONF_CHANNEL_PREFIX,
    CONF_DEVICE_ID,
    CONF_ESPHOME_NODE,
    DEFAULT_CHANNEL_PREFIX,
    DEFAULT_DEVICE_ID,
    DOMAIN,
    MAX_PREFIX_LENGTH,
    RF_CAPTURE_EVENT,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

_TRANSMIT_RAW_SUFFIX = "_transmit_raw"
_CAPTURE_TIMEOUT = 10.0
_POLL_INTERVAL = 1.0


def _validate_device_id(value: str) -> str:
    cleaned = value.strip().upper()
    if cleaned.startswith("0X"):
        cleaned = cleaned[2:]
    cleaned = cleaned or "0"
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
        self._pending_data: dict = {}
        self._pending_title: str = ""

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
                self._pending_data = user_input
                self._pending_title = f"Gumax RF ({user_input[CONF_DEVICE_ID]})"
                return await self.async_step_prefix()

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
                RF_CAPTURE_EVENT, self._on_rf_capture
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
                self._pending_data = {CONF_ESPHOME_NODE: self._selected_node, CONF_DEVICE_ID: device_id}
                self._pending_title = f"Gumax RF ({device_id})"
                return await self.async_step_prefix()

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
    # Prefix step (shared by manual and learn flows)
    # ------------------------------------------------------------------

    async def async_step_prefix(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            prefix = user_input.get(CONF_CHANNEL_PREFIX, "").strip()
            if not prefix:
                errors[CONF_CHANNEL_PREFIX] = "invalid_prefix"
            elif len(prefix) > MAX_PREFIX_LENGTH:
                errors[CONF_CHANNEL_PREFIX] = "invalid_prefix_too_long"
            else:
                return self.async_create_entry(
                    title=self._pending_title,
                    data=self._pending_data,
                    options={CONF_CHANNEL_PREFIX: prefix},
                )

        current = (
            user_input.get(CONF_CHANNEL_PREFIX, DEFAULT_CHANNEL_PREFIX).strip()
            if user_input and errors
            else DEFAULT_CHANNEL_PREFIX
        )
        schema = vol.Schema(
            {vol.Required(CONF_CHANNEL_PREFIX, default=DEFAULT_CHANNEL_PREFIX): str}
        )
        return self.async_show_form(
            step_id="prefix",
            data_schema=schema,
            errors=errors,
            description_placeholders={"example": f"{current}1"},
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
    """Options flow — configure channel prefix and view raw pulse timings."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__(config_entry)
        self._selected_channel: str = "1"
        self._unsub_capture: Callable[[], None] | None = None
        self._capture_task_opt: asyncio.Task | None = None
        self._capture_start_opt: float | None = None
        self._last_raw: str | None = None
        self._last_signal: dict | None = None
        self._capture_error: str | None = None

    # ------------------------------------------------------------------
    # Step 1: menu
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["configure_prefix", "view_codes", "capture_signal"],
        )

    # ------------------------------------------------------------------
    # Step 2a: configure channel prefix
    # ------------------------------------------------------------------

    async def async_step_configure_prefix(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        current_prefix = self.options.get(CONF_CHANNEL_PREFIX, DEFAULT_CHANNEL_PREFIX)

        if user_input is not None:
            prefix = user_input.get(CONF_CHANNEL_PREFIX, "").strip()
            if not prefix:
                errors[CONF_CHANNEL_PREFIX] = "invalid_prefix"
            elif len(prefix) > MAX_PREFIX_LENGTH:
                errors[CONF_CHANNEL_PREFIX] = "invalid_prefix_too_long"
            else:
                return self.async_create_entry(
                    data={**self.options, CONF_CHANNEL_PREFIX: prefix}
                )

        example_prefix = (
            user_input.get(CONF_CHANNEL_PREFIX, current_prefix).strip()
            if user_input and errors
            else current_prefix
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_CHANNEL_PREFIX, default=current_prefix): str,
            }
        )
        return self.async_show_form(
            step_id="configure_prefix",
            data_schema=schema,
            errors=errors,
            description_placeholders={"example": f"{example_prefix}1"},
        )

    # ------------------------------------------------------------------
    # Step 2b: pick channel to view pulse codes
    # ------------------------------------------------------------------

    async def async_step_view_codes(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._selected_channel = user_input["channel"]
            return await self.async_step_show_code()

        prefix = self.options.get(CONF_CHANNEL_PREFIX, DEFAULT_CHANNEL_PREFIX)
        channel_options = [
            {"value": str(i), "label": f"{prefix}{i}"} for i in range(1, 17)
        ] + [{"value": "CC", "label": "CC"}]
        schema = vol.Schema(
            {
                vol.Required("channel", default=self._selected_channel): SelectSelector(
                    SelectSelectorConfig(
                        options=channel_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="view_codes", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 2c / 3 / 4: capture and analyse a live signal
    # ------------------------------------------------------------------

    async def async_step_capture_signal(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._capture_error:
            errors["base"] = self._capture_error
            self._capture_error = None

        if user_input is not None:
            self._last_raw = None
            self._last_signal = None
            self._capture_start_opt = None
            self._cleanup_capture_listener()
            self._unsub_capture = self.hass.bus.async_listen(
                RF_CAPTURE_EVENT, self._on_signal_capture
            )
            return await self.async_step_capture_signal_wait()

        return self.async_show_form(
            step_id="capture_signal",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_capture_signal_wait(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        if self._capture_start_opt is None:
            self._capture_start_opt = time.monotonic()

        if self._last_raw is not None:
            return self.async_show_progress_done(next_step_id="capture_signal_result")

        elapsed = time.monotonic() - self._capture_start_opt
        remaining = max(0.0, _CAPTURE_TIMEOUT - elapsed)

        if remaining > 0:
            self._capture_task_opt = self.hass.async_create_background_task(
                asyncio.sleep(min(_POLL_INTERVAL, remaining)),
                "gumax_rf_signal_capture_poll",
            )
            return self.async_show_progress(
                step_id="capture_signal_wait",
                progress_action="capture_signal_wait",
                progress_task=self._capture_task_opt,
                description_placeholders={"remaining": str(int(remaining))},
            )

        self._cleanup_capture_listener()
        self._capture_start_opt = None
        self._capture_error = "no_signal_received"
        return self.async_show_progress_done(next_step_id="capture_signal")

    async def async_step_capture_signal_result(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_capture_signal()

        sig = self._last_signal or {}
        device_id = sig.get("device_id") or "?"
        channel_raw = sig.get("channel")
        command_raw = sig.get("command")

        prefix = self.options.get(CONF_CHANNEL_PREFIX, DEFAULT_CHANNEL_PREFIX)
        if channel_raw == "CC":
            channel_label = "CC"
        elif channel_raw is not None:
            channel_label = f"{prefix}{channel_raw}"
        else:
            channel_label = "?"

        checksum = sig.get("checksum")
        checksum_valid = sig.get("checksum_valid")
        if checksum is not None:
            checksum_label = f"0x{checksum:02X}"
            checksum_valid_label = "✓" if checksum_valid else "✗"
        else:
            checksum_label = "?"
            checksum_valid_label = "?"

        encode_match = sig.get("encode_match")
        encode_match_label = "✓" if encode_match else ("✗" if encode_match is not None else "?")

        return self.async_show_form(
            step_id="capture_signal_result",
            data_schema=vol.Schema({}),
            last_step=False,
            description_placeholders={
                "raw": self._last_raw or "–",
                "device_id": device_id,
                "channel": channel_label,
                "command": command_raw or "?",
                "checksum": checksum_label,
                "checksum_valid": checksum_valid_label,
                "encode_match": encode_match_label,
            },
        )

    @callback
    def _on_signal_capture(self, event) -> None:
        if self._last_raw is not None:
            return
        pulses_str: str = event.data.get("pulses", "")
        if not pulses_str:
            return
        try:
            pulses = [int(x) for x in pulses_str.split(",") if x.strip()]
        except ValueError:
            return
        signal = decode_signal(pulses)
        if signal is None:
            return
        self._last_raw = pulses_str
        self._last_signal = signal
        self._cleanup_capture_listener()

    def _cleanup_capture_listener(self) -> None:
        if self._unsub_capture is not None:
            self._unsub_capture()
            self._unsub_capture = None

    def async_remove(self) -> None:
        self._cleanup_capture_listener()
        if self._capture_task_opt and not self._capture_task_opt.done():
            self._capture_task_opt.cancel()

    # ------------------------------------------------------------------
    # Step 3: display all three commands — "Next" returns to step 2b
    # ------------------------------------------------------------------

    async def async_step_show_code(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_view_codes()

        device_id_hex: str = self.config_entry.data[CONF_DEVICE_ID]
        device_id_bin = device_id_from_hex(device_id_hex)
        prefix = self.options.get(CONF_CHANNEL_PREFIX, DEFAULT_CHANNEL_PREFIX)

        if self._selected_channel == "CC":
            pulses_up = encode_cc("up", device_id_bin)
            pulses_down = encode_cc("down", device_id_bin)
            pulses_stop = encode_cc("stop", device_id_bin)
            channel_label = "CC"
        else:
            channel_num = int(self._selected_channel)
            pulses_up = encode(channel_num, "up", device_id_bin)
            pulses_down = encode(channel_num, "down", device_id_bin)
            pulses_stop = encode(channel_num, "stop", device_id_bin)
            channel_label = f"{prefix}{channel_num}"

        return self.async_show_form(
            step_id="show_code",
            data_schema=vol.Schema({}),
            last_step=False,
            description_placeholders={
                "channel": channel_label,
                "code_up": ", ".join(str(p) for p in pulses_up),
                "code_down": ", ".join(str(p) for p in pulses_down),
                "code_stop": ", ".join(str(p) for p in pulses_stop),
            },
        )
