"""Shared capture_k1/k2/k9 -> x_dev/b9 calibration steps.

Used by both GumaxRfConfigFlow (new entry / reconfigure, in config_flow.py)
and GumaxRfLegacyChecksumRepairFlow (one-click repair fix, in repairs.py) so
the actual capture/validate/compute logic exists exactly once.

A concrete class using this mixin must, before the first capture_k1 step
runs: set self.hass, self._device_id (the remote's decoded hex device_id,
used to reject captures from a different remote), and call
self._init_calibration_state(). It must also implement
async def _finish_calibration(self, calibration: dict) -> FlowResult,
called once all three channels are captured and calibration is computed —
calibration has CONF_X_DEV/CONF_K1_EXTRA/CONF_K9_EXTRA/CONF_B9_DEFAULT/
CONF_B9_K1/CONF_B9_K9 keys, ready to merge into a config entry's data.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.core import callback

from ._protocol import DeviceProfile, channel_bytes, decode_signal, device_id_from_hex, encode, infer_x_dev
from .const import (
    CONF_B9_DEFAULT,
    CONF_B9_K1,
    CONF_B9_K9,
    CONF_K1_EXTRA,
    CONF_K9_EXTRA,
    CONF_X_DEV,
    RF_CAPTURE_EVENT,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_CAPTURE_TIMEOUT = 10.0
_POLL_INTERVAL = 1.0

# K2 gives x_dev directly (a "normal" channel with no known correction); K1
# and K9 are captured separately because some remotes add a small extra
# amount on top of x_dev for those two channels specifically.
_CALIB_ORDER: tuple[str, ...] = ("capture_k1", "capture_k2", "capture_k9")
_CALIB_CHANNELS: dict[str, int] = {"capture_k1": 1, "capture_k2": 2, "capture_k9": 9}

# Passed as description placeholders rather than embedded in translation
# strings — HACS's translation validator rejects literal URLs in strings.json.
_DISCUSSION_URL = "https://github.com/JimKoene/gumax-rf/discussions/5"
_ISSUE_URL = "https://github.com/JimKoene/gumax-rf/issues/new"


class CalibrationFlowMixin:
    def _init_calibration_state(self) -> None:
        self._calib_channel: int = 0
        self._calib_signal: dict | None = None
        self._calib_signal_raw: str | None = None
        self._calib_start: float | None = None
        self._calib_task: asyncio.Task | None = None
        self._unsub_calib: "Callable[[], None] | None" = None
        self._calib_error: str | None = None
        self._calib_data: dict[int, dict] = {}
        self._calib_raw: dict[int, str] = {}
        self._calib_result: dict = {}
        self._calib_verify: dict[int, bool] = {}

    # ------------------------------------------------------------------
    # capture_k1 / capture_k2 / capture_k9 — thin wrappers so HA's
    # step-id-based dispatch (async_step_<id>) can find each one.
    # ------------------------------------------------------------------

    async def async_step_capture_k1(self, user_input: dict | None = None):
        return await self._begin_capture("capture_k1", user_input)

    async def async_step_capture_k1_wait(self, user_input: dict | None = None):
        return await self._await_capture("capture_k1_wait")

    async def async_step_capture_k2(self, user_input: dict | None = None):
        return await self._begin_capture("capture_k2", user_input)

    async def async_step_capture_k2_wait(self, user_input: dict | None = None):
        return await self._await_capture("capture_k2_wait")

    async def async_step_capture_k9(self, user_input: dict | None = None):
        return await self._begin_capture("capture_k9", user_input)

    async def async_step_capture_k9_wait(self, user_input: dict | None = None):
        return await self._await_capture("capture_k9_wait")

    async def _begin_capture(self, step_id: str, user_input: dict | None):
        errors: dict[str, str] = {}
        if self._calib_error:
            errors["base"] = self._calib_error
            self._calib_error = None

        if user_input is not None:
            self._calib_signal = None
            self._calib_signal_raw = None
            self._calib_start = None
            self._calib_channel = _CALIB_CHANNELS[step_id]
            self._cleanup_calib_listener()
            self._unsub_calib = self.hass.bus.async_listen(
                RF_CAPTURE_EVENT, self._on_calib_capture
            )
            return await getattr(self, f"async_step_{step_id}_wait")()

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"progress": self._format_calib_progress()},
        )

    async def _await_capture(self, step_id: str):
        if self._calib_start is None:
            self._calib_start = time.monotonic()

        if self._calib_signal is not None:
            self._calib_data[self._calib_channel] = self._calib_signal
            self._calib_raw[self._calib_channel] = self._calib_signal_raw or ""
            self._calib_signal = None
            self._calib_signal_raw = None
            self._calib_start = None
            self._cleanup_calib_listener()
            idx = _CALIB_ORDER.index(step_id.removesuffix("_wait"))
            next_step_id = (
                _CALIB_ORDER[idx + 1] if idx + 1 < len(_CALIB_ORDER) else "calibration_done"
            )
            return self.async_show_progress_done(next_step_id=next_step_id)

        elapsed = time.monotonic() - self._calib_start
        remaining = max(0.0, _CAPTURE_TIMEOUT - elapsed)

        if remaining > 0:
            self._calib_task = self.hass.async_create_background_task(
                asyncio.sleep(min(_POLL_INTERVAL, remaining)),
                "gumax_rf_calib_poll",
            )
            return self.async_show_progress(
                step_id=step_id,
                progress_action=step_id,
                progress_task=self._calib_task,
                description_placeholders={"remaining": str(int(remaining))},
            )

        self._cleanup_calib_listener()
        self._calib_start = None
        self._calib_error = "no_signal_received"
        return self.async_show_progress_done(next_step_id=step_id.removesuffix("_wait"))

    # ------------------------------------------------------------------
    # calibration_done — compute x_dev/k1_extra/k9_extra/b9 from the three
    # captures; calibration_share — show a copy-friendly block and let the
    # user optionally share it, then hand off to _finish_calibration().
    # ------------------------------------------------------------------

    async def async_step_calibration_done(self, user_input: dict | None = None):
        k1, k2, k9 = self._calib_data[1], self._calib_data[2], self._calib_data[9]

        b5, b6, b7 = channel_bytes(2, k2["command"])
        x_dev = infer_x_dev(b5 + b6 + b7, k2["checksum"])
        b5, b6, b7 = channel_bytes(1, k1["command"])
        k1_extra = infer_x_dev(b5 + b6 + b7, k1["checksum"], hint=x_dev) - x_dev
        b5, b6, b7 = channel_bytes(9, k9["command"])
        k9_extra = infer_x_dev(b5 + b6 + b7, k9["checksum"], hint=x_dev) - x_dev

        self._calib_result = {
            CONF_X_DEV: x_dev,
            CONF_K1_EXTRA: k1_extra,
            CONF_K9_EXTRA: k9_extra,
            CONF_B9_DEFAULT: k2["b9"],
            CONF_B9_K1: k1["b9"],
            CONF_B9_K9: k9["b9"],
        }

        # Sanity check: re-encode each captured channel with the calibration
        # we just derived and confirm it reproduces the same checksum/b9 that
        # was actually captured. By construction this should always match —
        # infer_x_dev() and encode() are exact inverses of each other — so a
        # mismatch here means a bug in this integration's code, not in the
        # remote or the capture. (It can't catch the K1/K9-ambiguity case
        # discussed for infer_x_dev, since any candidate it returns already
        # satisfies this same equation by definition.)
        verify_profile = DeviceProfile(**self._calib_result)
        device_id_bin = device_id_from_hex(self._device_id)
        self._calib_verify = {}
        for channel in (1, 2, 9):
            sig = self._calib_data[channel]
            ref_pulses = encode(channel, sig["command"], device_id_bin, verify_profile)
            ref_signal = decode_signal(ref_pulses)
            self._calib_verify[channel] = (
                ref_signal is not None
                and ref_signal["checksum"] == sig["checksum"]
                and ref_signal["b9"] == sig["b9"]
            )

        return await self.async_step_calibration_share()

    async def async_step_calibration_share(self, user_input: dict | None = None):
        if user_input is not None:
            return await self._finish_calibration(self._calib_result)

        if not all(self._calib_verify.values()):
            return await self.async_step_calibration_mismatch()

        return self.async_show_form(
            step_id="calibration_share",
            data_schema=vol.Schema({}),
            last_step=False,
            description_placeholders={
                "verify_block": self._format_verify_block(),
                "share_block": self._format_share_block(),
                "discussion_url": _DISCUSSION_URL,
                **self._extra_share_placeholders(),
            },
        )

    async def async_step_calibration_mismatch(self, user_input: dict | None = None):
        if user_input is not None:
            return await self._finish_calibration(self._calib_result)

        return self.async_show_form(
            step_id="calibration_mismatch",
            data_schema=vol.Schema({}),
            last_step=False,
            description_placeholders={
                "verify_block": self._format_verify_block(),
                "share_block": self._format_share_block(),
                "issue_url": _ISSUE_URL,
                **self._extra_share_placeholders(),
            },
        )

    async def _finish_calibration(self, calibration: dict):
        raise NotImplementedError

    def _extra_share_placeholders(self) -> dict[str, str]:
        """Override to add flow-specific placeholders (e.g. entry title) to
        the calibration_share/calibration_mismatch description templates."""
        return {}

    def _format_verify_block(self) -> str:
        labels = (("K1", 1), ("K2", 2), ("K9", 9))
        return "\n".join(
            f"- {'✅' if self._calib_verify.get(ch) else '❌'} {label} checksum"
            for label, ch in labels
        )

    def _format_share_block(self) -> str:
        parts = [f"Device ID: {self._device_id}"]
        for label, channel in (("K1", 1), ("K2", 2), ("K9", 9)):
            sig = self._calib_data.get(channel, {})
            raw = self._calib_raw.get(channel, "")
            parts.append(f"\n{label} ({sig.get('command', '?')}):\n{raw}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @callback
    def _on_calib_capture(self, event) -> None:
        if self._calib_signal is not None:
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
        if signal["channel"] != self._calib_channel or signal["device_id"] != self._device_id:
            return  # wrong button/remote — keep waiting within the timeout
        self._calib_signal = signal
        self._calib_signal_raw = pulses_str
        self._cleanup_calib_listener()

    def _cleanup_calib_listener(self) -> None:
        if self._unsub_calib is not None:
            self._unsub_calib()
            self._unsub_calib = None

    def _format_calib_progress(self) -> str:
        labels = {1: "K1", 2: "K2", 9: "K9"}
        return "\n".join(
            f"- {'✅' if ch in self._calib_data else '⬜'} {label}"
            for ch, label in labels.items()
        )
