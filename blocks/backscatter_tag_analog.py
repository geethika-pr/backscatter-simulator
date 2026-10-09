# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Tag ANALOG (Backscatter)
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
import backscatter_tag_analog_epy_block_0 as epy_block_0  # embedded python block
import threading







class backscatter_tag_analog(gr.hier_block2):
    def __init__(self, alpha=0.01, analog_mode=0, as_imag=0.02, as_real=0.05, bit_rate=10e3, custom_g0_magnitude=0.2, custom_g0_phase_deg=0.0, custom_g1_mag=0.85, custom_g1_phase_deg=180.0, enable_gated=1, mod_depth=0.8, p_th=0.001, packet_bits=16, samp_rate=2e6, slot_id=0):
        gr.hier_block2.__init__(
            self, "Tag ANALOG (Backscatter)",
                gr.io_signature.makev(2, 2, [gr.sizeof_gr_complex*1, gr.sizeof_float*1]),
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
        )

        ##################################################
        # Parameters
        ##################################################
        self.alpha = alpha
        self.analog_mode = analog_mode
        self.as_imag = as_imag
        self.as_real = as_real
        self.bit_rate = bit_rate
        self.custom_g0_magnitude = custom_g0_magnitude
        self.custom_g0_phase_deg = custom_g0_phase_deg
        self.custom_g1_mag = custom_g1_mag
        self.custom_g1_phase_deg = custom_g1_phase_deg
        self.enable_gated = enable_gated
        self.mod_depth = mod_depth
        self.p_th = p_th
        self.packet_bits = packet_bits
        self.samp_rate = samp_rate
        self.slot_id = slot_id

        ##################################################
        # Blocks
        ##################################################

        self.tag_pie_decoder_0 = tag_pie_decoder(
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
        self.epy_block_0 = epy_block_0.auto_normalizer(alpha=0.005, min_spread=)
        self.complex_gamma_modulator_0 = complex_gamma_modulator(
            as_imag=as_imag,
            as_real=as_real,
            drift_pct=0.0,
            gamma0_mag=(custom_g0_mag if analog_mode == 2 else ((0.5 - 0.4 * mod_depth) if analog_mode == 0 else 0.85)),
            gamma0_phase_deg=(custom_g0_phase_deg if analog_mode == 2 else (0.0 if analog_mode == 0 else (-45.0 * mod_depth))),
            gamma1_mag=(custom_g1_mag if analog_mode == 2 else ((0.5 + 0.4 * mod_depth) if analog_mode == 0 else 0.85)),
            gamma1_phase_deg=(custom_g1_phase_deg if analog_mode == 2 else (0.0 if analog_mode == 0 else (45.0 * mod_depth))),
            jitter_samples=0.0,
        )
        self.blocks_multiply_xx_0 = blocks.multiply_vff(1)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.blocks_multiply_xx_0, 0), (self.harvesting_gate_0, 1))
        self.connect((self.complex_gamma_modulator_0, 0), (self, 0))
        self.connect((self.epy_block_0, 0), (self.blocks_multiply_xx_0, 1))
        self.connect((self.harvesting_gate_0, 0), (self.complex_gamma_modulator_0, 1))
        self.connect((self, 0), (self.complex_gamma_modulator_0, 0))
        self.connect((self, 0), (self.harvesting_gate_0, 0))
        self.connect((self, 0), (self.tag_pie_decoder_0, 0))
        self.connect((self, 1), (self.epy_block_0, 0))
        self.connect((self.tag_pie_decoder_0, 0), (self.blocks_multiply_xx_0, 0))


    def get_alpha(self):
        return self.alpha

    def set_alpha(self, alpha):
        self.alpha = alpha
        self.harvesting_gate_0.set_alpha(self.alpha)

    def get_analog_mode(self):
        return self.analog_mode

    def set_analog_mode(self, analog_mode):
        self.analog_mode = analog_mode
        self.complex_gamma_modulator_0.set_gamma0_mag((custom_g0_mag if self.analog_mode == 2 else ((0.5 - 0.4 * self.mod_depth) if self.analog_mode == 0 else 0.85)))
        self.complex_gamma_modulator_0.set_gamma0_phase_deg((self.custom_g0_phase_deg if self.analog_mode == 2 else (0.0 if self.analog_mode == 0 else (-45.0 * self.mod_depth))))
        self.complex_gamma_modulator_0.set_gamma1_mag((self.custom_g1_mag if self.analog_mode == 2 else ((0.5 + 0.4 * self.mod_depth) if self.analog_mode == 0 else 0.85)))
        self.complex_gamma_modulator_0.set_gamma1_phase_deg((self.custom_g1_phase_deg if self.analog_mode == 2 else (0.0 if self.analog_mode == 0 else (45.0 * self.mod_depth))))

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

    def get_custom_g0_magnitude(self):
        return self.custom_g0_magnitude

    def set_custom_g0_magnitude(self, custom_g0_magnitude):
        self.custom_g0_magnitude = custom_g0_magnitude

    def get_custom_g0_phase_deg(self):
        return self.custom_g0_phase_deg

    def set_custom_g0_phase_deg(self, custom_g0_phase_deg):
        self.custom_g0_phase_deg = custom_g0_phase_deg
        self.complex_gamma_modulator_0.set_gamma0_phase_deg((self.custom_g0_phase_deg if self.analog_mode == 2 else (0.0 if self.analog_mode == 0 else (-45.0 * self.mod_depth))))

    def get_custom_g1_mag(self):
        return self.custom_g1_mag

    def set_custom_g1_mag(self, custom_g1_mag):
        self.custom_g1_mag = custom_g1_mag
        self.complex_gamma_modulator_0.set_gamma1_mag((self.custom_g1_mag if self.analog_mode == 2 else ((0.5 + 0.4 * self.mod_depth) if self.analog_mode == 0 else 0.85)))

    def get_custom_g1_phase_deg(self):
        return self.custom_g1_phase_deg

    def set_custom_g1_phase_deg(self, custom_g1_phase_deg):
        self.custom_g1_phase_deg = custom_g1_phase_deg
        self.complex_gamma_modulator_0.set_gamma1_phase_deg((self.custom_g1_phase_deg if self.analog_mode == 2 else (0.0 if self.analog_mode == 0 else (45.0 * self.mod_depth))))

    def get_enable_gated(self):
        return self.enable_gated

    def set_enable_gated(self, enable_gated):
        self.enable_gated = enable_gated
        self.tag_pie_decoder_0.set_enable_mac(self.enable_gated)

    def get_mod_depth(self):
        return self.mod_depth

    def set_mod_depth(self, mod_depth):
        self.mod_depth = mod_depth
        self.complex_gamma_modulator_0.set_gamma0_mag((custom_g0_mag if self.analog_mode == 2 else ((0.5 - 0.4 * self.mod_depth) if self.analog_mode == 0 else 0.85)))
        self.complex_gamma_modulator_0.set_gamma0_phase_deg((self.custom_g0_phase_deg if self.analog_mode == 2 else (0.0 if self.analog_mode == 0 else (-45.0 * self.mod_depth))))
        self.complex_gamma_modulator_0.set_gamma1_mag((self.custom_g1_mag if self.analog_mode == 2 else ((0.5 + 0.4 * self.mod_depth) if self.analog_mode == 0 else 0.85)))
        self.complex_gamma_modulator_0.set_gamma1_phase_deg((self.custom_g1_phase_deg if self.analog_mode == 2 else (0.0 if self.analog_mode == 0 else (45.0 * self.mod_depth))))

    def get_p_th(self):
        return self.p_th

    def set_p_th(self, p_th):
        self.p_th = p_th
        self.harvesting_gate_0.set_p_th(self.p_th)

    def get_packet_bits(self):
        return self.packet_bits

    def set_packet_bits(self, packet_bits):
        self.packet_bits = packet_bits
        self.tag_pie_decoder_0.set_packet_bits(self.packet_bits)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.tag_pie_decoder_0.set_samp_rate(self.samp_rate)

    def get_slot_id(self):
        return self.slot_id

    def set_slot_id(self, slot_id):
        self.slot_id = slot_id
        self.tag_pie_decoder_0.set_slot_id(self.slot_id)

