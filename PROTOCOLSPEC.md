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

```python
def b8_up(cv):
    """Base checksum from channel value."""
    raw = 217 + (cv >> 8) + (cv & 0x7F)  # cv & 0x7F masks bit 7 of b6
    return (raw % 256) ^ 0x80 if raw >= 256 else raw

def b8(cv, command):
    """Checksum including command offset."""
    offsets = {'up': 0, 'down': 28, 'stop': 12}
    raw = b8_up(cv) + offsets[command]
    return (raw % 256) ^ 0x80 if raw >= 256 else raw
```

**Rule**: flip bit 7 (XOR 0x80) only on byte overflow (sum >= 256).

### Verified b8 values

| Channel | b8_up | b8_down | b8_stop |
|---------|-------|---------|---------|
| K1      | 217   | 245     | 229     |
| K2      | 218   | 246     | 230     |
| K3      | 219   | 247     | 231     |
| K4      | 221   | 249     | 233     |
| K5      | 225   | 253     | 237     |
| K6      | 233   | 133     | 245     |
| K7      | 249   | 149     | 133     |
| K8      | 153   | 181     | 165     |
| K9      | 217   | —       | 229     |
| K14     | 233   | —       | 245     |
| K15     | 249   | —       | 133     |
| K16     | 153   | —       | 165     |

## Parity bit (b9)

| b9 = 1 | K1, K9 |
| b9 = 0 | all other channels |

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

CH_B9 = {ch: 1 if ch in [1, 9] else 0 for ch in range(1, 17)}

CMD_B7     = {'up': 5, 'down': 33, 'stop': 17}
CMD_OFFSET = {'up': 0, 'down': 28, 'stop': 12}


def b8_up(cv):
    raw = 217 + (cv >> 8) + (cv & 0x7F)
    return (raw % 256) ^ 0x80 if raw >= 256 else raw


def b8(cv, command):
    raw = b8_up(cv) + CMD_OFFSET[command]
    return (raw % 256) ^ 0x80 if raw >= 256 else raw


def make_command(channel, command):
    cv = CH_VALS[channel]
    b5 = (cv >> 8) & 0xFF
    b6 = cv & 0xFF
    b7 = CMD_B7[command] | (0x80 if channel == 9 else 0)
    b9 = CH_B9[channel]

    bits = (DEVICE_ID
            + format(b5, '08b')
            + format(b6, '08b')
            + format(b7, '08b')
            + format(b8(cv, command), '08b')
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

The device ID is an arbitrary 32-bit value that identifies a remote. Any value can be used. The motors are paired to a specific ID via their learn mode — see the motor manual for pairing instructions.

## CC (broadcast all channels)

CC is fully calculated. CV=0x7FFF (b5=0x7F, b6=0xFF).

| Field | Value |
|-------|-------|
| b5 | 0x7F |
| b6 | 0xFF |
| b7 | CMD_B7[command] \| 0x80 (bit 7 set — same rule as K9) |
| b8 | 216 + CMD_OFFSET[command] (up=216, down=244, stop=228) |
| b9 | 0 |

b8 does **not** follow `_checksum()` — the formula yields b8_base=87 (sum 471 → overflow → XOR), but decoded captures show b8_base=**216**. The +28/+12 command offsets still apply. Hardcoded as `_CC_B8_BASE = 216`.

## Verified captures

All K1–K11 captures have been verified (100% match) against the formula.
K12–K16 have been captured but not tested on a motor (not paired).
CC has been decoded and verified: b7/b8/b9 confirmed across all three commands.
