# Gumax RF Protocol Specification

## Hardware
- Frequency: 433.92 MHz
- Modulation: OOK (On-Off Keying)
- Hardware: ESP32 + CC1101
- Captures recorded with: ESP32 + CC1101 + RFLink32

## Packet structure

Each packet consists of:
1. **Preamble**: 7 pairs of ~262 µs high + ~612 µs low
2. **Sync gap**: ~5000 µs high
3. **Data**: 65 bits

### Data layout (65 bits)

| Bits  | Field | Description |
|-------|-------|-------------|
| 1–32  | Device ID | 32-bit value identifying the remote (e.g. `10100001101100101100001111010100` = 0xA1B2C3D4) |
| 33–40 | b5 | High byte of channel value (cv >> 8) |
| 41–48 | b6 | Low byte of channel value (cv & 0xFF) |
| 49–56 | b7 | Command byte |
| 57–64 | b8 | Checksum |
| 65    | b9 | Parity bit |

## Channel values (cv)

```
K1   cv=0x0080  b5=0x00  b6=0x80
K2   cv=0x0100  b5=0x01  b6=0x00
K3   cv=0x0200  b5=0x02  b6=0x00
K4   cv=0x0400  b5=0x04  b6=0x00
K5   cv=0x0800  b5=0x08  b6=0x00
K6   cv=0x1000  b5=0x10  b6=0x00
K7   cv=0x2000  b5=0x20  b6=0x00
K8   cv=0x4000  b5=0x40  b6=0x00
K9   cv=0x0000  b5=0x00  b6=0x00  (broadcast group 2)
K10  cv=0x0001  b5=0x00  b6=0x01
K11  cv=0x0002  b5=0x00  b6=0x02
K12  cv=0x0004  b5=0x00  b6=0x04
K13  cv=0x0008  b5=0x00  b6=0x08
K14  cv=0x0010  b5=0x00  b6=0x10
K15  cv=0x0020  b5=0x00  b6=0x20
K16  cv=0x0040  b5=0x00  b6=0x40
CC   cv=0x7FFF  b5=0x7F  b6=0xFF  (broadcast all channels, via capture)
```

Bit pattern: a single bit shifts from position 7 (K1, in b6) through positions 8–14 (K2–K8, in b5) to positions 0–6 (K10–K16, in b6). K9 has no bit.

## Command byte (b7)

| Command | b7 value |
|---------|----------|
| Up      | 5 (0x05)  |
| Down    | 33 (0x21) |
| Stop    | 17 (0x11) |

**Special rule**: K9 (cv=0x0000) has bit 7 of b7 set:
- K9 up:   b7 = 5  | 0x80 = 133
- K9 down: b7 = 33 | 0x80 = 161
- K9 stop: b7 = 17 | 0x80 = 145

Reason: cv=0x0000 has no channel bit; bit 7 of b7 identifies K9 as a valid channel.

## Checksum (b8)

**b8 depends on the specific remote, not just on channel/command.** Every
remote transmits with a per-device additive constant (`x_dev`) baked into
the chip; it cannot be derived from `device_id` — it must be learned from a
live capture. (An earlier version of this spec treated `217` as a universal
constant; it was actually `x_dev` for one specific remote, discovered only
once a second physical remote's captures stopped matching the formula.)

```python
def wrap(total):
    return (total % 256) ^ 0x80 if total >= 256 else total

def b8(x_dev, b5, b6, b7):
    """Checksum for a 'normal' channel (not K1/K9)."""
    return wrap(x_dev + b5 + b6 + b7)

def b8_cc(x_dev, b6, b7):
    """CC excludes b5 — it's a wildcard sentinel (0x7F), not a real channel value."""
    return wrap(x_dev + b6 + b7)
```

**K1 and K9 sometimes need a small additive correction on top of `x_dev`**
(`x_dev + k1_extra` / `x_dev + k9_extra`). Some remotes need it (seen: +1),
others don't (seen: +0) — this is per-remote and must be learned per remote,
same as `x_dev` itself.

`x_dev` (and k1_extra/k9_extra) are learned by inverting the formula against
one real capture per channel: `x_dev = wrap⁻¹(b8 − b5 − b6 − b7)`. The
inversion can have two valid solutions (the overflow either did or didn't
trigger) — resolved by preferring the no-overflow solution when deriving
`x_dev` itself, or the solution closest to the already-known `x_dev` when
deriving k1_extra/k9_extra (see `infer_x_dev()` in `gumax.py`).

### x_dev observed on real remotes

Three physical remotes have been captured and decoded; each had a different
`x_dev` (one of them, the reference remote used for the encoder tests below,
happens to be `0xD4`). Real device_id/x_dev/checksum values are deliberately
**not** published here or in the public test suite — they're derived from
real users' physical remotes and, even though no formula links x_dev back to
device_id (see below), there's no reason to publish someone else's hardware
identifiers. Contributors with access to real captures can drop them in
`rf_protocols/tests/local_captures.py` (gitignored) to exercise the same
test cases locally — see that file's docstring for the expected format.

No formula relating `device_id` to `x_dev` has been found (tried: byte sums,
XOR, bit-reversed variants, running checksums, with/without the 217/0xD9
constant — none matched across all three remotes). With only three data
points and no chip datasheet, further reverse-engineering is unlikely to be
productive; treat `x_dev` as opaque and always learn it from a capture.

## Parity bit (b9)

**No formula found.** b9 is constant per channel within one remote (all of
up/down/stop on the same channel give the same b9), but which channels get
0 vs 1 differs per remote — and not in a way that maps cleanly onto a
per-channel table. Of the three remotes captured, one was the exact inverse
of the other two on every channel tested (K1, K7, K12, CC) — suggestive of
"one base pattern, flipped per remote by a single bit," but that's a
hypothesis from 3 data points, not a proven rule, and it hasn't been checked
on any channel other than K1/K7/K12/CC. Channels K2-K6, K8, K10, K11,
K13-K16 have never been captured on any remote. See `local_captures.py`
(same note as above) for the actual per-channel values behind this finding.

Practical handling (see `DeviceProfile` in `gumax.py`): store the directly
observed b9 for K1 and K9 per remote (`b9_k1`, `b9_k9`), and use the value
observed on any other captured "normal" channel (e.g. K2) as `b9_default`
for every other channel, including CC. This is unverified for untested
channels, but matches everything measured so far (K7/K12/CC always agree
within a remote).

## Pulse encoding

**Rule**: the SPACE (negative pulse) determines the bit value. The MARK (positive pulse) is determined by the next bit.

```python
PREAMBLE = [262, -612, 269, -610, 268, -622, 269, -610,
            262, -610, 265, -613, 268, -611, 4998]

def encode(bits):
    pulses = list(PREAMBLE)
    for i, bit in enumerate(bits):
        space = -600 if bit == '0' else -280   # long=0, short=1
        mark  =  600 if i+1 < len(bits) and bits[i+1] == '1' else 280
        pulses.append(space)
        pulses.append(mark)
    return pulses
```

Timings:
- Long space:  ~600 µs (bit = 0)
- Short space: ~280 µs (bit = 1)
- Long mark:   ~600 µs (next bit = 1)
- Short mark:  ~280 µs (next bit = 0 or last bit)

## Complete encoder (Python)

See `encode()`/`encode_cc()`/`DeviceProfile` in `rf_protocols/protocols/gumax.py`
(kept in sync with `custom_components/gumax_rf/_protocol.py`) for the actual
implementation. Sketch:

```python
PREAMBLE = [262, -612, 269, -610, 268, -622, 269, -610,
            262, -610, 265, -613, 268, -611, 4998]

DEVICE_ID = device_id_from_hex("A1B2C3D4")  # replace with your device ID

CH_VALS = {
    1: 0x0080, 2: 0x0100, 3: 0x0200, 4: 0x0400,
    5: 0x0800, 6: 0x1000, 7: 0x2000, 8: 0x4000,
    9: 0x0000, 10: 0x0001, 11: 0x0002, 12: 0x0004,
    13: 0x0008, 14: 0x0010, 15: 0x0020, 16: 0x0040,
}

CMD_B7 = {'up': 5, 'down': 33, 'stop': 17}


def wrap(total):
    return (total % 256) ^ 0x80 if total >= 256 else total


def make_command(channel, command, profile):
    """profile = DeviceProfile(x_dev, k1_extra, k9_extra, b9_default, b9_k1, b9_k9),
    learned from a live capture of THIS remote — see the config flow's
    capture_k1/k2/k9 steps."""
    cv = CH_VALS[channel]
    b5 = (cv >> 8) & 0xFF
    b6 = cv & 0xFF
    b7 = CMD_B7[command] | (0x80 if channel == 9 else 0)

    if channel == 1:
        x_dev, b9 = profile.x_dev + profile.k1_extra, profile.b9_k1
    elif channel == 9:
        x_dev, b9 = profile.x_dev + profile.k9_extra, profile.b9_k9
    else:
        x_dev, b9 = profile.x_dev, profile.b9_default

    bits = (DEVICE_ID
            + format(b5, '08b')
            + format(b6, '08b')
            + format(b7, '08b')
            + format(wrap(x_dev + b5 + b6 + b7), '08b')
            + str(b9))

    pulses = list(PREAMBLE)
    for i, bit in enumerate(bits):
        space = -600 if bit == '0' else -280
        mark  =  600 if i + 1 < len(bits) and bits[i + 1] == '1' else 280
        pulses.append(space)
        pulses.append(mark)

    return pulses
```

## Generic device ID

The device ID is a 32-bit value that identifies a remote. The motors are
paired to a specific ID via their learn mode — see the motor manual for
pairing instructions. Unlike b8/b9, `device_id` alone is not enough to
control a motor: the checksum also needs that remote's `x_dev` (see above),
so a brand new/invented device_id cannot be used without first calibrating
it against a real capture from a matching remote.

## CC (broadcast all channels)

CC uses the same formula as any other channel, just with b5 excluded from
the checksum sum (CV=0x7FFF is a wildcard sentinel, not a real per-channel
value — including it in the sum never matched any real capture).

| Field | Value |
|-------|-------|
| b5 | 0x7F |
| b6 | 0xFF |
| b7 | CMD_B7[command] \| 0x80 (bit 7 set — same rule as K9) |
| b8 | wrap(x_dev + b6 + b7) |
| b9 | profile.b9_default (same value as the remote's other "normal" channels) |

An earlier version of this spec described CC as needing stored/hardcoded
captures because it "didn't follow the formula" — that was because the
formula being compared against was per-channel/command only, with no
device-specific term and no b5-exclusion rule. Both were wrong, not CC.

## Verified captures

The channel/command table under "Checksum" above was cross-validated
against a working ESPHome YAML reference implementation for one specific
remote (its `x_dev` happens to equal `0xD4`).

Separately, three physical remotes had their CC/K1/K7/K12 channels captured
and decoded directly — 36/36 checksums matched the `x_dev`-based formula
above (device IDs deliberately omitted, see "Checksum" section). K2-K6, K8,
K10, K11, K13-K16 and K9 have not been captured on any of the three remotes
and their b9 in particular should be treated as unverified per remote (see
"Parity bit").
