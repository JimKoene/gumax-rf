"""Gumax RF protocol encoder (inline copy of rf_protocols.protocols.gumax).

Kept self-contained so the custom component works without installing the
rf_protocols package separately.  Keep in sync with the upstream package.
"""

from __future__ import annotations

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

_CMD_B7:     dict[str, int] = {"up": 0x05, "down": 0x21, "stop": 0x11}
_CMD_OFFSET: dict[str, int] = {"up": 0,   "down": 28,   "stop": 12}
_B9: dict[int, int] = {ch: 1 if ch in (1, 9) else 0 for ch in range(1, 17)}


def _checksum(cv: int, command: str) -> int:
    raw_base = 217 + (cv >> 8) + (cv & 0x7F)
    base = (raw_base % 256) ^ 0x80 if raw_base >= 256 else raw_base
    raw = base + _CMD_OFFSET[command]
    return (raw % 256) ^ 0x80 if raw >= 256 else raw


def encode(channel: int, command: str, device_id: str = DEVICE_ID_DEFAULT) -> list[int]:
    if channel not in _CH_VALS:
        raise ValueError(f"channel must be 1–16, got {channel!r}")
    if command not in _CMD_B7:
        raise ValueError(f"command must be 'up', 'down', or 'stop', got {command!r}")
    if len(device_id) != 32 or not all(c in "01" for c in device_id):
        raise ValueError("device_id must be a 32-character binary string")

    cv = _CH_VALS[channel]
    b5 = (cv >> 8) & 0xFF
    b6 = cv & 0xFF
    b7 = _CMD_B7[command] | (0x80 if channel == 9 else 0)
    cs = _checksum(cv, command)
    b9 = _B9[channel]

    bits = (
        device_id
        + format(b5, "08b")
        + format(b6, "08b")
        + format(b7, "08b")
        + format(cs, "08b")
        + str(b9)
    )

    pulses: list[int] = list(PREAMBLE)
    for i, bit in enumerate(bits):
        space = -600 if bit == "0" else -280
        mark  =  600 if i + 1 < len(bits) and bits[i + 1] == "1" else 280
        pulses.append(space)
        pulses.append(mark)

    return pulses


def device_id_from_hex(hex_str: str) -> str:
    cleaned = hex_str.strip().upper().lstrip("0X") or "0"
    return format(int(cleaned, 16), "032b")


_SYNC_THRESHOLD = 3000  # µs — sync mark is ~4998µs; preamble marks are ~270µs
_LONG = 600   # µs space → bit 0
_SHORT = 280  # µs space → bit 1
_TOLERANCE = 0.35

_CH_VALS_REVERSE: dict[int, int] = {v: k for k, v in _CH_VALS.items()}
_CMD_B7_REVERSE: dict[int, str] = {v: k for k, v in _CMD_B7.items()}
_CC_B8_BASE = 216


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


def decode_signal(pulses: list[int]) -> dict | None:
    """Decode a full RF capture into device_id, channel, and command.

    Returns a dict with device_id (8-char hex), channel (1-16 or "CC"),
    and command ("up"/"down"/"stop").
    Returns None when no sync pulse, fewer than 56 data bits, or the channel
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
    expected_b8 = (
        _CC_B8_BASE + _CMD_OFFSET[command]
        if channel == "CC"
        else _checksum(cv, command)
    )

    device_id_bin = "".join(bits[:32])
    ref_pulses = (
        encode_cc(command, device_id_bin)
        if channel == "CC"
        else encode(channel, command, device_id_bin)
    )
    ref_bits = _extract_bits(ref_pulses)
    encode_match = ref_bits is not None and "".join(ref_bits[:65]) == "".join(bits[:65])

    return {
        "device_id": device_id,
        "channel": channel,
        "command": command,
        "checksum": b8,
        "checksum_valid": b8 == expected_b8,
        "encode_match": encode_match,
    }


# CC broadcast: cv=0x7FFF, b7 has bit 7 set (same rule as K9), b9=0.
# b8 does not follow _checksum() — decoded captures show b8_base=216
# instead of the formula's 87. The +28/+12 command offsets still apply.


def encode_cc(command: str, device_id: str = DEVICE_ID_DEFAULT) -> list[int]:
    """Encode a CC broadcast command for the given device ID."""
    if command not in _CMD_B7:
        raise ValueError(f"command must be 'up', 'down', or 'stop', got {command!r}")
    if len(device_id) != 32 or not all(c in "01" for c in device_id):
        raise ValueError("device_id must be a 32-character binary string")

    b7 = _CMD_B7[command] | 0x80
    b8 = _CC_B8_BASE + _CMD_OFFSET[command]

    bits = (
        device_id
        + format(0x7F, "08b")
        + format(0xFF, "08b")
        + format(b7, "08b")
        + format(b8, "08b")
        + "0"
    )

    pulses: list[int] = list(PREAMBLE)
    for i, bit in enumerate(bits):
        space = -600 if bit == "0" else -280
        mark  =  600 if i + 1 < len(bits) and bits[i + 1] == "1" else 280
        pulses.append(space)
        pulses.append(mark)

    return pulses
