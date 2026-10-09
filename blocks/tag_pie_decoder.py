# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Tag PIE Decoder
# GNU Radio version: 3.10.12.0

from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.fft import window
import sys
import signal
import tag_pie_decoder_epy_block_0 as epy_block_0  # embedded python block
import threading







class tag_pie_decoder(gr.hier_block2):
    def __init__(self, bit_rate=10e3, enable_mac=1, packet_bits=16, samp_rate=2e6, slot_id=0, tari=12.5, threshold=0.6):
        gr.hier_block2.__init__(
            self, "Tag PIE Decoder",
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
                gr.io_signature(1, 1, gr.sizeof_float*1),
        )

        ##################################################
        # Parameters
        ##################################################
        self.bit_rate = bit_rate
        self.enable_mac = enable_mac
        self.packet_bits = packet_bits
        self.samp_rate = samp_rate
        self.slot_id = slot_id
        self.tari = tari
        self.threshold = threshold

        ##################################################
        # Blocks
        ##################################################

        self.epy_block_0 = epy_block_0.tag_pie_decoder(samp_rate=samp_rate, bit_rate=bit_rate, packet_len_bits=packet_bits, tari_us=tari, threshold=threshold, enable_mac=enable_mac, slot_id=slot_id)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.epy_block_0, 0), (self, 0))
        self.connect((self, 0), (self.epy_block_0, 0))


    def get_bit_rate(self):
        return self.bit_rate

    def set_bit_rate(self, bit_rate):
        self.bit_rate = bit_rate
        self.epy_block_0.bit_rate = self.bit_rate

    def get_enable_mac(self):
        return self.enable_mac

    def set_enable_mac(self, enable_mac):
        self.enable_mac = enable_mac
        self.epy_block_0.enable_mac = self.enable_mac

    def get_packet_bits(self):
        return self.packet_bits

    def set_packet_bits(self, packet_bits):
        self.packet_bits = packet_bits
        self.epy_block_0.packet_len_bits = self.packet_bits

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.epy_block_0.samp_rate = self.samp_rate

    def get_slot_id(self):
        return self.slot_id

    def set_slot_id(self, slot_id):
        self.slot_id = slot_id
        self.epy_block_0.slot_id = self.slot_id

    def get_tari(self):
        return self.tari

    def set_tari(self, tari):
        self.tari = tari
        self.epy_block_0.tari_us = self.tari

    def get_threshold(self):
        return self.threshold

    def set_threshold(self, threshold):
        self.threshold = threshold
        self.epy_block_0.threshold = self.threshold

