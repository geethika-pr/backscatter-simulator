# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Harvesting Gate
# GNU Radio version: 3.10.12.0

from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.fft import window
import sys
import signal
import harvesting_gate_epy_block_0 as epy_block_0  # embedded python block
import threading







class harvesting_gate(gr.hier_block2):
    def __init__(self, alpha=0.01, p_th=0.001):
        gr.hier_block2.__init__(
            self, "Harvesting Gate",
                gr.io_signature.makev(2, 2, [gr.sizeof_gr_complex*1, gr.sizeof_float*1]),
                gr.io_signature(1, 1, gr.sizeof_float*1),
        )

        ##################################################
        # Parameters
        ##################################################
        self.alpha = alpha
        self.p_th = p_th

        ##################################################
        # Blocks
        ##################################################

        self.epy_block_0 = epy_block_0.harvesting_gate(p_th=p_th, alpha=alpha)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.epy_block_0, 0), (self, 0))
        self.connect((self, 0), (self.epy_block_0, 0))
        self.connect((self, 1), (self.epy_block_0, 1))


    def get_alpha(self):
        return self.alpha

    def set_alpha(self, alpha):
        self.alpha = alpha
        self.epy_block_0.alpha = self.alpha

    def get_p_th(self):
        return self.p_th

    def set_p_th(self, p_th):
        self.p_th = p_th
        self.epy_block_0.p_th = self.p_th

