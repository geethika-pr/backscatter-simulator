# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Sender
# GNU Radio version: 3.10.12.0

from gnuradio import analog
from gnuradio import blocks
from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.fft import window
import sys
import signal
import backscatter_sender_epy_block_0 as epy_block_0  # embedded python block
import threading







class backscatter_sender(gr.hier_block2):
    def __init__(self, carrier_freq_send=0, command_bits=(0, 1, 0, 1), enable_gated=0, phase_noise_std_send=0, samp_rate_send=2e6, tari=12.5, tx_power_send=20):
        gr.hier_block2.__init__(
            self, "Sender",
                gr.io_signature(0, 0, 0),
                gr.io_signature.makev(2, 2, [gr.sizeof_gr_complex*1, gr.sizeof_gr_complex*1]),
        )

        ##################################################
        # Parameters
        ##################################################
        self.carrier_freq_send = carrier_freq_send
        self.command_bits = command_bits
        self.enable_gated = enable_gated
        self.phase_noise_std_send = phase_noise_std_send
        self.samp_rate_send = samp_rate_send
        self.tari = tari
        self.tx_power_send = tx_power_send

        ##################################################
        # Blocks
        ##################################################

        self.epy_block_0 = epy_block_0.reader_pie_encoder(samp_rate=samp_rate_send, tari_us=tari, cmd_len=len(command_bits))
        self.blocks_vector_source_x_0 = blocks.vector_source_b(command_bits, True, 1, [])
        self.blocks_selector_0 = blocks.selector(gr.sizeof_gr_complex*1,enable_gated,0)
        self.blocks_selector_0.set_enabled(True)
        self.blocks_multiply_xx_0_0 = blocks.multiply_vcc(1)
        self.blocks_multiply_xx_0 = blocks.multiply_vcc(1)
        self.analog_sig_source_x_0 = analog.sig_source_c(samp_rate_send, analog.GR_COS_WAVE, carrier_freq_send, tx_power_send, 0, 0)
        self.analog_phase_modulator_fc_0 = analog.phase_modulator_fc(1.0)
        self.analog_noise_source_x_0 = analog.noise_source_f(analog.GR_GAUSSIAN, phase_noise_std_send, 0)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_noise_source_x_0, 0), (self.analog_phase_modulator_fc_0, 0))
        self.connect((self.analog_phase_modulator_fc_0, 0), (self.blocks_multiply_xx_0_0, 1))
        self.connect((self.analog_sig_source_x_0, 0), (self.blocks_multiply_xx_0_0, 0))
        self.connect((self.blocks_multiply_xx_0, 0), (self.blocks_selector_0, 1))
        self.connect((self.blocks_multiply_xx_0_0, 0), (self.blocks_multiply_xx_0, 0))
        self.connect((self.blocks_multiply_xx_0_0, 0), (self.blocks_selector_0, 0))
        self.connect((self.blocks_selector_0, 0), (self, 0))
        self.connect((self.blocks_selector_0, 0), (self, 1))
        self.connect((self.blocks_vector_source_x_0, 0), (self.epy_block_0, 0))
        self.connect((self.epy_block_0, 0), (self.blocks_multiply_xx_0, 1))


    def get_carrier_freq_send(self):
        return self.carrier_freq_send

    def set_carrier_freq_send(self, carrier_freq_send):
        self.carrier_freq_send = carrier_freq_send
        self.analog_sig_source_x_0.set_frequency(self.carrier_freq_send)

    def get_command_bits(self):
        return self.command_bits

    def set_command_bits(self, command_bits):
        self.command_bits = command_bits
        self.blocks_vector_source_x_0.set_data(self.command_bits, [])
        self.epy_block_0.cmd_len = len(self.command_bits)

    def get_enable_gated(self):
        return self.enable_gated

    def set_enable_gated(self, enable_gated):
        self.enable_gated = enable_gated
        self.blocks_selector_0.set_input_index(self.enable_gated)

    def get_phase_noise_std_send(self):
        return self.phase_noise_std_send

    def set_phase_noise_std_send(self, phase_noise_std_send):
        self.phase_noise_std_send = phase_noise_std_send
        self.analog_noise_source_x_0.set_amplitude(self.phase_noise_std_send)

    def get_samp_rate_send(self):
        return self.samp_rate_send

    def set_samp_rate_send(self, samp_rate_send):
        self.samp_rate_send = samp_rate_send
        self.analog_sig_source_x_0.set_sampling_freq(self.samp_rate_send)
        self.epy_block_0.samp_rate = self.samp_rate_send

    def get_tari(self):
        return self.tari

    def set_tari(self, tari):
        self.tari = tari

    def get_tx_power_send(self):
        return self.tx_power_send

    def set_tx_power_send(self, tx_power_send):
        self.tx_power_send = tx_power_send
        self.analog_sig_source_x_0.set_amplitude(self.tx_power_send)

