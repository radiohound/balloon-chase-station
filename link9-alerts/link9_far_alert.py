#!/usr/bin/env python3
"""
link9_far_alert.py - hourly check of lora.link9.net (lora-aprs.live) for LoRa APRS
stations heard DIRECTLY by Bay Area iGates from more than 50 km away, and an email
summary when there are new ones.

Data sources (lora.link9.net):
  /api/map?time_period=<minutes>  latest record per station worldwide; used only
                                  to learn iGate positions from their beacons.
  /api/messages?rx_callsign=<iGate>&limit=100
                                  each Bay Area iGate's newest receptions
                                  (about 1 s per iGate). The map can't be used
                                  for this: it keeps one record per station.

"Heard directly" means no '*' (used digipeater) in the packet's path or
destination. link9's own object_type "direct" is not used because it marks some
digipeated packets as direct.

"Bay Area iGate" means the receiving iGate's position is inside BBOX. iGate
positions come from the iGates' own beacons in the link9 data and are cached in
STATE_FILE, so iGates that beacon rarely are still recognised.

Email: Gmail SMTP with an app password read from the macOS Keychain
(security find-generic-password -s link9-alerts -a <gmail address> -w).
Use --dry-run to print instead of sending.

Standard library only; downloads with curl (uses the system's certificates).
"""

import argparse
import datetime as dt
import email.message
import json
import os
import smtplib
import subprocess
import sys

MAP_API = "https://lora.link9.net/api/map?time_period=%d"
MSG_API = "https://lora.link9.net/api/messages?rx_callsign=%s&limit=100"
BBOX = (36.85, 38.60, -123.20, -121.20)   # lat_min, lat_max, lon_min, lon_max
SEED_IGATES = {                            # positions used until link9 data provides them
    "K6ATV-10": (37.01933, -121.61800),
}
REPEAT_HOURS = 6


def fetch(url):
    out = subprocess.run(["curl", "-sS", "-m", "60", url], capture_output=True, check=True).stdout
    return json.loads(out)


def is_direct(r):
    return "*" not in (r.get("path") or "") and "*" not in (r.get("destination") or "")


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def in_bbox(pos):
    lat, lon = pos
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def load_state(path):
    try:
        with open(path) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.setdefault("igates", {})
    s.setdefault("reported", {})   # tx_callsign -> ISO time last emailed
    s.setdefault("seen", [])       # keys of receptions already handled
    for k, v in SEED_IGATES.items():
        s["igates"].setdefault(k, list(v))
    return s


def save_state(path, s):
    s["seen"] = s["seen"][-5000:]
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, path)


def learn_igates(map_records, state):
    """Record iGate positions from their own beacons; return Bay Area iGates."""
    for r in map_records:
        if r.get("object_class") == "igate" and r.get("tx_callsign") == r.get("rx_callsign"):
            lat, lon = fnum(r.get("latitude")), fnum(r.get("longitude"))
            if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
                state["igates"][r["tx_callsign"]] = [lat, lon]
    return sorted(k for k, v in state["igates"].items() if in_bbox(v))


def recent(records, minutes, now):
    cutoff = now - dt.timedelta(minutes=minutes)
    out = []
    for r in records:
        try:
            if dt.datetime.fromisoformat(r.get("received_at")) >= cutoff:
                out.append(r)
        except (TypeError, ValueError):
            pass
    return out


def find_far(records, state, min_km):
    seen = set(state["seen"])
    hits = []
    for r in records:
        rx, tx = r.get("rx_callsign") or r.get("rx_igate"), r.get("tx_callsign")
        dist = fnum(r.get("distance"))
        if not rx or not tx or rx == tx or dist is None or dist <= min_km or not is_direct(r):
            continue
        key = "%s|%s|%s" % (r.get("received_at"), rx, tx)
        if key in seen:
            continue
        seen.add(key)
        state["seen"].append(key)
        hits.append(r)
    return hits


def new_stations(hits, state, now):
    out = {}
    for r in sorted(hits, key=lambda r: fnum(r.get("distance")) or 0, reverse=True):
        tx = r["tx_callsign"]
        last = state["reported"].get(tx)
        if last and now - dt.datetime.fromisoformat(last) < dt.timedelta(hours=REPEAT_HOURS):
            continue
        out.setdefault(tx, []).append(r)
    return out


def local(ts):
    try:
        return dt.datetime.fromisoformat(ts).astimezone().strftime("%m/%d %I:%M %p")
    except (TypeError, ValueError):
        return ts or "?"


def build_email(stations, min_km):
    lines = ["LoRa APRS stations heard directly by Bay Area iGates from more than %d km:" % min_km, ""]
    for tx, rs in stations.items():
        best = rs[0]
        lines.append("%s - %.1f km, heard by %s at %s (%s dBm, SNR %s dB)%s" % (
            tx, fnum(best.get("distance")), best.get("rx_callsign") or best.get("rx_igate"),
            local(best.get("received_at")), best.get("signal_strength"), best.get("signal_quality"),
            "" if len(rs) == 1 else "; %d receptions in all" % len(rs)))
        lines.append("    https://lora.link9.net/api/messages?tx_callsign=%s&limit=20" % tx)
    lines += ["", "Times are lora-aprs.live 'Seen' times, which run about 12 minutes behind the actual reception."]
    subject = "LoRa APRS: %s heard from %d+ km" % (", ".join(list(stations)[:3]) + (" +" if len(stations) > 3 else ""), min_km)
    return subject, "\n".join(lines)


def send(subject, body, addr, dry_run):
    if dry_run:
        print("Subject: " + subject + "\n\n" + body)
        return
    pw = subprocess.run(["security", "find-generic-password", "-s", "link9-alerts", "-a", addr, "-w"],
                        capture_output=True, text=True, check=True).stdout.strip()
    msg = email.message.EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = addr, addr, subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(addr, pw)
        s.send_message(msg)


def main():
    ap = argparse.ArgumentParser(description="Email when Bay Area iGates hear far stations directly.")
    ap.add_argument("--email", default="radiohound@gmail.com")
    ap.add_argument("--km", type=float, default=50)
    ap.add_argument("--minutes", type=int, default=120,
                    help="receptions this recent are checked (overlaps the hourly run; repeats are skipped)")
    ap.add_argument("--state", default=os.path.expanduser("~/.link9-alerts.json"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    state = load_state(a.state)
    now = dt.datetime.now(dt.timezone.utc)
    try:
        igates = learn_igates(fetch(MAP_API % 180), state)
    except (subprocess.CalledProcessError, ValueError) as e:
        print("map fetch failed (%s); using cached iGate list" % e)
        igates = sorted(k for k, v in state["igates"].items() if in_bbox(v))

    records = []
    for ig in igates:
        try:
            records += recent(fetch(MSG_API % ig).get("results", []), a.minutes, now)
        except (subprocess.CalledProcessError, ValueError, AttributeError) as e:
            print("skip %s: %s" % (ig, e))
    hits = find_far(records, state, a.km)
    stations = new_stations(hits, state, now)
    print("%s: %d Bay Area iGates, %d recent receptions, %d far direct, %d stations to report" % (
        now.isoformat(timespec="seconds"), len(igates), len(records), len(hits), len(stations)))
    if stations:
        subject, body = build_email(stations, int(a.km))
        send(subject, body, a.email, a.dry_run)
        if not a.dry_run:
            for tx in stations:
                state["reported"][tx] = now.isoformat()
    save_state(a.state, state)


if __name__ == "__main__":
    sys.exit(main())
