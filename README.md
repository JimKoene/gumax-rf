# Gumax RF Bridge
Control Gumax roller shutters / awnings from Home Assistant via an ESP32 + CC1101 RF module.

---

## Requirements

### Hardware
| Component | Specification |
|---|---|
| Microcontroller | ESP32 DevKit (or compatible) |
| RF module | CC1101 (433.92 MHz) |
| Connection | Jumper wires / breadboard or soldered |

### Software
- [ESPHome](https://esphome.io/) (CLI or HA add-on)
- Home Assistant 2026.1 or newer
- This repository

---

## Step 1 — Wiring ESP32 ↔ CC1101

Connect the CC1101 module to the ESP32 as follows:

| CC1101 pin | ESP32 GPIO | Function |
|---|---|---|
| CLK / SCK | GPIO18 | SPI clock |
| MOSI / SI | GPIO23 | SPI data out |
| MISO / SO | GPIO19 | SPI data in |
| CS / CSN | GPIO5 | SPI chip select |
| GDO0 | GPIO26 | Transmit data |
| GDO2 | GPIO25 | Receive data |
| VCC | 3.3 V | Power |
| GND | GND | Ground |

> **Note:** Use 3.3 V power for the CC1101. The module is **not** 5 V tolerant.

---

## Step 2 — Create ESPHome secrets

Create (or update) the file `secrets.yaml` in your ESPHome configuration directory:

```yaml
wifi_ssid: "YourWifiName"
wifi_password: "YourWifiPassword"
gumax_api_key: "GenerateABase64Key=="   # esphome generate-api-key
gumax_ota_password: "ChooseAnOTAPassword"
gumax_ap_password: "FallbackHotspotPassword"
```

Generate an API key via the ESPHome CLI:
```bash
esphome generate-api-key
```

---

## Step 3 — Flash the ESP32 with ESPHome

Copy `esphome/gumax_rf_bridge.yaml` to your ESPHome configuration directory.

Flash the firmware via USB:
```bash
esphome run esphome/gumax_rf_bridge.yaml
```

Or via the ESPHome add-on in Home Assistant:
1. Go to **ESPHome** → click **+ New device**
2. Import `gumax_rf_bridge.yaml` or paste the contents manually
3. Click **Install** → **Plug into this computer**

After flashing, the ESP32 automatically connects to your Wi-Fi. Check the logs — you should see `[I] (esphome)` messages without errors.

---

## Step 4 — Add the ESP32 to Home Assistant

1. Open Home Assistant
2. Go to **Settings → Devices & services**
3. The ESP32 appears automatically as a discovered integration (**ESPHome — gumax-rf**)
4. Click **Configure** and enter the API key you set in `secrets.yaml`

---

## Step 5 — Install the custom component

### Via HACS (recommended)
1. Install [HACS](https://hacs.xyz/) if you haven't already
2. Go to **HACS → Integrations → ⋮ → Custom repositories**
3. Add the URL of this repository as type **Integration**
4. Search for **Gumax RF** and install
5. Restart Home Assistant

### Manually
Copy the folder `homeassistant/custom_components/gumax_rf/` to your HA configuration directory:

```
<config>/
  custom_components/
    gumax_rf/
      __init__.py
      _protocol.py
      config_flow.py
      const.py
      cover.py
      icon.png
      manifest.json
      strings.json
      translations/
        nl.json
        en.json
```

Then restart Home Assistant.

---

## Step 6 — Configure the Gumax RF integration

1. Go to **Settings → Devices & services → + Add integration**
2. Search for **Gumax RF** and click it
3. Choose a setup method:

| Method | When to use |
|---|---|
| **Learn from remote** | Press a button on your existing Gumax remote — the integration captures the Device ID automatically |
| **Enter manually** | You already know the Device ID (e.g. from a previous setup) |

4. Select the ESPHome node (auto-detected if the CC1101 bridge is online)
5. Click **Submit**

Home Assistant will create **16 channel entities** (K1–K16) plus one **CC (broadcast) entity** for all channels simultaneously.

---

## Viewing raw pulse timings

Each configured entry exposes the raw pulse timings for any channel and command directly from the integration page.

1. Go to **Settings → Devices & services → Gumax RF**
2. Click the cogwheel next to the entry → **Configure**
3. Select a channel (K1–K16 or CC) and click **Next**
4. The raw pulse timings for **up**, **down**, and **stop** are shown on screen and can be selected and copied

This is useful if you want to transmit commands from another tool (e.g. ESPHome `remote_transmitter.transmit_raw`, Node-RED, or a script) without going through the integration.

---

## Device ID

The Device ID is a 32-bit hex value (e.g. `A1B2C3D4`) that identifies a remote. It cannot be read from a sticker — it must be captured from an existing remote using the **Learn** flow, or a new one can be created manually and then paired to the motor (see the motor's manual for pairing instructions).

If you have multiple remotes or systems, add the integration multiple times with a different Device ID for each.

---

## Entities overview

| Entity | Description |
|---|---|
| `cover.gumax_rf_k1` – `cover.gumax_rf_k16` | Individual channels 1–16 |
| `cover.gumax_rf_cc` | Broadcast — controls all paired channels simultaneously |

Every entity supports the **Open**, **Close**, and **Stop** actions.

> **Note:** The position of the shutter is unknown (one-way RF). Entities use `assumed_state`, meaning HA remembers the last sent command as the state, but it also does not block the buttons.

---

## Troubleshooting

### ESP32 not found in HA
- Check that the ESP32 is connected to Wi-Fi (check the ESPHome logs)
- Make sure the API key in HA matches the one in `secrets.yaml`
- Wait 1–2 minutes after the ESP32 boots

### No ESPHome node available in the configuration form
- Check that the ESPHome integration has added the ESP32 correctly
- Restart Home Assistant after installing the custom component

### Shutter does not respond
- Check the wiring (especially GDO0 on GPIO26, GDO2 on GPIO25, and CS on GPIO5)
- Verify the Device ID — a wrong ID means the signal will be ignored by the motor
- Keep the ESP32 and the motor receiver close together during initial tests
- Check the ESPHome logs via **ESPHome → gumax-rf → Logs**

---

## Technical details
- Frequency: 433.92 MHz OOK
- Channels: 1–16 (K1–K16) + CC broadcast
- Each command is transmitted 3× for reliable reception
- Hardware: ESP32 with CC1101 via SPI
- RF framework: ESPHome `remote_transmitter` + `remote_receiver` + `cc1101` external component
