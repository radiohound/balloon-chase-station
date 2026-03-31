# balloon-chase-station

Raspberry Pi based balloon chase and ground station for high-altitude balloon (HAB) recovery.

## Pipelines

| Pipeline | Frequency | Software | Status |
|---|---|---|---|
| LoRa APRS | 433.775 MHz | Dire Wolf + gr-lora_sdr | working |
| Horus Binary | 431.050 MHz | horus-gui / horusdemodlib | planned |
| Wenet SSTV | 434.000 MHz | wenet | planned |

## SDR Architecture

All RF is received by an **Airspy R2** and shared via **ka9q-radio** (`radiod`)
over IP multicast.  Each pipeline subscribes to its own channel via `pcmrecord`.

```
Airspy R2
    └── radiod (ka9q-radio)
            ├── horus-iq.local    431.050 MHz  48 ksps   → horusdemodlib
            ├── lora-iq.local     433.775 MHz  1 Msps    → lora_ka9q_bridge.py → Dire Wolf
            └── wenet-iq.local    434.000 MHz  500 ksps  → wenet
```

For LoRa APRS **transmit**, a MeshAdv Pi Hat (Ebyte E22-400M30S, SX1262, 1W PA)
is connected via SPI and driven natively by Dire Wolf (`LCHANNEL`).

## Files

| File | Description |
|---|---|
| `install-ka9q.sh` | Build and install ka9q-radio from source |
| `config/lora.conf` | LoRa RF parameters for lora_sdr_bridge / lora_ka9q_bridge |
| `lora-aprs/lora_ka9q_bridge.py` | ka9q-radio IQ → gr-lora_sdr → KISS TCP → Dire Wolf |
| `lora-aprs/lora_ka9q_flowgraph.py` | GNU Radio flowgraph (stdin IQ → LoRa demod) |
| `lora-aprs/lora_sdr_bridge.py` | RTL-SDR direct IQ → gr-lora_sdr → KISS TCP → Dire Wolf |
| `lora-aprs/lora_sdr_flowgraph.py` | GNU Radio flowgraph (RTL-SDR → LoRa demod) |

## Requirements

- Raspberry Pi 4 (recommended)
- Airspy R2
- ka9q-radio (`install-ka9q.sh`)
- GNU Radio 3.10 with gr-lora_sdr
- Dire Wolf (with LoRa SPI support — see [radiohound/direwolf](https://github.com/radiohound/direwolf))
- MeshAdv Pi Hat or compatible SX1262 module for TX

## Usage

### Start ka9q-radio

```bash
sudo systemctl start radiod@balloon
```

### Start LoRa APRS SDR receive bridge

```bash
pcmrecord -c -r lora-iq.local | python3 lora-aprs/lora_ka9q_bridge.py -c config/lora.conf
```

### Start Dire Wolf

```bash
direwolf -c ~/direwolf.conf
```
