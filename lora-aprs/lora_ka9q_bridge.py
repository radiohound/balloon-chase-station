#!/usr/bin/env python3
"""
lora_ka9q_bridge.py - LoRa APRS ka9q-radio -> Dire Wolf bridge via KISS TCP.

Architecture
------------
ka9q-radio (radiod) -> pcmrecord -> lora_ka9q_bridge.py <-- Dire Wolf (NCHANNEL)

Run as:
    pcmrecord -c -r lora-iq.local | python3 lora_ka9q_bridge.py
    pcmrecord -c -r lora-iq.local | python3 lora_ka9q_bridge.py -c /etc/direwolf/lora.conf

This bridge reads IQ samples from stdin (piped from pcmrecord), demodulates
LoRa packets using gr-lora_sdr, and forwards decoded frames to Dire Wolf via
KISS TCP.  TX frames sent by Dire Wolf are displayed and dropped (RX-only path).

direwolf.conf:
    NCHANNEL <chan>  127.0.0.1  8002

Requirements
------------
    GNU Radio 3.10+ with gr-lora_sdr
    lora_ka9q_flowgraph.py and lora_sdr_flowgraph.py in the same directory
    ka9q-radio (radiod) with a lora-iq.local channel configured
"""

import argparse
import logging
import os
import sys

# Allow importing sibling scripts from the same directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Re-use all KISS/AX.25/config/logging helpers from the RTL-SDR bridge.
from lora_sdr_bridge import (
    parse_lora_conf,
    KissServer,
    _valid_tnc2_header,
    tnc2_to_ax25,
    _dw_print,
    _ColorFormatter,
    _ANSI_GREEN,
    _ANSI_MAGENTA,
)

log = logging.getLogger("lora_ka9q_bridge")


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class LoRaKa9qBridge:
    """
    Wires the ka9q-radio IQ flowgraph (stdin) to the Dire Wolf KISS server.
    """

    def __init__(self, cfg):
        self._cfg  = cfg
        self._kiss = KissServer(port=int(cfg.get('kissport', 8002)))
        self._fg   = None

    def _on_packet(self, payload_bytes, snr=None):
        """Called by the GNU Radio flowgraph for each decoded LoRa frame."""
        clean = b''
        for i, b in enumerate(payload_bytes):
            if 0x30 <= b <= 0x39 or 0x41 <= b <= 0x5a:
                clean = payload_bytes[i:]
                break
        if not clean:
            log.debug("Empty or all-preamble packet — skipped")
            return

        try:
            line = clean.decode('ascii', errors='replace').strip()
        except Exception as e:
            log.warning("Could not decode payload: %s", e)
            return

        if not line:
            return

        if not _valid_tnc2_header(line):
            safe = ''.join(c if c.isprintable() else '?' for c in line)
            log.warning("Dropping packet with invalid TNC2 header: %s", safe)
            return

        snr_str = f"  SNR={snr:.1f}dB" if snr is not None else ""
        src = line.split('>')[0] if '>' in line else line[:9]
        _dw_print(src + "  LoRa ka9q" + snr_str, line, _ANSI_GREEN)

        self._kiss.send_kiss(line)

    def start(self):
        from lora_ka9q_flowgraph import LoRaKa9qFlowgraph

        self._kiss.start()

        try:
            self._fg = LoRaKa9qFlowgraph(
                freq_mhz    = float(self._cfg.get('lorafreq',     '433.775')),
                bw          = int(  self._cfg.get('lorabw',        '125')),
                sf          = int(  self._cfg.get('lorasf',        '12')),
                cr          = int(  self._cfg.get('loracr',        '5')),
                sw          = int(  self._cfg.get('lorasw',        '0x12'), 16),
                sample_rate = int(  self._cfg.get('sdrsamplerate', '1000000')),
                callback    = self._on_packet,
            )
        except ImportError as e:
            log.error("Cannot start ka9q flowgraph: %s", e)
            sys.exit(1)

        self._fg.start()
        log.info(
            "LoRa ka9q bridge running — %.3f MHz SF%s BW%s kHz — KISS port %s",
            float(self._cfg.get('lorafreq', '433.775')),
            self._cfg.get('lorasf', '12'),
            self._cfg.get('lorabw', '125'),
            self._cfg.get('kissport', '8002'),
        )
        log.info("Reading IQ from stdin (pcmrecord -c -r lora-iq.local)")
        log.info("Add to direwolf.conf:  NCHANNEL <chan>  127.0.0.1  %s",
                 self._cfg.get('kissport', '8002'))

        import time
        try:
            while self._fg.running:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self.stop()

    def stop(self):
        if self._fg and self._fg.running:
            self._fg.stop()
        self._kiss.stop()
        log.info("LoRa ka9q bridge stopped")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LoRa APRS ka9q-radio bridge — stdin IQ -> Dire Wolf (NCHANNEL)"
    )
    parser.add_argument(
        '-c', '--config',
        default=os.path.join(os.path.expanduser('~'), 'lora.conf'),
        help='Path to lora.conf (default: ~/lora.conf)',
    )
    parser.add_argument(
        '--log-level',
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
    )
    args = parser.parse_args()

    handler = logging.StreamHandler()
    handler.setFormatter(_ColorFormatter(
        fmt='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    ))
    logging.root.setLevel(getattr(logging, args.log_level))
    logging.root.addHandler(handler)

    if sys.stdin.isatty():
        log.error("No IQ data on stdin.  Run as:")
        log.error("  pcmrecord -c -r lora-iq.local | python3 lora_ka9q_bridge.py")
        sys.exit(1)

    cfg = parse_lora_conf(args.config)
    log.debug("Config: %s", cfg)

    bridge = LoRaKa9qBridge(cfg)

    import signal
    try:
        signal.signal(signal.SIGTERM, lambda s, f: bridge.stop() or sys.exit(0))
        signal.signal(signal.SIGINT,  lambda s, f: bridge.stop() or sys.exit(0))
    except (ValueError, OSError):
        pass

    bridge.start()


if __name__ == '__main__':
    main()
