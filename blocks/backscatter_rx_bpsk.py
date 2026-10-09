# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Reciever BPSK (Backscatter)
# GNU Radio version: 3.10.12.0

from gnuradio import blocks
from gnuradio import digital
from gnuradio import filter
from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.fft import window
import sys
import signal
import threading







class backscatter_rx_bpsk(gr.hier_block2):
    def __init__(self, dc_len=1024, samp_rate=2e6, si_enable=1, si_isolation_db=25.0):
        gr.hier_block2.__init__(
            self, "Reciever BPSK (Backscatter)",
                gr.io_signature.makev(2, 2, [gr.sizeof_gr_complex*1, gr.sizeof_gr_complex*1]),
                gr.io_signature(1, 1, gr.sizeof_char*1),
        )

        ##################################################
        # Parameters
        ##################################################
        self.dc_len = dc_len
        self.samp_rate = samp_rate
        self.si_enable = si_enable
        self.si_isolation_db = si_isolation_db

        ##################################################
        # Blocks
        ##################################################

        self.digital_binary_slicer_fb_0 = digital.binary_slicer_fb()
        self.dc_blocker_xx_0 = filter.dc_blocker_cc(dc_len, True)
        self.blocks_selector_0 = blocks.selector(gr.sizeof_gr_complex*1,si_enable,0)
        self.blocks_selector_0.set_enabled(True)
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_cc(-1.0 * (10.0 ** (-si_isolation_db / 20.0)))
        self.blocks_complex_to_real_0 = blocks.complex_to_real(1)
        self.blocks_add_xx_0 = blocks.add_vcc(1)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.blocks_add_xx_0, 0), (self.blocks_selector_0, 1))
        self.connect((self.blocks_complex_to_real_0, 0), (self.digital_binary_slicer_fb_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.blocks_add_xx_0, 1))
        self.connect((self.blocks_selector_0, 0), (self.dc_blocker_xx_0, 0))
        self.connect((self.dc_blocker_xx_0, 0), (self.blocks_complex_to_real_0, 0))
        self.connect((self.digital_binary_slicer_fb_0, 0), (self, 0))
        self.connect((self, 0), (self.blocks_add_xx_0, 0))
        self.connect((self, 0), (self.blocks_selector_0, 0))
        self.connect((self, 1), (self.blocks_multiply_const_vxx_0, 0))


    def get_dc_len(self):
        return self.dc_len

    def set_dc_len(self, dc_len):
        self.dc_len = dc_len

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate

    def get_si_enable(self):
        return self.si_enable

    def set_si_enable(self, si_enable):
        self.si_enable = si_enable
        self.blocks_selector_0.set_input_index(self.si_enable)

    def get_si_isolation_db(self):
        return self.si_isolation_db

    def set_si_isolation_db(self, si_isolation_db):
        self.si_isolation_db = si_isolation_db
        self.blocks_multiply_const_vxx_0.set_k(-1.0 * (10.0 ** (-self.si_isolation_db / 20.0)))

