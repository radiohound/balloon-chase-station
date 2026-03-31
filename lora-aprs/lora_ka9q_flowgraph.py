#!/usr/bin/env python3
"""
lora_ka9q_flowgraph.py - GNU Radio flowgraph for LoRa receive via ka9q-radio IQ stream.

Reads interleaved signed int16 IQ from stdin, as produced by:
    pcmrecord -c -r lora-iq.local

The ka9q-radio radiod instance must be configured with a channel section that
outputs IQ centred on the LoRa frequency, e.g.:

    [lora-iq]
    data = lora-iq.local
    freq = 433m775000
    samprate = 1000000
    low = -500000
    high = +500000

This flowgraph replaces the RTL-SDR/gr-osmosdr source used by
lora_sdr_flowgraph.py.  The gr-lora_sdr demodulator chain is identical.
"""

import logging

log = logging.getLogger(__name__)


class LoRaKa9qFlowgraph:
    """
    GNU Radio flowgraph that reads IQ from stdin (ka9q-radio pcmrecord output)
    and demodulates LoRa packets using gr-lora_sdr.

    Parameters
    ----------
    freq_mhz    : float  — LoRa centre frequency in MHz (e.g. 433.775)
    bw          : int    — LoRa bandwidth in kHz (125, 250, 500)
    sf          : int    — spreading factor 7-12
    cr          : int    — coding rate numerator (5=4/5 .. 8=4/8)
    sw          : int    — sync word (0x12 for LoRa APRS)
    sample_rate : int    — IQ sample rate matching ka9q-radio channel samprate
    callback    : callable(bytes, snr=None) — called with each decoded payload
    """

    def __init__(self, freq_mhz, bw, sf, cr, sw,
                 sample_rate=1_000_000, callback=None):
        self._freq_hz   = int(freq_mhz * 1e6)
        self._bw        = bw
        self._sf        = sf
        self._cr        = cr
        self._sw        = sw
        self._samp_rate = sample_rate
        self._callback  = callback
        self._tb        = None
        self._running   = False
        self._build()

    def _build(self):
        from gnuradio import gr, blocks, lora_sdr
        from lora_sdr_flowgraph import _LoRaMessageSink

        bw_hz     = self._bw * 1000
        os_factor = self._samp_rate // bw_hz
        buf_items = int(2 ** self._sf * os_factor * 1.5)
        buf_bytes = buf_items * gr.sizeof_gr_complex

        tb = gr.top_block()

        # Source: interleaved int16 stereo IQ from stdin (pcmrecord -c output).
        # file_descriptor_source(itemsize, fd, repeat)
        src   = blocks.file_descriptor_source(gr.sizeof_short, 0, False)
        s2c   = blocks.interleaved_short_to_complex(False, False)
        scale = blocks.multiply_const_cc(1.0 / 32768.0)

        # Buffer block — forces GR to allocate a real output buffer large enough
        # for frame_sync's work() call (same pattern as lora_sdr_flowgraph.py).
        buf = blocks.multiply_const_cc(1.0)
        buf.set_min_output_buffer(buf_bytes)

        # gr-lora_sdr demodulator chain (identical to RTL-SDR path).
        cr_api         = self._cr - 4   # API: 1=4/5, 2=4/6, 3=4/7, 4=4/8
        frame_sync     = lora_sdr.frame_sync(
            self._freq_hz, bw_hz, self._sf, False, [self._sw], os_factor, 8
        )
        fft_demod      = lora_sdr.fft_demod(True, True)
        gray_mapping   = lora_sdr.gray_mapping(True)
        deinterleaver  = lora_sdr.deinterleaver(True)
        hamming_dec    = lora_sdr.hamming_dec(True)
        header_decoder = lora_sdr.header_decoder(
            False, cr_api, 255, False, 2, False
        )
        dewhitening    = lora_sdr.dewhitening()
        crc_verif      = lora_sdr.crc_verif(False, False)

        msg_sink = _LoRaMessageSink(callback=self._on_packet)

        tb.connect(src, s2c, scale, buf, frame_sync, fft_demod, gray_mapping,
                   deinterleaver, hamming_dec, header_decoder,
                   dewhitening, crc_verif)
        tb.msg_connect(header_decoder, "frame_info", frame_sync, "frame_info")
        tb.msg_connect(crc_verif, "msg", msg_sink._block, "in")

        self._tb       = tb
        self._msg_sink = msg_sink
        log.debug("ka9q IQ flowgraph built — %.3f MHz SF%d BW%d kHz os=%d",
                  self._freq_hz / 1e6, self._sf, self._bw, os_factor)

    def _on_packet(self, payload_bytes, snr=None):
        if self._callback:
            try:
                self._callback(payload_bytes, snr=snr)
            except Exception:
                log.exception("callback raised an exception")

    def start(self):
        if self._running:
            return
        self._running = True
        self._tb.start()
        log.info("ka9q IQ flowgraph running — %.3f MHz SF%d BW%d kHz",
                 self._freq_hz / 1e6, self._sf, self._bw)

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._tb.stop()
        self._tb.wait()
        log.info("ka9q IQ flowgraph stopped")

    @property
    def running(self):
        return self._running
