# link9-bridge

Forwards every LoRa APRS packet that Direwolf's `loraspi` driver receives on
K6ATV-10 to **lora.link9.net** (the backend of [lora-aprs.live](https://lora-aprs.live)),
with RSSI and SNR, the same way a CA2RXU ESP32 iGate does. It also keeps a
local CSV of every reception.

It runs alongside Direwolf and changes nothing in Direwolf, `direwolf.conf`
or `direwolf-chase.service`.

## How it works

1. Direwolf (`direwolf-chase.service`) prints each received LoRa packet to the
   systemd journal as `<packet>  RSSI=<n> dBm  SNR=<x> dB`
   (`src/loraspi.c` in radiohound/direwolf, branch `feature/lora-spi`).
2. `link9_bridge.py` follows that journal with `journalctl -f -o json`.
3. Each reception is rebuilt in CA2RXU's syslog layout
   (`syslog_utils.cpp` + `APRSPacketLib` decoder, CA2RXU firmware 4.0.4) and sent
   by UDP to `lora.link9.net:1514`.
4. Each reception is also appended to `~/dw-logs/lora-rx.csv`
   (UTC time, sender, RSSI, SNR, lat, lon, raw packet).

## Limits

- Packets Direwolf can't parse (for example SSIDs above 15, such as K6ATV-22)
  are logged by Direwolf without RSSI/SNR, so they are not forwarded.
- The SX1262 driver doesn't report frequency error; `0 Hz` is sent.
- This receiver is set to SF12 only (433.775 MHz, BW125, CR4/5, sync 0x12),
  so SF7 traffic is never heard here.
- Packets heard while the bridge is restarting (about 10 s) are not sent.
- The syslog APP-NAME is `Direwolf_loraspi_link9_bridge_1.0`, not CA2RXU's.
  If link9 ignores these lines, `--app-name` can be changed.

## Install (on the Pi)

```bash
cd ~/balloon-chase-station
sudo cp link9-bridge/link9-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now link9-bridge
journalctl -u link9-bridge -f        # watch "sent:" lines
```

## Test without sending anything

```bash
journalctl -u direwolf-chase -o cat | \
  python3 link9-bridge/link9_bridge.py --stdin --dry-run \
  --callsign K6ATV-10 --lat 37.01933 --lon -121.61800
```

## Check it arrived on link9

`https://lora.link9.net/api/messages?rx_callsign=K6ATV-10&limit=20`

## Remove

```bash
sudo systemctl disable --now link9-bridge
sudo rm /etc/systemd/system/link9-bridge.service
sudo systemctl daemon-reload
```

## Change log

- 2026-10-16: Initial version.
