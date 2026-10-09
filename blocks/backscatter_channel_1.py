# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Channel
# GNU Radio version: 3.10.12.0

from gnuradio import blocks
from gnuradio import channels
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
import threading







class backscatter_channel_1(gr.hier_block2):
    def __init__(self, distance=2, fading_type_c1=0, freq=915e6, g_reader_dbi=6.0, g_tag_dbi=2.0, noise_volt_c1=0.01, samp_rate_c1=2e6):
        gr.hier_block2.__init__(
            self, "Channel",
                gr.io_signature.makev(2, 2, [gr.sizeof_gr_complex*1, gr.sizeof_gr_complex*1]),
                gr.io_signature(1, 1, gr.sizeof_gr_complex*1),
        )

        ##################################################
        # Parameters
        ##################################################
        self.distance = distance
        self.fading_type_c1 = fading_type_c1
        self.freq = freq
        self.g_reader_dbi = g_reader_dbi
        self.g_tag_dbi = g_tag_dbi
        self.noise_volt_c1 = noise_volt_c1
        self.samp_rate_c1 = samp_rate_c1

        ##################################################
        # Variables
        ##################################################
        self.friis_gain = friis_gain = (10**((g_reader_dbi + g_tag_dbi) / 20.0)) * (3e8 / freq) / (4.0 * 3.14159265 * max(float(distance), 0.05))

        ##################################################
        # Blocks
        ##################################################

        self.channels_fading_model_0_0 = channels.fading_model( 8, (10.0/samp_rate_c1), False, 10.0, 0 )
        self.channels_fading_model_0 = channels.fading_model( 8, (10.0/samp_rate_c1), True, 10.0, 0 )
        self.channels_channel_model_0 = channels.channel_model(
            noise_voltage=noise_volt_c1,
            frequency_offset=0.0,
            epsilon=1.0,
            taps=[1.0],
            noise_seed=0,
            block_tags=False)
        self.blocks_selector_0 = blocks.selector(gr.sizeof_gr_complex*1,fading_type_c1,0)
        self.blocks_selector_0.set_enabled(True)
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_cc(friis_gain)
        self.blocks_add_xx_0 = blocks.add_vcc(1)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.blocks_add_xx_0, 0), (self.channels_channel_model_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.blocks_selector_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.channels_fading_model_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.channels_fading_model_0_0, 0))
        self.connect((self.blocks_selector_0, 0), (self.blocks_add_xx_0, 0))
        self.connect((self.channels_channel_model_0, 0), (self, 0))
        self.connect((self.channels_fading_model_0, 0), (self.blocks_selector_0, 1))
        self.connect((self.channels_fading_model_0_0, 0), (self.blocks_selector_0, 2))
        self.connect((self, 0), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self, 1), (self.blocks_add_xx_0, 1))


    def get_distance(self):
        return self.distance

    def set_distance(self, distance):
        self.distance = distance
        self.set_friis_gain((10**((self.g_reader_dbi + self.g_tag_dbi) / 20.0)) * (3e8 / self.freq) / (4.0 * 3.14159265 * max(float(self.distance), 0.05)))

    def get_fading_type_c1(self):
        return self.fading_type_c1

    def set_fading_type_c1(self, fading_type_c1):
        self.fading_type_c1 = fading_type_c1
        self.blocks_selector_0.set_input_index(self.fading_type_c1)

    def get_freq(self):
        return self.freq

    def set_freq(self, freq):
        self.freq = freq
        self.set_friis_gain((10**((self.g_reader_dbi + self.g_tag_dbi) / 20.0)) * (3e8 / self.freq) / (4.0 * 3.14159265 * max(float(self.distance), 0.05)))

    def get_g_reader_dbi(self):
        return self.g_reader_dbi

    def set_g_reader_dbi(self, g_reader_dbi):
        self.g_reader_dbi = g_reader_dbi
        self.set_friis_gain((10**((self.g_reader_dbi + self.g_tag_dbi) / 20.0)) * (3e8 / self.freq) / (4.0 * 3.14159265 * max(float(self.distance), 0.05)))

    def get_g_tag_dbi(self):
        return self.g_tag_dbi

    def set_g_tag_dbi(self, g_tag_dbi):
        self.g_tag_dbi = g_tag_dbi
        self.set_friis_gain((10**((self.g_reader_dbi + self.g_tag_dbi) / 20.0)) * (3e8 / self.freq) / (4.0 * 3.14159265 * max(float(self.distance), 0.05)))

    def get_noise_volt_c1(self):
        return self.noise_volt_c1

    def set_noise_volt_c1(self, noise_volt_c1):
        self.noise_volt_c1 = noise_volt_c1
        self.channels_channel_model_0.set_noise_voltage(self.noise_volt_c1)

    def get_samp_rate_c1(self):
        return self.samp_rate_c1

    def set_samp_rate_c1(self, samp_rate_c1):
        self.samp_rate_c1 = samp_rate_c1
        self.channels_fading_model_0.set_fDTs((10.0/self.samp_rate_c1))
        self.channels_fading_model_0_0.set_fDTs((10.0/self.samp_rate_c1))

    def get_friis_gain(self):
        return self.friis_gain

    def set_friis_gain(self, friis_gain):
        self.friis_gain = friis_gain
        self.blocks_multiply_const_vxx_0.set_k(self.friis_gain)

