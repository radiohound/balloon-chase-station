#!/usr/bin/env python3
"""
link9_bridge.py - forward LoRa APRS packets heard by Direwolf (loraspi) to
lora.link9.net (lora-aprs.live) as CA2RXU-style syslog lines.

How it works
------------
Direwolf's loraspi driver prints every LoRa packet it receives and parses as:

    <TNC2 packet>  RSSI=<int> dBm  SNR=<float> dB

That output goes to the systemd journal of the Direwolf service. This script
follows that journal, picks out those lines, and sends each one to link9 over
UDP syslog (port 1514) in the same layout the CA2RXU LoRa APRS iGate firmware
uses (src/syslog_utils.cpp + APRSPacketLib decoder), because link9 does not
publish its own format and is built around CA2RXU's.

It does not touch Direwolf, direwolf.conf or the Direwolf service.

Known limits
------------
* Packets Direwolf rejects (e.g. "loraspi: failed to parse", such as SSIDs
  above 15) are printed without RSSI/SNR, so they cannot be forwarded.
* The SX1262 driver does not report frequency error. CA2RXU always sends
  one, so this script sends --freq-error (default 0).
* Only receptions are forwarded (no TX / beacon / CRC-error lines).

Standard library only. Python 3.7+.
"""

import argparse
import csv
import datetime
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys

VERSION = "1.0"

RX_LINE = re.compile(r"^(?P<pkt>.+?)  RSSI=(?P<rssi>-?\d+) dBm  SNR=(?P<snr>-?\d+(?:\.\d+)?) dB\s*$")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ---------------------------------------------------------------------------
# Small helpers that mimic Arduino String behaviour used by CA2RXU
# ---------------------------------------------------------------------------

def _to_float(s):
    """Arduino String.toFloat(): leading numeric part, 0 if none."""
    m = re.match(r"\s*[-+]?\d*\.?\d+", s)
    try:
        return float(m.group(0)) if m else 0.0
    except ValueError:
        return 0.0


def _to_int(s):
    """Arduino String.toInt(): leading integer part, 0 if none."""
    m = re.match(r"\s*[-+]?\d+", s)
    return int(m.group(0)) if m else 0


def _ch(s, i):
    """Arduino String operator[]: returns '\\0' when out of range."""
    return s[i] if 0 <= i < len(s) else "\0"


# ---------------------------------------------------------------------------
# Coordinate decoders (ported from APRSPacketLib)
# ---------------------------------------------------------------------------

def decode_latitude(s):
    dot = s.find(".")
    v = _to_float(s[0:2]) + _to_float(s[2:4]) / 60
    v += _to_float(s[dot + 1:dot + 3]) / 6000 if dot >= 0 else _to_float(s[0:2]) / 6000
    return -v if s.endswith("S") else v


def decode_longitude(s):
    dot = s.find(".")
    v = _to_float(s[0:3]) + _to_float(s[3:5]) / 60
    v += _to_float(s[dot + 1:dot + 3]) / 6000 if dot >= 0 else _to_float(s[0:2]) / 6000
    return -v if s.endswith("W") else v


def decode_b91_lat(s):
    y = [ord(_ch(s, i)) - 33 for i in range(4)]
    return 90.0 - ((y[0] * 91 ** 3) + (y[1] * 91 ** 2) + (y[2] * 91) + y[3]) / 380926.0


def decode_b91_lon(s):
    x = [ord(_ch(s, i)) - 33 for i in range(4)]
    return -180.0 + ((x[0] * 91 ** 3) + (x[1] * 91 ** 2) + (x[2] * 91) + x[3]) / 190463.0


def _deg_to_dec_lat(s):
    v = _to_int(s[0:2]) + _to_int(s[2:4]) / 60.0 + _to_int(s[5:7]) / 6000.0
    return v if _ch(s, 7) == "N" else -v


def _deg_to_dec_lon(s):
    v = _to_int(s[0:3]) + _to_int(s[3:5]) / 60.0 + _to_int(s[6:8]) / 6000.0
    return -v if _ch(s, 8) == "W" else v


def decode_mice_lat(dest):
    if len(dest) < 6:
        return 0.0
    out, ns = "", "S"
    for i in range(6):
        c = dest[i]
        out += chr(ord(c) - 32) if c > "9" else c
        if i == 3:
            out += "."
            if c > "9":
                ns = "N"
    return _deg_to_dec_lat(out + ns)


def decode_mice_lon(dest, info):
    if len(dest) < 6 or len(info) < 3:
        return 0.0
    offset = dest[4] > "9"
    we = "W" if dest[5] > "9" else "E"
    d = ord(info[0]) - 28 + (100 if offset else 0)
    if 180 <= d <= 189:
        d -= 80
    elif 190 <= d <= 199:
        d -= 190
    m = ord(info[1]) - 28
    if m >= 60:
        m -= 60
    h = ord(info[2]) - 28
    return _deg_to_dec_lon("%03d%02d.%02d%s" % (d, m, h, we))


# ---------------------------------------------------------------------------
# Packet classifier (port of APRSPacketLib::processReceivedPacket)
# ---------------------------------------------------------------------------

GPS, MESSAGE, STATUS, TELEMETRY, MICE, OBJECT, OTHER = range(7)


def classify(pkt):
    p = {"type": OTHER, "sender": "", "tocall": "", "path": "", "addressee": "",
         "payload": "", "lat": 0.0, "lon": 0.0}

    temp0 = pkt
    c = pkt.find(":")
    if c > 0 and _ch(pkt, c + 1) == "}":          # third-party header
        temp0 = pkt[pkt.find(":}") + 2:]

    gt = temp0.find(">")
    p["sender"] = temp0[0:gt] if gt >= 0 else temp0
    colon = temp0.find(":")
    temp1 = temp0[gt + 1:colon if colon >= 0 else len(temp0)]
    p["tocall"] = temp1
    if temp1.find(",") > 2:
        p["tocall"] = temp1[:temp1.find(",")]
        p["path"] = temp1[temp1.find(",") + 1:]

    if temp0.find(":=") > 10 or temp0.find(":!") > 10 or temp0.find(":@") > 10:
        p["type"] = GPS
        chars, off = ":=", 2
        if temp0.find(":!") > 10:
            chars = ":!"
        if temp0.find(":@") > 10:
            chars, off = ":@", 9
        po = temp0.find(chars) + off
        st = _ch(temp0, po)
        compressed = st in "/\\" or "A" <= st <= "Z" or "a" <= st <= "j"
        p["payload"] = temp0[po + (13 if compressed else 19):]
        if compressed:
            p["lat"] = decode_b91_lat(temp0[po + 1:po + 5])
            p["lon"] = decode_b91_lon(temp0[po + 5:po + 9])
        else:
            p["lat"] = decode_latitude(temp0[po:po + 8])
            p["lon"] = decode_longitude(temp0[po + 9:po + 18])
    elif temp0.find("::") > 10:
        p["type"] = MESSAGE
        i = temp0.find("::")
        p["addressee"] = temp0[i + 2:i + 11].strip()
        p["payload"] = temp0[i + 12:]
    elif temp0.find(":>") > 10:
        p["type"] = STATUS
        p["payload"] = temp0[temp0.find(":>") + 2:]
    elif temp0.find(":T#") >= 10 and temp0.find(":=/") == -1:
        p["type"] = TELEMETRY
        p["payload"] = temp0[temp0.find(":T#") + 3:]
    elif temp0.find(":`") > 10 or temp0.find(":'") > 10:
        p["type"] = MICE
        i = temp0.find(":`") if temp0.find(":`") > 10 else temp0.find(":'")
        info = temp0[i + 2:]
        p["lat"] = decode_mice_lat(p["tocall"])
        p["lon"] = decode_mice_lon(p["tocall"], info)
        start = 13 if (info.find("`") == 8 and info.find("}") == 12) else 8
        p["payload"] = info[start:]
    elif temp0.find(":;") > 10:
        p["type"] = OBJECT
        p["payload"] = temp0[temp0.find(":;") + 2:]
    else:
        p["payload"] = pkt
    return p


# ---------------------------------------------------------------------------
# CA2RXU syslog line builder (port of SYSLOG_Utils::logLoRaRx)
# ---------------------------------------------------------------------------

def distance_km(lat1, lon1, lat2, lon2):
    """TinyGPSPlus::distanceBetween, in km."""
    delta = math.radians(lon1 - lon2)
    sdlong, cdlong = math.sin(delta), math.cos(delta)
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    slat1, clat1 = math.sin(lat1), math.cos(lat1)
    slat2, clat2 = math.sin(lat2), math.cos(lat2)
    d = (clat1 * slat2) - (slat1 * clat2 * cdlong)
    d = math.sqrt(d * d + (clat2 * sdlong) ** 2)
    denom = (slat1 * slat2) + (clat1 * clat2 * cdlong)
    return math.atan2(d, denom) * 6372795 / 1000.0


def build_syslog(pkt, rssi, snr, cfg):
    p = classify(pkt)
    out = "<165>1 - %s %s - - - RX / " % (cfg.callsign, cfg.app_name)
    t = p["type"]
    if t == MESSAGE:
        out += "MESSAGE / %s ---> %s:%s" % (p["sender"], p["addressee"], p["payload"])
    elif t == GPS:
        out += "GPS / %s / " % p["sender"]
        if "WIDE1-1" in p["path"]:
            out += p["tocall"] + " / WIDE1-1"
        else:
            out += p["tocall"] + ("," + p["path"] if p["path"] else "") + " / -"
    elif t == STATUS:
        out += "STATUS / %s ---> %s" % (p["sender"], p["payload"])
    elif t == MICE:
        out += "MIC-E / %s ---> %s" % (p["sender"], "`" if ":`" in pkt else "'")
    elif t == OBJECT:
        out += "OBJECT / %s ---> %s" % (p["sender"], p["payload"])
    elif t == TELEMETRY:
        out += "TELEMETRY / %s ---> %s" % (p["sender"], p["payload"])
    else:
        out += p["payload"]
    out += " / %ddBm / %.2fdB / %dHz" % (rssi, snr, cfg.freq_error)
    if t in (GPS, MICE):
        dist = distance_km(cfg.lat, cfg.lon, p["lat"], p["lon"])
        out += " / %.5fN / %.5fE / %.1fkm" % (p["lat"], p["lon"], dist)
        if p["payload"]:
            out += " / " + p["payload"]
    return out, p


# ---------------------------------------------------------------------------
# Input sources
# ---------------------------------------------------------------------------

def journal_lines(unit):
    """Follow the unit's journal from now on. Yields text lines (latin-1)."""
    cmd = ["journalctl", "-u", unit, "-f", "-n", "0", "-o", "json", "--output-fields=MESSAGE"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
    for raw in proc.stdout:
        try:
            msg = json.loads(raw).get("MESSAGE")
        except ValueError:
            continue
        if isinstance(msg, list):          # journald stores non-text bytes as an int array
            msg = bytes(msg).decode("latin-1")
        elif not isinstance(msg, str):
            continue
        yield msg
    sys.exit("journalctl exited (code %s)" % proc.wait())


def stdin_lines():
    for raw in sys.stdin.buffer:
        yield raw.decode("latin-1").rstrip("\r\n")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--callsign", required=True, help="iGate callsign sent to link9, e.g. K6ATV-10")
    ap.add_argument("--lat", type=float, required=True, help="iGate latitude (decimal), for distance")
    ap.add_argument("--lon", type=float, required=True, help="iGate longitude (decimal), for distance")
    ap.add_argument("--server", default="lora.link9.net")
    ap.add_argument("--port", type=int, default=1514)
    ap.add_argument("--unit", default="direwolf-chase", help="systemd unit running Direwolf")
    ap.add_argument("--app-name", default="Direwolf_loraspi_link9_bridge_" + VERSION,
                    help="syslog APP-NAME field (CA2RXU sends CA2RXU_LoRa_iGate_<ver>)")
    ap.add_argument("--freq-error", type=int, default=0, help="Hz value to send (SX1262 driver has none)")
    ap.add_argument("--csv", help="also append every reception to this CSV file")
    ap.add_argument("--stdin", action="store_true", help="read Direwolf output from stdin (testing)")
    ap.add_argument("--dry-run", action="store_true", help="print syslog lines, do not send")
    cfg = ap.parse_args()

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print("link9 bridge %s: %s -> %s:%d%s" % (VERSION, cfg.unit if not cfg.stdin else "stdin",
          cfg.server, cfg.port, " (dry run)" if cfg.dry_run else ""), flush=True)

    csv_file = None
    if cfg.csv:
        os.makedirs(os.path.dirname(os.path.abspath(cfg.csv)), exist_ok=True)
        new = not os.path.exists(cfg.csv)
        csv_file = open(cfg.csv, "a", newline="", encoding="latin-1")
        writer = csv.writer(csv_file)
        if new:
            writer.writerow(["utc_time", "sender", "rssi_dbm", "snr_db", "lat", "lon", "packet"])
            csv_file.flush()

    for line in (stdin_lines() if cfg.stdin else journal_lines(cfg.unit)):
        m = RX_LINE.match(ANSI.sub("", line).replace("loraspi: failed to parse: ", "", 1))
        if not m:
            continue
        pkt = m.group("pkt")
        if ">" not in pkt or ":" not in pkt:
            continue
        rssi, snr = int(m.group("rssi")), float(m.group("snr"))
        try:
            msg, p = build_syslog(pkt, rssi, snr, cfg)
        except Exception as e:  # never let one odd packet stop the bridge
            print("skip (format error %s): %r" % (e, pkt), flush=True)
            continue

        if csv_file:
            writer.writerow([datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             p["sender"], rssi, snr,
                             "%.5f" % p["lat"] if p["type"] in (GPS, MICE) else "",
                             "%.5f" % p["lon"] if p["type"] in (GPS, MICE) else "", pkt])
            csv_file.flush()

        if cfg.dry_run:
            print(msg, flush=True)
            continue
        try:
            sock.sendto(msg.encode("latin-1", "replace"), (cfg.server, cfg.port))
            print("sent: " + msg, flush=True)
        except OSError as e:     # DNS or network hiccup: log and keep going
            print("send failed (%s): %s" % (e, msg), flush=True)


if __name__ == "__main__":
    main()
