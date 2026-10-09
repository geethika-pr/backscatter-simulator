# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Complex Gamma Modulator
# GNU Radio version: 3.10.12.0

from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.fft import window
import sys
import signal
import complex_gamma_modulator_epy_block_0 as epy_block_0  # embedded python block
import threading







class complex_gamma_modulator(gr.hier_block2):
    def __init__(self, as_imag=0.02, as_real=0.5, drift_pct=0.0, gamma0_mag=0.2, gamma0_phase_deg=0.0, gamma1_mag=0.85, gamma1_phase_deg=180, jitter_samples=0.0):
        gr.hier_block2.__init__(
            self, "Complex Gamma Modulator",
                gr.io_signature.makev(2, 2, [gr.sizeof_gr_complex*1, gr.sizeof_float*1]),
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
        )

        ##################################################
        # Parameters
        ##################################################
        self.as_imag = as_imag
        self.as_real = as_real
        self.drift_pct = drift_pct
        self.gamma0_mag = gamma0_mag
        self.gamma0_phase_deg = gamma0_phase_deg
        self.gamma1_mag = gamma1_mag
        self.gamma1_phase_deg = gamma1_phase_deg
        self.jitter_samples = jitter_samples

        ##################################################
        # Blocks
        ##################################################

        self.epy_block_0 = epy_block_0.complex_gamma_modulator(as_real=as_real, as_imag=as_imag, gamma0_mag=gamma0_mag, gamma0_phase_deg=gamma0_phase_deg, gamma1_mag=gamma1_mag, gamma1_phase_deg=gamma1_phase_deg, drift_pct=drift_pct, jitter_samples=jitter_samples)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.epy_block_0, 0), (self, 0))
        self.connect((self, 0), (self.epy_block_0, 0))
        self.connect((self, 1), (self.epy_block_0, 1))


    def get_as_imag(self):
        return self.as_imag

    def set_as_imag(self, as_imag):
        self.as_imag = as_imag

    def get_as_real(self):
        return self.as_real

    def set_as_real(self, as_real):
        self.as_real = as_real

    def get_drift_pct(self):
        return self.drift_pct

    def set_drift_pct(self, drift_pct):
        self.drift_pct = drift_pct
        self.epy_block_0.drift_pct = self.drift_pct

    def get_gamma0_mag(self):
        return self.gamma0_mag

    def set_gamma0_mag(self, gamma0_mag):
        self.gamma0_mag = gamma0_mag

    def get_gamma0_phase_deg(self):
        return self.gamma0_phase_deg

    def set_gamma0_phase_deg(self, gamma0_phase_deg):
        self.gamma0_phase_deg = gamma0_phase_deg

    def get_gamma1_mag(self):
        return self.gamma1_mag

    def set_gamma1_mag(self, gamma1_mag):
        self.gamma1_mag = gamma1_mag

    def get_gamma1_phase_deg(self):
        return self.gamma1_phase_deg

    def set_gamma1_phase_deg(self, gamma1_phase_deg):
        self.gamma1_phase_deg = gamma1_phase_deg

    def get_jitter_samples(self):
        return self.jitter_samples

    def set_jitter_samples(self, jitter_samples):
        self.jitter_samples = jitter_samples
        self.epy_block_0.jitter_samples = self.jitter_samples

