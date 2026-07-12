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

Checksum (b8) is x_dev + b5 + b6 + b7, wrapped mod 256 with the top bit
flipped on overflow. x_dev is a per-remote constant that cannot be derived
from device_id — it must be learned from a live capture (see DeviceProfile).
K1 and K9 sometimes use x_dev plus a small per-remote correction instead of
x_dev directly; b9 has no known formula and is stored per remote as well.
CC (broadcast-all) uses the same formula as any other channel, but excludes
b5 (its b5 is a wildcard sentinel, not a real per-channel value).
"""

from __future__ import annotations

from dataclasses import dataclass

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
    # K9 has cv=0x0000 (no channel bit); bit 7 of b7 flags it as a valid channel.
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


def encode(
    channel: int,
    command: str,
    device_id: str,
    profile: DeviceProfile,
) -> list[int]:
    """Return a pulse list (µs) for the given channel and command.

    Positive values are marks (TX on), negative values are spaces (TX off).

    Args:
        channel: 1–16
        command: 'up', 'down', or 'stop'
        device_id: 32-character binary string. Use device_id_from_hex() to
                   convert a hex ID (e.g. 'A1B2C3D4') to the required format.
        profile: per-remote calibration (x_dev, K1/K9 corrections, b9 values),
                 learned via the config flow's capture steps.
    """
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
    assert len(bits) == 65, f"expected 65 bits, got {len(bits)}"
    return _pulses_from_bits(bits)


def device_id_from_hex(hex_str: str) -> str:
    """Convert a hex device ID (e.g. 'A1B2C3D4' or '0xA1B2C3D4') to a 32-bit binary string."""
    cleaned = hex_str.strip().upper().lstrip("0X") or "0"
    return format(int(cleaned, 16), "032b")


def encode_cc(command: str, device_id: str, profile: DeviceProfile) -> list[int]:
    """Return a pulse list for a CC (broadcast-all) command.

    Same formula as encode(), but b5 (0x7F, a wildcard sentinel rather than a
    real channel value) is excluded from the checksum sum, and b9 always uses
    profile.b9_default.
    """
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
    assert len(bits) == 65, f"expected 65 bits, got {len(bits)}"
    return _pulses_from_bits(bits)
