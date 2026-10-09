# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Packet Hex Decoder
# GNU Radio version: 3.10.12.0

from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.fft import window
import sys
import signal
import packet_hex_decoder_epy_block_0 as epy_block_0  # embedded python block
import threading







class packet_hex_decoder(gr.hier_block2):
    def __init__(self, bit_rate=10e3, packet_queue=None, payload_len_bits=16, preamble='10101011', samp_rate=2e6):
        gr.hier_block2.__init__(
            self, "Packet Hex Decoder",
                gr.io_signature(1, 1, gr.sizeof_char*1),
                gr.io_signature(0, 0, 0),
        )

        ##################################################
        # Parameters
        ##################################################
        self.bit_rate = bit_rate
        self.packet_queue = packet_queue
        self.payload_len_bits = payload_len_bits
        self.preamble = preamble
        self.samp_rate = samp_rate

        ##################################################
        # Blocks
        ##################################################

        self.epy_block_0 = epy_block_0.packet_hex_decoder(preamble=preamble, payload_len_bits=payload_len_bits, samp_rate=samp_rate, bit_rate=bit_rate, packet_queue=packet_queue)


        ##################################################
        # Connections
        ##################################################
        self.connect((self, 0), (self.epy_block_0, 0))


    def get_bit_rate(self):
        return self.bit_rate

    def set_bit_rate(self, bit_rate):
        self.bit_rate = bit_rate
        self.epy_block_0.bit_rate = self.bit_rate

    def get_packet_queue(self):
        return self.packet_queue

    def set_packet_queue(self, packet_queue):
        self.packet_queue = packet_queue
        self.epy_block_0.packet_queue = self.packet_queue

    def get_payload_len_bits(self):
        return self.payload_len_bits

    def set_payload_len_bits(self, payload_len_bits):
        self.payload_len_bits = payload_len_bits
        self.epy_block_0.payload_len_bits = self.payload_len_bits

    def get_preamble(self):
        return self.preamble

    def set_preamble(self, preamble):
        self.preamble = preamble
        self.epy_block_0.preamble = self.preamble

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.epy_block_0.samp_rate = self.samp_rate

