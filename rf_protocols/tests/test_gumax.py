"""Tests for the Gumax RF protocol encoder.

Checksum/b9 expectations come from two sources:
  - The comprehensive channel/command table further down was cross-validated
    against a working ESPHome YAML reference implementation, using REMOTE_1
    (whose calibration reproduces that original formula exactly).
  - test_b8_and_b9_against_local_captures() covers additional real remotes,
    but their data lives in local_captures.py, which is gitignored and not
    part of this repo — those remotes came from real users who did not agree
    to have their device's data published. That test is skipped when the
    file isn't present (e.g. on CI, or for anyone else who clones this repo).
"""

import pytest
from rf_protocols.protocols.gumax import (
    DEVICE_ID_DEFAULT,
    PREAMBLE,
    DeviceProfile,
    channel_bytes,
    device_id_from_hex,
    encode,
    encode_cc,
    infer_x_dev,
)

try:
    from .local_captures import LOCAL_REMOTES
except ImportError:
    LOCAL_REMOTES = []

PREAMBLE_LEN = len(PREAMBLE)  # 15
BITS = 65
EXPECTED_PULSES = PREAMBLE_LEN + BITS * 2  # 145

# REMOTE_1's calibration reproduces the original (pre-calibration) formula
# exactly, which is why it doubles as the profile for the structural /
# cross-implementation tests below. It's a real remote too, but its owner is
# also this project's maintainer, who's fine with these specific derived
# values (not the device_id itself) being public — see PROTOCOLSPEC.md.
REMOTE_1 = DeviceProfile(x_dev=0xD4, k1_extra=0, k9_extra=0, b9_default=0, b9_k1=1, b9_k9=1)


def _bits(pulses: list[int]) -> list[int]:
    """Decode bit values from encoded pulse list (space sign encodes bit)."""
    return [1 if pulses[PREAMBLE_LEN + i * 2] == -280 else 0 for i in range(BITS)]


def _byte(pulses: list[int], start: int) -> int:
    """Extract an 8-bit value from decoded bits starting at bit index `start`."""
    bits = _bits(pulses)
    return int("".join(str(b) for b in bits[start : start + 8]), 2)


# ── packet structure ──────────────────────────────────────────────────────────

def test_encode_total_length():
    assert len(encode(1, "up", DEVICE_ID_DEFAULT, REMOTE_1)) == EXPECTED_PULSES


def test_encode_preamble():
    pulses = encode(1, "up", DEVICE_ID_DEFAULT, REMOTE_1)
    assert pulses[:PREAMBLE_LEN] == list(PREAMBLE)


def test_encode_device_id_default():
    pulses = encode(1, "up", DEVICE_ID_DEFAULT, REMOTE_1)
    bits = _bits(pulses)
    assert "".join(str(b) for b in bits[:32]) == DEVICE_ID_DEFAULT


def test_encode_rejects_bad_channel():
    with pytest.raises(ValueError):
        encode(0, "up", DEVICE_ID_DEFAULT, REMOTE_1)
    with pytest.raises(ValueError):
        encode(17, "up", DEVICE_ID_DEFAULT, REMOTE_1)


def test_encode_rejects_bad_command():
    with pytest.raises(ValueError):
        encode(1, "left", DEVICE_ID_DEFAULT, REMOTE_1)


def test_encode_rejects_bad_device_id():
    with pytest.raises(ValueError):
        encode(1, "up", "not-binary", REMOTE_1)


# ── b7 (command byte) ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("command,expected_b7", [
    ("up",   0x05),
    ("down", 0x21),
    ("stop", 0x11),
])
def test_b7_normal_channel(command, expected_b7):
    pulses = encode(1, command, DEVICE_ID_DEFAULT, REMOTE_1)
    assert _byte(pulses, 48) == expected_b7


def test_b7_k9_sets_bit7():
    """K9 (cv=0x0000) must have bit 7 of b7 set to identify it as a valid channel."""
    assert _byte(encode(9, "up", DEVICE_ID_DEFAULT, REMOTE_1),   48) == 0x05 | 0x80
    assert _byte(encode(9, "down", DEVICE_ID_DEFAULT, REMOTE_1), 48) == 0x21 | 0x80
    assert _byte(encode(9, "stop", DEVICE_ID_DEFAULT, REMOTE_1), 48) == 0x11 | 0x80


# ── channel_bytes ──────────────────────────────────────────────────────────────

def test_channel_bytes_normal_channel():
    assert channel_bytes(1, "up") == (0x00, 0x80, 0x05)


def test_channel_bytes_k9_sets_flag():
    assert channel_bytes(9, "stop") == (0x00, 0x00, 0x11 | 0x80)


# ── b9 (parity bit) — REMOTE_1's stored pattern ────────────────────────────────

@pytest.mark.parametrize("channel,expected_b9", [
    (1,  1),
    (9,  1),
    (2,  0),
    (3,  0),
    (8,  0),
    (10, 0),
    (16, 0),
])
def test_b9_uses_profile(channel, expected_b9):
    bits = _bits(encode(channel, "up", DEVICE_ID_DEFAULT, REMOTE_1))
    assert bits[64] == expected_b9


# ── b8 (checksum) — cross-validated against ESPHome YAML reference ───────────

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
    assert _byte(encode(channel, command, DEVICE_ID_DEFAULT, REMOTE_1), 56) == expected_b8


# ── b8 + b9 — REMOTE_1 (public reference remote) ──────────────────────────────
# Real captures, cross-checked during protocol reverse-engineering. K1 and K9
# differ from the "normal channel" formula per remote (see DeviceProfile
# docstring), which is exactly what these cover.

@pytest.mark.parametrize("channel,command,expected_b8,expected_b9", [
    (1,  "up",   0xD9, 1),
    (1,  "stop", 0xE5, 1),
    (1,  "down", 0xF5, 1),
    (7,  "up",   0xF9, 0),
    (7,  "stop", 0x85, 0),
    (7,  "down", 0x95, 0),
    (12, "up",   0xDD, 0),
    (12, "stop", 0xE9, 0),
    (12, "down", 0xF9, 0),
])
def test_b8_and_b9_against_reference_remote(channel, command, expected_b8, expected_b9):
    pulses = encode(channel, command, DEVICE_ID_DEFAULT, REMOTE_1)
    assert _byte(pulses, 56) == expected_b8
    assert _bits(pulses)[64] == expected_b9


def test_b8_and_b9_against_local_captures():
    """Additional real remotes' data, kept out of the public repo — see
    local_captures.py's docstring. Skipped without that (gitignored) file."""
    if not LOCAL_REMOTES:
        pytest.skip("rf_protocols/tests/local_captures.py not present locally")
    for remote in LOCAL_REMOTES:
        profile = remote["profile"]
        for channel, command, expected_b8, expected_b9 in remote.get("rows", []):
            pulses = encode(channel, command, DEVICE_ID_DEFAULT, profile)
            assert _byte(pulses, 56) == expected_b8, f"{remote['label']} ch{channel} {command} b8"
            assert _bits(pulses)[64] == expected_b9, f"{remote['label']} ch{channel} {command} b9"
        for command, expected_b8, expected_b9 in remote.get("cc_rows", []):
            pulses = encode_cc(command, DEVICE_ID_DEFAULT, profile)
            assert _byte(pulses, 56) == expected_b8, f"{remote['label']} CC {command} b8"
            assert _bits(pulses)[64] == expected_b9, f"{remote['label']} CC {command} b9"


# ── channel_flag — zonnescherm (awning) remotes OR an extra 0x80 into b5 ────

def test_encode_applies_channel_flag_to_b5():
    """DeviceProfile.channel_flag must be OR'd into b5 on every channel, and
    feed into the checksum sum like any other part of b5 — this is what
    lets zonnescherm remotes (which set this bit) be re-encoded exactly."""
    plain = DeviceProfile(x_dev=0x71)
    flagged = DeviceProfile(x_dev=0x71, channel_flag=0x80)

    b5_plain = _byte(encode(2, "up", DEVICE_ID_DEFAULT, plain), 32)
    b5_flagged = _byte(encode(2, "up", DEVICE_ID_DEFAULT, flagged), 32)
    assert b5_plain == 0x01  # canonical K2 high byte
    assert b5_flagged == 0x81  # same, with the flag bit OR'd in

    b8_plain = _byte(encode(2, "up", DEVICE_ID_DEFAULT, plain), 56)
    b8_flagged = _byte(encode(2, "up", DEVICE_ID_DEFAULT, flagged), 56)
    assert b8_flagged == (b8_plain + 0x80) % 256  # flag bit feeds the checksum sum


def test_b8_and_b9_against_local_zonnescherm_captures():
    """Full K1+K2+K9 calibration + round-trip, using real zonnescherm-remote
    raw captures kept out of the public repo — see raw_captures.py's
    docstring. Skipped without that (gitignored) file. This exercises the
    exact bug this fix addresses: decode_signal() failing to recognize
    K1/K2/K9 on this remote model, which stalled the calibration wizard."""
    try:
        from .raw_captures import RAW_CAPTURES
    except ImportError:
        pytest.skip("rf_protocols/tests/raw_captures.py not present locally")

    import sys
    from pathlib import Path

    ha_component = Path(__file__).resolve().parents[2] / "custom_components" / "gumax_rf"
    sys.path.insert(0, str(ha_component))
    from _protocol import DeviceProfile as HADeviceProfile
    from _protocol import channel_bytes as ha_channel_bytes
    from _protocol import decode_signal, device_id_from_hex
    from _protocol import encode as ha_encode
    from _protocol import infer_x_dev as ha_infer_x_dev

    zonnescherm_labels = [
        label for label in RAW_CAPTURES if label.startswith("zonnescherm")
    ]
    if not zonnescherm_labels:
        pytest.skip("no zonnescherm-labelled entries in raw_captures.py")

    for label in zonnescherm_labels:
        entries = RAW_CAPTURES[label]
        channels_present = {ch for ch, _cmd in entries}
        if not {1, 2, 9} <= channels_present:
            continue  # e.g. zonnescherm1 only has K1 — not a full triplet

        signals = {}
        for (ch, cmd), raw in entries.items():
            pulses = [int(x) for x in raw.split(",")]
            sig = decode_signal(pulses)
            assert sig is not None, f"{label} K{ch}/{cmd} failed to decode"
            assert sig["channel"] == ch, f"{label} K{ch}/{cmd} channel mismatch"
            assert sig["channel_flag"] == 0x80, f"{label} K{ch}/{cmd} expected channel_flag 0x80"
            signals[ch] = sig

        k1, k2, k9 = signals[1], signals[2], signals[9]
        device_id_hex = k2["device_id"]

        b5, b6, b7 = ha_channel_bytes(2, k2["command"])
        b5 |= k2["channel_flag"]
        x_dev = ha_infer_x_dev(b5 + b6 + b7, k2["checksum"])
        b5, b6, b7 = ha_channel_bytes(1, k1["command"])
        b5 |= k1["channel_flag"]
        k1_extra = ha_infer_x_dev(b5 + b6 + b7, k1["checksum"], hint=x_dev) - x_dev
        b5, b6, b7 = ha_channel_bytes(9, k9["command"])
        b5 |= k9["channel_flag"]
        k9_extra = ha_infer_x_dev(b5 + b6 + b7, k9["checksum"], hint=x_dev) - x_dev

        profile = HADeviceProfile(
            x_dev=x_dev,
            k1_extra=k1_extra,
            k9_extra=k9_extra,
            b9_default=k2["b9"],
            b9_k1=k1["b9"],
            b9_k9=k9["b9"],
            channel_flag=k2["channel_flag"],
        )
        device_id_bin = device_id_from_hex(device_id_hex)
        for ch in (1, 2, 9):
            sig = signals[ch]
            ref_pulses = ha_encode(ch, sig["command"], device_id_bin, profile)
            ref_signal = decode_signal(ref_pulses)
            assert ref_signal is not None, f"{label} K{ch} re-encode failed to decode"
            assert ref_signal["checksum"] == sig["checksum"], f"{label} K{ch} re-encode checksum mismatch"
            assert ref_signal["b9"] == sig["b9"], f"{label} K{ch} re-encode b9 mismatch"


# ── infer_x_dev — inverts the formula used to calibrate a new remote ─────────
# Synthetic values on purpose (not tied to any real remote) — infer_x_dev is
# pure math, it doesn't need real capture data to be exercised correctly.

def test_infer_x_dev_round_trip_normal_channel():
    b5, b6, b7 = channel_bytes(7, "down")
    synthetic_x_dev = 0x2A
    checksum = _byte(encode(7, "down", DEVICE_ID_DEFAULT, DeviceProfile(x_dev=synthetic_x_dev)), 56)
    assert infer_x_dev(b5 + b6 + b7, checksum) == synthetic_x_dev


def test_infer_x_dev_round_trip_k1_includes_extra():
    b5, b6, b7 = channel_bytes(1, "down")
    synthetic_x_dev, synthetic_extra = 0x2A, 3
    profile = DeviceProfile(x_dev=synthetic_x_dev, k1_extra=synthetic_extra)
    checksum = _byte(encode(1, "down", DEVICE_ID_DEFAULT, profile), 56)
    assert (
        infer_x_dev(b5 + b6 + b7, checksum, hint=synthetic_x_dev)
        == synthetic_x_dev + synthetic_extra
    )


def test_infer_x_dev_ambiguous_without_hint_needs_a_hint_for_k1():
    """K1's channel bit can make both wrap branches valid; without a hint the
    no-overflow solution is preferred, which isn't always the real one — this
    is why config_flow always passes hint=x_dev when solving for K1/K9."""
    b5, b6, b7 = channel_bytes(1, "down")
    synthetic_combined = 200  # large enough to overflow when summed with K1's bytes
    checksum = _byte(encode(1, "down", DEVICE_ID_DEFAULT, DeviceProfile(x_dev=synthetic_combined)), 56)
    assert infer_x_dev(b5 + b6 + b7, checksum) != synthetic_combined
    assert infer_x_dev(b5 + b6 + b7, checksum, hint=synthetic_combined) == synthetic_combined


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
    assert encode(1, "up", DEVICE_ID_DEFAULT, REMOTE_1)[PREAMBLE_LEN:] == _K1_UP_DATA


def test_k9_up_matches_yaml_capture():
    assert encode(9, "up", DEVICE_ID_DEFAULT, REMOTE_1)[PREAMBLE_LEN:] == _K9_UP_DATA


# ── CC broadcast — computed via the same formula, verified per remote ────────
# (additional remotes' CC rows are covered by test_b8_and_b9_against_local_captures)

@pytest.mark.parametrize("command,expected_b8,expected_b9", [
    ("up",   0xD8, 0),
    ("stop", 0xE4, 0),
    ("down", 0xF4, 0),
])
def test_cc_against_reference_remote(command, expected_b8, expected_b9):
    pulses = encode_cc(command, DEVICE_ID_DEFAULT, REMOTE_1)
    assert _byte(pulses, 56) == expected_b8
    assert _bits(pulses)[64] == expected_b9


def test_encode_cc_length():
    assert len(encode_cc("up", DEVICE_ID_DEFAULT, REMOTE_1)) == EXPECTED_PULSES


def test_encode_cc_rejects_bad_command():
    with pytest.raises(ValueError):
        encode_cc("left", DEVICE_ID_DEFAULT, REMOTE_1)


def test_encode_cc_rejects_bad_device_id():
    with pytest.raises(ValueError):
        encode_cc("up", "not-binary", REMOTE_1)
