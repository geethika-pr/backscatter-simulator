# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Tag OOK (Backscatter)
# GNU Radio version: 3.10.12.0

import os
import sys
import logging as log

def get_state_directory() -> str:
    oldpath = os.path.expanduser("~/.grc_gnuradio")
    try:
        from gnuradio.gr import paths
        newpath = paths.persistent()
        if os.path.exists(newpath):
            return newpath
        if os.path.exists(oldpath):
            log.warning(f"Found persistent state path '{newpath}', but file does not exist. " +
                     f"Old default persistent state path '{oldpath}' exists; using that. " +
                     "Please consider moving state to new location.")
            return oldpath
        # Default to the correct path if both are configured.
        # neither old, nor new path exist: create new path, return that
        os.makedirs(newpath, exist_ok=True)
        return newpath
    except (ImportError, NameError):
        log.warning("Could not retrieve GNU Radio persistent state directory from GNU Radio. " +
                 "Trying defaults.")
        xdgstate = os.getenv("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
        xdgcand = os.path.join(xdgstate, "gnuradio")
        if os.path.exists(xdgcand):
            return xdgcand
        if os.path.exists(oldpath):
            log.warning(f"Using legacy state path '{oldpath}'. Please consider moving state " +
                     f"files to '{xdgcand}'.")
            return oldpath
        # neither old, nor new path exist: create new path, return that
        os.makedirs(xdgcand, exist_ok=True)
        return xdgcand

sys.path.append(os.environ.get('GRC_HIER_PATH', get_state_directory()))

from complex_gamma_modulator import complex_gamma_modulator  # grc-generated hier_block
from gnuradio import blocks
from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.fft import window
import signal
from harvesting_gate import harvesting_gate  # grc-generated hier_block
from tag_pie_decoder import tag_pie_decoder  # grc-generated hier_block
import threading







class backscatter_tag_ook(gr.hier_block2):
    def __init__(self, alpha=0.01, as_imag=0.0, as_real=0.05, bit_rate=10e3, enable_gated=1, gamma0_mag=0.1, gamma0_phase_deg=180.0, gamma1_mag=0.9, gamma1_phase_deg=180.0, p_th=0.001, packet_bits=16000, samp_rate=2e6, slot_id=0):
        gr.hier_block2.__init__(
            self, "Tag OOK (Backscatter)",
                gr.io_signature.makev(2, 2, [gr.sizeof_gr_complex*1, gr.sizeof_char*1]),
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
        )

        ##################################################
        # Parameters
        ##################################################
        self.alpha = alpha
        self.as_imag = as_imag
        self.as_real = as_real
        self.bit_rate = bit_rate
        self.enable_gated = enable_gated
        self.gamma0_mag = gamma0_mag
        self.gamma0_phase_deg = gamma0_phase_deg
        self.gamma1_mag = gamma1_mag
        self.gamma1_phase_deg = gamma1_phase_deg
        self.p_th = p_th
        self.packet_bits = packet_bits
        self.samp_rate = samp_rate
        self.slot_id = slot_id

        ##################################################
        # Blocks
        ##################################################

        self.tag_pie_decoder_1 = tag_pie_decoder(
            bit_rate=10e3,
            enable_mac=enable_gated,
            packet_bits=packet_bits,
            samp_rate=samp_rate,
            slot_id=slot_id,
            tari=12.5,
            threshold=0.6,
        )
        self.harvesting_gate_0 = harvesting_gate(
            alpha=alpha,
            p_th=p_th,
        )
        self.complex_gamma_modulator_0 = complex_gamma_modulator(
            as_imag=as_imag,
            as_real=as_real,
            drift_pct=0.0,
            gamma0_mag=gamma0_mag,
            gamma0_phase_deg=gamma0_phase_deg,
            gamma1_mag=gamma1_mag,
            gamma1_phase_deg=gamma1_phase_deg,
            jitter_samples=0.0,
        )
        self.blocks_repeat_0 = blocks.repeat(gr.sizeof_float*1, (int(samp_rate / bit_rate)))
        self.blocks_multiply_xx_0 = blocks.multiply_vff(1)
        self.blocks_char_to_float_0 = blocks.char_to_float(1, 1)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.blocks_char_to_float_0, 0), (self.blocks_repeat_0, 0))
        self.connect((self.blocks_multiply_xx_0, 0), (self.harvesting_gate_0, 1))
        self.connect((self.blocks_repeat_0, 0), (self.blocks_multiply_xx_0, 1))
        self.connect((self.complex_gamma_modulator_0, 0), (self, 0))
        self.connect((self.harvesting_gate_0, 0), (self.complex_gamma_modulator_0, 1))
        self.connect((self, 0), (self.complex_gamma_modulator_0, 0))
        self.connect((self, 0), (self.harvesting_gate_0, 0))
        self.connect((self, 0), (self.tag_pie_decoder_1, 0))
        self.connect((self, 1), (self.blocks_char_to_float_0, 0))
        self.connect((self.tag_pie_decoder_1, 0), (self.blocks_multiply_xx_0, 0))


    def get_alpha(self):
        return self.alpha

    def set_alpha(self, alpha):
        self.alpha = alpha
        self.harvesting_gate_0.set_alpha(self.alpha)

    def get_as_imag(self):
        return self.as_imag

    def set_as_imag(self, as_imag):
        self.as_imag = as_imag
        self.complex_gamma_modulator_0.set_as_imag(self.as_imag)

    def get_as_real(self):
        return self.as_real

    def set_as_real(self, as_real):
        self.as_real = as_real
        self.complex_gamma_modulator_0.set_as_real(self.as_real)

    def get_bit_rate(self):
        return self.bit_rate

    def set_bit_rate(self, bit_rate):
        self.bit_rate = bit_rate
        self.blocks_repeat_0.set_interpolation((int(self.samp_rate / self.bit_rate)))

    def get_enable_gated(self):
        return self.enable_gated

    def set_enable_gated(self, enable_gated):
        self.enable_gated = enable_gated
        self.tag_pie_decoder_1.set_enable_mac(self.enable_gated)

    def get_gamma0_mag(self):
        return self.gamma0_mag

    def set_gamma0_mag(self, gamma0_mag):
        self.gamma0_mag = gamma0_mag
        self.complex_gamma_modulator_0.set_gamma0_mag(self.gamma0_mag)

    def get_gamma0_phase_deg(self):
        return self.gamma0_phase_deg

    def set_gamma0_phase_deg(self, gamma0_phase_deg):
        self.gamma0_phase_deg = gamma0_phase_deg
        self.complex_gamma_modulator_0.set_gamma0_phase_deg(self.gamma0_phase_deg)

    def get_gamma1_mag(self):
        return self.gamma1_mag

    def set_gamma1_mag(self, gamma1_mag):
        self.gamma1_mag = gamma1_mag
        self.complex_gamma_modulator_0.set_gamma1_mag(self.gamma1_mag)

    def get_gamma1_phase_deg(self):
        return self.gamma1_phase_deg

    def set_gamma1_phase_deg(self, gamma1_phase_deg):
        self.gamma1_phase_deg = gamma1_phase_deg
        self.complex_gamma_modulator_0.set_gamma1_phase_deg(self.gamma1_phase_deg)

    def get_p_th(self):
        return self.p_th

    def set_p_th(self, p_th):
        self.p_th = p_th
        self.harvesting_gate_0.set_p_th(self.p_th)

    def get_packet_bits(self):
        return self.packet_bits

    def set_packet_bits(self, packet_bits):
        self.packet_bits = packet_bits
        self.tag_pie_decoder_1.set_packet_bits(self.packet_bits)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.blocks_repeat_0.set_interpolation((int(self.samp_rate / self.bit_rate)))
        self.tag_pie_decoder_1.set_samp_rate(self.samp_rate)

    def get_slot_id(self):
        return self.slot_id

    def set_slot_id(self, slot_id):
        self.slot_id = slot_id
        self.tag_pie_decoder_1.set_slot_id(self.slot_id)

