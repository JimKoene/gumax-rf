"""Gumax RF protocol — 433.92 MHz OOK, ESP32 + CC1101.

Packet structure:
  Preamble : 7 × (262 µs mark + 612 µs space)
  Sync     : 4998 µs mark
  Data     : 65 bits  (device_id[32] + b5[8] + b6[8] + b7[8] + b8[8] + b9[1])

Space encodes the bit value; mark encodes the look-ahead to the next bit.
  space -600 µs → bit 0 (long)
  space -280 µs → bit 1 (short)
  mark   600 µs → next bit is 1
  mark   280 µs → next bit is 0 (or last bit)

Verified against K1–K16 captures (100 % match).
CC (broadcast-all) uses stored captures; b8 does not follow the formula.
"""

from __future__ import annotations

PREAMBLE: list[int] = [
    262, -612, 269, -610, 268, -622, 269, -610,
    262, -610, 265, -613, 268, -611, 4998,
]

DEVICE_ID_DEFAULT = "10100001101100101100001111010100"  # 0xA1B2C3D4 (example)

CHANNELS: tuple[int, ...] = tuple(range(1, 17))
COMMANDS: tuple[str, ...] = ("up", "down", "stop")

_CH_VALS: dict[int, int] = {
    1: 0x0080, 2: 0x0100, 3: 0x0200, 4: 0x0400,
    5: 0x0800, 6: 0x1000, 7: 0x2000, 8: 0x4000,
    9: 0x0000, 10: 0x0001, 11: 0x0002, 12: 0x0004,
    13: 0x0008, 14: 0x0010, 15: 0x0020, 16: 0x0040,
}

_CMD_B7:     dict[str, int] = {"up": 0x05, "down": 0x21, "stop": 0x11}
_CMD_OFFSET: dict[str, int] = {"up": 0,   "down": 28,   "stop": 12}

# K1 and K9 carry parity bit = 1; all others = 0.
_B9: dict[int, int] = {ch: 1 if ch in (1, 9) else 0 for ch in range(1, 17)}


def _checksum(cv: int, command: str) -> int:
    # cv & 0x7F masks bit-7 of b6 (K1's channel bit) so K1 and K9 share the same base.
    raw_base = 217 + (cv >> 8) + (cv & 0x7F)
    base = (raw_base % 256) ^ 0x80 if raw_base >= 256 else raw_base
    raw = base + _CMD_OFFSET[command]
    return (raw % 256) ^ 0x80 if raw >= 256 else raw


def encode(
    channel: int,
    command: str,
    device_id: str = DEVICE_ID_DEFAULT,
) -> list[int]:
    """Return a pulse list (µs) for the given channel and command.

    Positive values are marks (TX on), negative values are spaces (TX off).

    Args:
        channel: 1–16
        command: 'up', 'down', or 'stop'
        device_id: 32-character binary string. Use device_id_from_hex() to
                   convert a hex ID (e.g. 'A1B2C3D4') to the required format.
    """
    if channel not in _CH_VALS:
        raise ValueError(f"channel must be 1–16, got {channel!r}")
    if command not in _CMD_B7:
        raise ValueError(f"command must be 'up', 'down', or 'stop', got {command!r}")
    if len(device_id) != 32 or not all(c in "01" for c in device_id):
        raise ValueError("device_id must be a 32-character binary string")

    cv = _CH_VALS[channel]
    b5 = (cv >> 8) & 0xFF
    b6 = cv & 0xFF
    # K9 has cv=0x0000 (no channel bit); bit 7 of b7 flags it as a valid channel.
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
    assert len(bits) == 65, f"expected 65 bits, got {len(bits)}"

    pulses: list[int] = list(PREAMBLE)
    for i, bit in enumerate(bits):
        space = -600 if bit == "0" else -280
        mark  =  600 if i + 1 < len(bits) and bits[i + 1] == "1" else 280
        pulses.append(space)
        pulses.append(mark)

    return pulses


def device_id_from_hex(hex_str: str) -> str:
    """Convert a hex device ID (e.g. 'A1B2C3D4' or '0xA1B2C3D4') to a 32-bit binary string."""
    cleaned = hex_str.strip().upper().lstrip("0X") or "0"
    return format(int(cleaned, 16), "032b")


# CC (broadcast-all channels) uses stored captures.
# cv = 0x7FFF gives b8_base = 216 which does not follow the checksum formula
# (formula would give 87). The +28/+12 offsets for down/stop still apply.
# b8: up=216, down=244, stop=228.
_CC_CAPTURES: dict[str, list[int]] = {
    "up": [
        230, -652, 225, -647, 239, -633, 239, -650, 230, -638, 235, -636, 240, -652, 4969,
        -632, 580, -296, 267, -620, 583, -295, 260, -610, 276, -603, 268, -610, 594, -295,
        581, -297, 590, -294, 260, -618, 583, -296, 593, -287, 267, -598, 278, -608, 593,
        -294, 588, -281, 275, -616, 268, -603, 590, -295, 586, -283, 280, -609, 594, -284,
        595, -281, 269, -622, 588, -281, 274, -614, 582, -296, 592, -287, 581, -296, 594,
        -280, 605, -278, 275, -607, 591, -280, 600, -281, 589, -294, 598, -282, 591, -294,
        584, -284, 592, -300, 580, -297, 581, -294, 597, -282, 599, -281, 590, -289, 593,
        -291, 592, -279, 596, -283, 598, -282, 279, -602, 267, -607, 279, -611, 266, -608,
        597, -282, 271, -610, 589, -294, 597, -281, 585, -285, 278, -611, 593, -279, 591,
        -297, 262, -609, 274, -618, 255, -611, 281, -594, 584,
    ],
    "down": [
        252, -623, 265, -621, 257, -619, 262, -621, 253, -632, 245, -636, 253, -616, 4995,
        -633, 582, -297, 257, -622, 581, -296, 266, -609, 272, -606, 273, -611, 590, -292,
        581, -291, 589, -290, 268, -610, 595, -294, 582, -296, 261, -610, 271, -609, 595,
        -284, 593, -285, 267, -624, 255, -624, 585, -295, 584, -284, 266, -621, 583, -297,
        593, -275, 268, -619, 588, -293, 265, -607, 586, -296, 589, -294, 586, -293, 590,
        -292, 583, -284, 280, -607, 582, -296, 594, -280, 596, -296, 588, -282, 588, -291,
        594, -299, 576, -290, 596, -284, 594, -297, 578, -303, 579, -291, 596, -286, 593,
        -295, 579, -296, 581, -294, 585, -294, 272, -612, 591, -293, 256, -613, 267, -621,
        267, -603, 281, -606, 592, -289, 582, -295, 595, -296, 575, -295, 590, -281, 268,
        -624, 581, -294, 268, -608, 266, -621, 266, -592, 594,
    ],
    "stop": [
        267, -620, 261, -610, 276, -602, 268, -611, 267, -621, 268, -609, 262, -624, 4987,
        -636, 581, -294, 266, -609, 592, -294, 259, -618, 269, -612, 265, -615, 583, -297,
        589, -293, 586, -283, 268, -621, 582, -293, 589, -290, 264, -608, 272, -611, 586,
        -292, 594, -284, 280, -607, 267, -610, 595, -279, 595, -284, 272, -616, 584, -295,
        583, -296, 263, -616, 584, -296, 264, -617, 584, -297, 579, -298, 592, -277, 604,
        -279, 596, -283, 272, -608, 595, -296, 582, -294, 585, -294, 585, -297, 586, -296,
        579, -297, 593, -280, 595, -294, 587, -294, 587, -279, 596, -285, 598, -281, 592,
        -297, 581, -294, 597, -283, 586, -295, 264, -616, 269, -609, 593, -282, 265, -610,
        279, -609, 271, -606, 585, -290, 590, -294, 585, -295, 589, -294, 259, -611, 268,
        -619, 591, -292, 266, -607, 264, -620, 260, -590, 592,
    ],
}


def encode_cc(command: str) -> list[int]:
    """Return the stored capture pulse list for a CC (broadcast-all) command.

    CC pulses cannot be derived from the checksum formula (b8_base=216 for CC
    vs 87 from the formula). All three commands are available as captures.
    """
    if command not in _CC_CAPTURES:
        raise ValueError(
            f"command must be 'up', 'down', or 'stop', got {command!r}"
        )
    return list(_CC_CAPTURES[command])
