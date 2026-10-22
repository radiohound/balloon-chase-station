# Balloon-Tracker-pi5: ka9q-radio build notes

The balloon-tracker Pi 5 at the K6ATV-10 site runs **ka9q-radio** (`radiod`)
with an Airspy R2, feeding **radiosonde_auto_rx** in KA9Q mode.
`install-ka9q.sh` in this repo is for the Pi 4 chase station and was not used
here.

| Item | Value |
|---|---|
| radiod service | `radiod@airspy-autorx` (`/etc/radio/radiod@airspy-autorx.conf`) |
| auto_rx service | `auto_rx` (`~/sondes/radiosonde_auto_rx/auto_rx`, `station.cfg` sdr_type KA9Q, `sonde.local`) |
| ka9q-radio commit | `5dd4a75` "fix memory leak in set_filter" (2026-07-29) |
| Build folder | `~/ka9q-radio-new` |
| Previous build | Apr 29 2026, kept as `*.apr29` (see Roll back) |

## Why it was upgraded (2026-10-21)

The Apr 29 build leaked memory: radiod had grown to about 5 GB (roughly 300 MB
a day), and the kernel's out-of-memory killer stopped it on 2026-10-02. Commit
`5dd4a75` fixes a leak in `set_filter`. It's pinned there on purpose: later
commits (Sep–Oct 2026) include large refactors (huge pages, futex sync) that
weren't needed and carry more risk.

## Build (no sudo, doesn't touch the running system)

```bash
git clone https://github.com/ka9q/ka9q-radio.git ~/ka9q-radio-new
cd ~/ka9q-radio-new && git checkout 5dd4a75
make -j4 ENABLE_BLADERF=0 ENABLE_FOBOS=0 ENABLE_HACKRF=0 ENABLE_HYDRASDR=0 \
         ENABLE_RX888=0 ENABLE_FUNCUBE=0 ENABLE_RTLSDR=0
ls -l src/radiod src/airspy.so
```

The top-level Makefile turns on every SDR driver by default. Without the
`ENABLE_*=0` flags, the build stops at `bladerf.c` because libbladeRF isn't
installed.

## Install (only the two files that matter)

`sudo make install` was **not** used. It also reinstalls systemd units, udev
rules, sysctl files and example configs in `/etc/radio`. Only radiod and its
Airspy driver plug-in were swapped:

```bash
cd ~/ka9q-radio-new
sudo systemctl stop radiod@airspy-autorx
sudo cp /usr/local/sbin/radiod /usr/local/sbin/radiod.apr29
sudo cp /usr/local/lib/ka9q-radio/airspy.so /usr/local/lib/ka9q-radio/airspy.so.apr29
sudo install -m 0755 src/radiod /usr/local/sbin/radiod
sudo install -m 0755 src/airspy.so /usr/local/lib/ka9q-radio/airspy.so
sudo systemctl start radiod@airspy-autorx
sudo systemctl start auto_rx          # see "Restarting" below
```

The other driver plug-ins in `/usr/local/lib/ka9q-radio/` are still the Apr 29
versions. They aren't used.

## Roll back

```bash
sudo cp /usr/local/sbin/radiod.apr29 /usr/local/sbin/radiod
sudo cp /usr/local/lib/ka9q-radio/airspy.so.apr29 /usr/local/lib/ka9q-radio/airspy.so
sudo systemctl restart radiod@airspy-autorx
```

## Restarting

`auto_rx.service` has `Requires=radiod@airspy-autorx.service`:

- `sudo systemctl restart radiod@airspy-autorx` restarts both. **Use this.**
- `stop` on radiod also stops auto_rx, and a later `start` of radiod does
  **not** bring auto_rx back. Start it again by hand with
  `sudo systemctl start auto_rx`.
- auto_rx's `Restart=on-failure` only covers it crashing on its own.

## Checks

```bash
ps -o rss=,etimes=,pcpu= -C radiod          # memory (kB) should stay roughly flat
journalctl -u radiod@airspy-autorx -n 30 --no-pager
journalctl -u auto_rx -n 15 --no-pager      # "Running frequency scan" every ~20 s
ls -lt ~/sondes/radiosonde_auto_rx/auto_rx/log/ | head
```

radiod logs that the FFTW wisdom files are missing (`/etc/fftw/wisdomf`,
`/var/lib/ka9q-radio/wisdom*`). That was true before the upgrade too. They're
optional, and without them FFT planning just uses a bit more CPU.

## Change log

- 2026-10-21: Upgraded radiod and airspy.so from the Apr 29 2026 build to
  `5dd4a75` (memory-leak fix). auto_rx was down 20:08–20:15 PDT because of
  the stop/start described above.
