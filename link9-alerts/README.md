# link9-alerts

Hourly email when a LoRa APRS station is heard **directly** by a **Bay Area
iGate** from **more than 50 km** away, using the RF reception logs on
lora.link9.net (lora-aprs.live). Meant to run on the Mac mini.

## What counts

- **Bay Area iGate:** the receiving iGate's position is inside lat 36.85–38.60,
  lon −123.20 to −121.20 (`BBOX` in the script). That covers the Bay Area down
  through Gilroy and Henry W. Coe. iGate positions are learned from their own
  beacons in link9 data and cached, so new iGates are picked up automatically.
- **Directly:** no `*` (used digipeater) anywhere in the packet's path. link9's
  own "direct" label isn't used: it marks some digipeated packets as direct.
- **More than 50 km:** link9's distance between the iGate and the station's
  reported position.
- Only RF receptions that iGates log to link9; nothing from APRS-IS.

## How it works

1. `/api/map?time_period=180` to learn iGate positions (it keeps only the latest
   record per station, so it isn't used for receptions).
2. `/api/messages?rx_callsign=<iGate>&limit=100` for each Bay Area iGate
   (about 20 s in total for 9 iGates).
3. Keeps receptions from the last 120 minutes that pass the rules above and
   haven't been handled before.
4. One summary email per run, at most one mention per station every 6 hours.

link9 shows receptions about 12 minutes after they happen, so an alert can
arrive up to roughly 75 minutes after the packet was heard.

## Install on the Mac

1. Make a Gmail app password: Google Account → Security → 2-Step Verification →
   App passwords (name it "link9-alerts"). Keep it handy.
2. In Terminal:
   ```zsh
   git clone git@github.com:radiohound/balloon-chase-station.git   # or git pull
   cd balloon-chase-station/link9-alerts
   python3 link9_far_alert.py --dry-run          # check it runs; sends nothing
   ./install-mac.sh                              # stores the app password in Keychain, starts hourly job
   ```
   The password goes straight into the macOS Keychain (service
   `link9-alerts`); it isn't stored in any file.

Log: `~/Library/Logs/link9-alerts.log`. State (iGate positions, what was
already reported): `~/.link9-alerts.json`.

## Options

```
--km 50          distance threshold
--minutes 120    how far back each run looks
--email ADDR     sender and recipient (Gmail)
--dry-run        print the email instead of sending
```

Test the email format without waiting for a real 50 km reception:

```zsh
python3 link9_far_alert.py --dry-run --km 15 --minutes 360 --state /tmp/test.json
```

## Remove

```zsh
./install-mac.sh --uninstall
security delete-generic-password -s link9-alerts   # optional: remove the app password
```
