"""Gumax RF protocol encoder/decoder (inline copy of rf_protocols.protocols.gumax).

Kept self-contained so the custom component works without installing the
rf_protocols package separately. Keep the encode-side logic in sync with the
upstream package; decode_signal()/_extract_bits() etc. are HA-specific and
have no upstream counterpart.
"""

from __future__ import annotations

from dataclasses import dataclass

PREAMBLE: list[int] = [
    262, -612, 269, -610, 268, -622, 269, -610,
    262, -610, 265, -613, 268, -611, 4998,
]

DEVICE_ID_DEFAULT = "10100001101100101100001111010100"  # 0xA1B2C3D4 (example)

_CH_VALS: dict[int, int] = {
    1: 0x0080, 2: 0x0100, 3: 0x0200, 4: 0x0400,
    5: 0x0800, 6: 0x1000, 7: 0x2000, 8: 0x4000,
    9: 0x0000, 10: 0x0001, 11: 0x0002, 12: 0x0004,
    13: 0x0008, 14: 0x0010, 15: 0x0020, 16: 0x0040,
}

_CMD_B7: dict[str, int] = {"up": 0x05, "down": 0x21, "stop": 0x11}


@dataclass(frozen=True)
class DeviceProfile:
    """Per-remote calibration, learned from live captures (see config flow).

    x_dev is a constant baked into the transmitter chip; it cannot be derived
    from device_id and must be learned from a real capture on any channel
    other than K1/K9. k1_extra/k9_extra correct for two channels that were
    observed to sometimes add a small extra amount on top of x_dev — some
    remotes need it, others don't, so it must be measured, not assumed.

    b9 (the final bit of the packet) has no known formula. b9_k1/b9_k9 hold
    the directly observed values for those two channels; b9_default is used
    for every other channel, including CC.
    """

    x_dev: int
    k1_extra: int = 0
    k9_extra: int = 0
    b9_default: int = 1
    b9_k1: int = 1
    b9_k9: int = 1


# Reproduces the original (pre-calibration) formula, which was hardcoded to a
# single remote's x_dev without knowing it. Used as a fallback for config
# entries created before per-remote calibration existed, so they keep working
# exactly as before until the user re-runs the capture flow.
LEGACY_PROFILE = DeviceProfile(
    x_dev=0xD4, k1_extra=0, k9_extra=0, b9_default=0, b9_k1=1, b9_k9=1
)


def _wrap(total: int) -> int:
    return (total % 256) ^ 0x80 if total >= 256 else total


def _checksum(x_dev: int, b5: int, b6: int, b7: int, *, include_b5: bool = True) -> int:
    return _wrap(x_dev + b7 + b6 + (b5 if include_b5 else 0))


def infer_x_dev(known_sum: int, checksum: int, hint: int | None = None) -> int:
    """Invert the checksum formula.

    Given b5+b6+b7 (known_sum) and an observed checksum byte, recover the
    additive constant that produced it — x_dev itself for a normal channel,
    or x_dev+k1_extra / x_dev+k9_extra for a K1/K9 capture.

    The equation can have more than one valid solution in [0, 255] (the wrap
    can plausibly have triggered or not) — this happens more often for K1,
    whose channel bit makes known_sum large. Without a hint, the no-overflow
    solution is preferred (right for deriving x_dev itself from a "normal"
    channel like K2). When deriving a K1/K9 extra on top of an already-known
    x_dev, pass hint=x_dev so the candidate closest to it — i.e. the smallest
    extra — is chosen; real remotes only ever add a small correction there.
    """
    candidates: list[int] = []
    candidate = checksum - known_sum
    if 0 <= candidate <= 255:
        candidates.append(candidate)
    for overflow_count in (1, 2, 3):
        candidate = 256 * overflow_count + (checksum ^ 0x80) - known_sum
        if 0 <= candidate <= 255 and candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        raise ValueError("could not infer x_dev from checksum/sum pair")
    if hint is None or len(candidates) == 1:
        return candidates[0]
    return min(candidates, key=lambda c: abs(c - hint))


def channel_bytes(channel: int, command: str) -> tuple[int, int, int]:
    """Return the canonical (b5, b6, b7) for a channel/command pair."""
    cv = _CH_VALS[channel]
    b5 = (cv >> 8) & 0xFF
    b6 = cv & 0xFF
    b7 = _CMD_B7[command] | (0x80 if channel == 9 else 0)
    return b5, b6, b7


def _pulses_from_bits(bits: str) -> list[int]:
    pulses: list[int] = list(PREAMBLE)
    for i, bit in enumerate(bits):
        space = -600 if bit == "0" else -280
        mark = 600 if i + 1 < len(bits) and bits[i + 1] == "1" else 280
        pulses.append(space)
        pulses.append(mark)
    return pulses


def encode(channel: int, command: str, device_id: str, profile: DeviceProfile) -> list[int]:
    if channel not in _CH_VALS:
        raise ValueError(f"channel must be 1–16, got {channel!r}")
    if command not in _CMD_B7:
        raise ValueError(f"command must be 'up', 'down', or 'stop', got {command!r}")
    if len(device_id) != 32 or not all(c in "01" for c in device_id):
        raise ValueError("device_id must be a 32-character binary string")

    b5, b6, b7 = channel_bytes(channel, command)
    if channel == 1:
        x_dev, b9 = profile.x_dev + profile.k1_extra, profile.b9_k1
    elif channel == 9:
        x_dev, b9 = profile.x_dev + profile.k9_extra, profile.b9_k9
    else:
        x_dev, b9 = profile.x_dev, profile.b9_default
    cs = _checksum(x_dev, b5, b6, b7)

    bits = (
        device_id
        + format(b5, "08b")
        + format(b6, "08b")
        + format(b7, "08b")
        + format(cs, "08b")
        + str(b9)
    )
    return _pulses_from_bits(bits)


def device_id_from_hex(hex_str: str) -> str:
    cleaned = hex_str.strip().upper().lstrip("0X") or "0"
    return format(int(cleaned, 16), "032b")


def encode_cc(command: str, device_id: str, profile: DeviceProfile) -> list[int]:
    if command not in _CMD_B7:
        raise ValueError(f"command must be 'up', 'down', or 'stop', got {command!r}")
    if len(device_id) != 32 or not all(c in "01" for c in device_id):
        raise ValueError("device_id must be a 32-character binary string")

    b7 = _CMD_B7[command] | 0x80
    cs = _checksum(profile.x_dev, 0x7F, 0xFF, b7, include_b5=False)

    bits = (
        device_id
        + format(0x7F, "08b")
        + format(0xFF, "08b")
        + format(b7, "08b")
        + format(cs, "08b")
        + str(profile.b9_default)
    )
    return _pulses_from_bits(bits)


_SYNC_THRESHOLD = 3000  # µs — sync mark is ~4998µs; preamble marks are ~270µs
_LONG = 600   # µs space → bit 0
_SHORT = 280  # µs space → bit 1
_TOLERANCE = 0.35

_CH_VALS_REVERSE: dict[int, int] = {v: k for k, v in _CH_VALS.items()}
_CMD_B7_REVERSE: dict[int, str] = {v: k for k, v in _CMD_B7.items()}


def _extract_bits(pulses: list[int]) -> list[str] | None:
    sync_idx = next((i for i, p in enumerate(pulses) if p > _SYNC_THRESHOLD), None)
    if sync_idx is None:
        return None
    bits: list[str] = []
    for p in pulses[sync_idx + 1:]:
        if p >= 0:
            continue
        abs_p = abs(p)
        if abs(abs_p - _LONG) < _LONG * _TOLERANCE:
            bits.append("0")
        elif abs(abs_p - _SHORT) < _SHORT * _TOLERANCE:
            bits.append("1")
    return bits if bits else None


def decode_device_id(pulses: list[int]) -> str | None:
    """Extract device ID (8-char hex) from raw captured pulse data."""
    bits = _extract_bits(pulses)
    if not bits or len(bits) < 32:
        return None
    return format(int("".join(bits[:32]), 2), "08X")


def decode_signal(pulses: list[int], profile: DeviceProfile | None = None) -> dict | None:
    """Decode a full RF capture into device_id, channel, command, checksum and b9.

    Returns a dict with device_id (8-char hex), channel (1-16 or "CC"),
    command ("up"/"down"/"stop"), checksum (b8), b9, and — only when a
    DeviceProfile is supplied — checksum_valid/encode_match against that
    profile's formula. Without a profile (e.g. during initial calibration,
    before x_dev is known) checksum_valid/encode_match are None.
    Returns None when no sync pulse, fewer than 65 data bits, or the channel
    or command byte does not match any known value.
    """
    bits = _extract_bits(pulses)
    if not bits or len(bits) < 65:
        return None
    device_id = format(int("".join(bits[:32]), 2), "08X")
    b5 = int("".join(bits[32:40]), 2)
    b6 = int("".join(bits[40:48]), 2)
    b7 = int("".join(bits[48:56]), 2)
    cv = (b5 << 8) | b6
    channel: int | str | None = "CC" if cv == 0x7FFF else _CH_VALS_REVERSE.get(cv)
    command: str | None = _CMD_B7_REVERSE.get(b7 & 0x7F)
    if channel is None or command is None:
        return None
    b8 = int("".join(bits[56:64]), 2)
    b9 = int(bits[64])

    result: dict = {
        "device_id": device_id,
        "channel": channel,
        "command": command,
        "checksum": b8,
        "b9": b9,
        "checksum_valid": None,
        "encode_match": None,
    }

    if profile is not None:
        device_id_bin = "".join(bits[:32])
        ref_pulses = (
            encode_cc(command, device_id_bin, profile)
            if channel == "CC"
            else encode(channel, command, device_id_bin, profile)
        )
        ref_bits = _extract_bits(ref_pulses)
        ref_valid = ref_bits is not None and len(ref_bits) >= 65
        result["checksum_valid"] = ref_valid and ref_bits[56:64] == bits[56:64]
        result["encode_match"] = ref_valid and ref_bits[:65] == bits[:65]

    return result
