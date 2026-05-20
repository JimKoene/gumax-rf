"""Tests for Gumax RF protocol encoder.

All expected values from the spec's verified capture table (K1–K11 100% match).
"""

import pytest
from rf_protocols.protocols.gumax import DEVICE_ID_DEFAULT, PREAMBLE, encode, encode_cc, device_id_from_hex

PREAMBLE_LEN = len(PREAMBLE)  # 15
BITS = 65
EXPECTED_PULSES = PREAMBLE_LEN + BITS * 2  # 145


def _bits(pulses: list[int]) -> list[int]:
    """Decode bit values from encoded pulse list (space sign encodes bit)."""
    return [1 if pulses[PREAMBLE_LEN + i * 2] == -280 else 0 for i in range(BITS)]


def _byte(pulses: list[int], start: int) -> int:
    """Extract an 8-bit value from decoded bits starting at bit index `start`."""
    bits = _bits(pulses)
    return int("".join(str(b) for b in bits[start : start + 8]), 2)


# ── packet structure ──────────────────────────────────────────────────────────

def test_encode_total_length():
    assert len(encode(1, "up")) == EXPECTED_PULSES


def test_encode_preamble():
    pulses = encode(1, "up")
    assert pulses[:PREAMBLE_LEN] == list(PREAMBLE)


def test_encode_device_id_default():
    pulses = encode(1, "up")
    bits = _bits(pulses)
    assert "".join(str(b) for b in bits[:32]) == DEVICE_ID_DEFAULT


def test_encode_rejects_bad_channel():
    with pytest.raises(ValueError):
        encode(0, "up")
    with pytest.raises(ValueError):
        encode(17, "up")


def test_encode_rejects_bad_command():
    with pytest.raises(ValueError):
        encode(1, "left")


def test_encode_rejects_bad_device_id():
    with pytest.raises(ValueError):
        encode(1, "up", "not-binary")


# ── b7 (command byte) ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("command,expected_b7", [
    ("up",   0x05),
    ("down", 0x21),
    ("stop", 0x11),
])
def test_b7_normal_channel(command, expected_b7):
    pulses = encode(1, command)
    assert _byte(pulses, 48) == expected_b7


def test_b7_k9_sets_bit7():
    """K9 (cv=0x0000) must have bit 7 of b7 set to identify it as a valid channel."""
    assert _byte(encode(9, "up"),   48) == 0x05 | 0x80
    assert _byte(encode(9, "down"), 48) == 0x21 | 0x80
    assert _byte(encode(9, "stop"), 48) == 0x11 | 0x80


# ── b9 (parity bit) ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("channel,expected_b9", [
    (1,  1),
    (9,  1),
    (2,  0),
    (3,  0),
    (8,  0),
    (10, 0),
    (16, 0),
])
def test_b9_parity(channel, expected_b9):
    bits = _bits(encode(channel, "up"))
    assert bits[64] == expected_b9


# ── b8 (checksum) — verified against spec capture table ──────────────────────

@pytest.mark.parametrize("channel,command,expected_b8", [
    (1,  "up",   217),
    (1,  "down", 245),
    (1,  "stop", 229),
    (2,  "up",   218),
    (2,  "down", 246),
    (2,  "stop", 230),
    (3,  "up",   219),
    (3,  "down", 247),
    (3,  "stop", 231),
    (4,  "up",   221),
    (4,  "down", 249),
    (4,  "stop", 233),
    (5,  "up",   225),
    (5,  "down", 253),
    (5,  "stop", 237),
    (6,  "up",   233),
    (6,  "down", 133),  # 233+28=261 → (261%256)^0x80 = 5^0x80 = 133
    (6,  "stop", 245),
    (7,  "up",   249),
    (7,  "down", 149),  # 249+28=277 → (277%256)^0x80 = 21^0x80 = 149
    (7,  "stop", 133),  # 249+12=261 → (261%256)^0x80 = 5^0x80  = 133
    (8,  "up",   153),  # raw_base=281 → (281%256)^0x80 = 25^0x80 = 153
    (8,  "down", 181),
    (8,  "stop", 165),
    (9,  "up",   217),
    (9,  "stop", 229),
    (14, "up",   233),
    (14, "stop", 245),
    (15, "up",   249),
    (15, "stop", 133),
    (16, "up",   153),
    (16, "stop", 165),
])
def test_b8_checksum(channel, command, expected_b8):
    assert _byte(encode(channel, command), 56) == expected_b8


# ── device_id_from_hex ────────────────────────────────────────────────────────

def test_device_id_from_hex_default():
    assert device_id_from_hex("A1B2C3D4") == DEVICE_ID_DEFAULT


def test_device_id_from_hex_prefix():
    assert device_id_from_hex("0xA1B2C3D4") == DEVICE_ID_DEFAULT


def test_device_id_from_hex_lowercase():
    assert device_id_from_hex("a1b2c3d4") == DEVICE_ID_DEFAULT


def test_device_id_from_hex_zero():
    assert device_id_from_hex("0") == "0" * 32


# ── cross-validation against working YAML captures ───────────────────────────
# Reference codes taken verbatim from the verified ESPHome YAML.
# Comparison is on the data portion only (pulses[15:]) since the preamble
# contains measured jitter but our calculated PREAMBLE is the nominal value.

_K1_UP_DATA = [
    -280, 280, -600, 600, -280, 280, -600, 280, -600, 280, -600, 280, -600, 600,
    -280, 600, -280, 280, -600, 600, -280, 600, -280, 280, -600, 280, -600, 600,
    -280, 280, -600, 600, -280, 600, -280, 280, -600, 280, -600, 280, -600, 280,
    -600, 600, -280, 600, -280, 600, -280, 600, -280, 280, -600, 600, -280, 280,
    -600, 600, -280, 280, -600, 280, -600, 280, -600, 280, -600, 280, -600, 280,
    -600, 280, -600, 280, -600, 280, -600, 280, -600, 600, -280, 280, -600, 280,
    -600, 280, -600, 280, -600, 280, -600, 280, -600, 280, -600, 280, -600, 280,
    -600, 280, -600, 280, -600, 280, -600, 600, -280, 280, -600, 600, -280, 600,
    -280, 600, -280, 280, -600, 600, -280, 600, -280, 280, -600, 280, -600, 600,
    -280, 600, -280, 280,
]

_K9_UP_DATA = [
    -280, 280, -600, 600, -280, 280, -600, 280, -600, 280, -600, 280, -600, 600,
    -280, 600, -280, 280, -600, 600, -280, 600, -280, 280, -600, 280, -600, 600,
    -280, 280, -600, 600, -280, 600, -280, 280, -600, 280, -600, 280, -600, 280,
    -600, 600, -280, 600, -280, 600, -280, 600, -280, 280, -600, 600, -280, 280,
    -600, 600, -280, 280, -600, 280, -600, 280, -600, 280, -600, 280, -600, 280,
    -600, 280, -600, 280, -600, 280, -600, 280, -600, 280, -600, 280, -600, 280,
    -600, 280, -600, 280, -600, 280, -600, 280, -600, 280, -600, 600, -280, 280,
    -600, 280, -600, 280, -600, 280, -600, 600, -280, 280, -600, 600, -280, 600,
    -280, 600, -280, 280, -600, 600, -280, 600, -280, 280, -600, 280, -600, 600,
    -280, 600, -280, 280,
]


def test_k1_up_matches_yaml_capture():
    assert encode(1, "up")[PREAMBLE_LEN:] == _K1_UP_DATA


def test_k9_up_matches_yaml_capture():
    assert encode(9, "up")[PREAMBLE_LEN:] == _K9_UP_DATA


# ── CC captured pulses ────────────────────────────────────────────────────────
# CC uses cv=0x7FFF; b8_base=216 (not 87 from the formula), offsets still apply:
#   up=216, down=244, stop=228.

def test_encode_cc_all_commands_available():
    for cmd in ("up", "down", "stop"):
        pulses = encode_cc(cmd)
        assert isinstance(pulses, list)
        assert len(pulses) == 145  # 15 preamble + 65*2 data


def test_encode_cc_rejects_bad_command():
    with pytest.raises(ValueError):
        encode_cc("left")


def test_encode_cc_returns_copy():
    assert encode_cc("up") is not encode_cc("up")
