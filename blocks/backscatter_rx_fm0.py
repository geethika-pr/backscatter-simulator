# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Reciever FM0 (Backscatter)
# GNU Radio version: 3.10.12.0

from gnuradio import blocks
from gnuradio import digital
from gnuradio import filter
from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.fft import window
import sys
import signal
import backscatter_rx_fm0_epy_block_0 as epy_block_0  # embedded python block
import threading







class backscatter_rx_fm0(gr.hier_block2):
    def __init__(self, bit_rate=10000, dc_len=1024, payload_len=5, preamble='10101011', samp_rate=2e6, si_enable=1, si_isolation_db=25.0, threshold=0.02):
        gr.hier_block2.__init__(
            self, "Reciever FM0 (Backscatter)",
                gr.io_signature.makev(2, 2, [gr.sizeof_gr_complex*1, gr.sizeof_gr_complex*1]),
                gr.io_signature(1, 1, gr.sizeof_char*1),
        )

        ##################################################
        # Parameters
        ##################################################
        self.bit_rate = bit_rate
        self.dc_len = dc_len
        self.payload_len = payload_len
        self.preamble = preamble
        self.samp_rate = samp_rate
        self.si_enable = si_enable
        self.si_isolation_db = si_isolation_db
        self.threshold = threshold

        ##################################################
        # Blocks
        ##################################################

        self.epy_block_0 = epy_block_0.blk(samp_rate=samp_rate, bit_rate=bit_rate, payload_len_bits=payload_len, preamble=preamble)
        self.digital_binary_slicer_fb_0 = digital.binary_slicer_fb()
        self.dc_blocker_xx_0 = filter.dc_blocker_cc(dc_len, True)
        self.blocks_selector_0 = blocks.selector(gr.sizeof_gr_complex*1,si_enable,0)
        self.blocks_selector_0.set_enabled(True)
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_cc(-1.0 * (10.0 ** (-si_isolation_db / 20.0)))
        self.blocks_complex_to_real_0 = blocks.complex_to_real(1)
        self.blocks_add_xx_0 = blocks.add_vcc(1)
        self.blocks_add_const_vxx_0 = blocks.add_const_ff((-threshold))


        ##################################################
        # Connections
        ##################################################
        self.connect((self.blocks_add_const_vxx_0, 0), (self.digital_binary_slicer_fb_0, 0))
        self.connect((self.blocks_add_xx_0, 0), (self.blocks_selector_0, 1))
        self.connect((self.blocks_complex_to_real_0, 0), (self.blocks_add_const_vxx_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.blocks_add_xx_0, 1))
        self.connect((self.blocks_selector_0, 0), (self.dc_blocker_xx_0, 0))
        self.connect((self.dc_blocker_xx_0, 0), (self.blocks_complex_to_real_0, 0))
        self.connect((self.digital_binary_slicer_fb_0, 0), (self.epy_block_0, 0))
        self.connect((self.epy_block_0, 0), (self, 0))
        self.connect((self, 0), (self.blocks_add_xx_0, 0))
        self.connect((self, 0), (self.blocks_selector_0, 0))
        self.connect((self, 1), (self.blocks_multiply_const_vxx_0, 0))


    def get_bit_rate(self):
        return self.bit_rate

    def set_bit_rate(self, bit_rate):
        self.bit_rate = bit_rate

    def get_dc_len(self):
        return self.dc_len

    def set_dc_len(self, dc_len):
        self.dc_len = dc_len

    def get_payload_len(self):
        return self.payload_len

    def set_payload_len(self, payload_len):
        self.payload_len = payload_len

    def get_preamble(self):
        return self.preamble

    def set_preamble(self, preamble):
        self.preamble = preamble

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

    def get_threshold(self):
        return self.threshold

    def set_threshold(self, threshold):
        self.threshold = threshold
        self.blocks_add_const_vxx_0.set_k((-self.threshold))

