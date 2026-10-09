import numpy as np
from gnuradio import gr


class blk(gr.basic_block):
    def __init__(self, samp_rate=2e6, bit_rate=10e3, payload_len_bits=16, preamble="10101011"):
        self.sps = int(samp_rate / bit_rate)
        self.preamble_bits = [int(b) for b in preamble]
        # Preamble gets re-emitted into the output stream (packet_hex_decoder's
        # own SEARCHING state expects to see it there) plus the payload --
        # knowing this exact total is what replaces guessing "has the burst
        # ended" from the raw signal.
        self.packet_len_bits = len(self.preamble_bits) + int(payload_len_bits)

        gr.basic_block.__init__(
            self, name='FM0 Decoder (synced)',
            in_sig=[np.uint8], out_sig=[np.uint8],
        )
        self.buf = []
        self.prev_sample = None
        self.same_run = 0
        self.dead_run_threshold = 3 * self.sps
        self.active = False

        self.locked = False
        self.phase_offset = 0
        self.locked_buf = []
        self.bits_emitted = 0  # since the current lock was acquired

    def _decode_bit(self, window):
        idx1 = int(self.sps * 0.25)
        idx2 = int(self.sps * 0.75)
        return 1 if window[idx1] == window[idx2] else 0

    def _try_lock(self):
        needed = self.sps * len(self.preamble_bits)
        for offset in range(self.sps):
            if offset + needed > len(self.buf):
                break
            bits = [self._decode_bit(self.buf[offset + k * self.sps: offset + (k + 1) * self.sps])
                    for k in range(len(self.preamble_bits))]
            if bits == self.preamble_bits:
                self.phase_offset = offset
                return True
        return False

    def _unlock(self):
        self.active = False
        self.locked = False
        self.buf = []
        self.locked_buf = []
        self.bits_emitted = 0

    def general_work(self, input_items, output_items):
        in_data = input_items[0]
        out_data = output_items[0]
        n_produced = 0

        for sample in in_data:
            if self.prev_sample is not None and sample == self.prev_sample:
                self.same_run += 1
            else:
                self.same_run = 0
            self.prev_sample = sample

            if self.same_run >= self.dead_run_threshold:
                self._unlock()

            if not self.locked:
                self.buf.append(sample)
                if not self.active and self.same_run == 0:
                    self.active = True
                if self.active:
                    needed = self.sps * (len(self.preamble_bits) + 1)
                    if len(self.buf) >= needed:
                        if self._try_lock():
                            self.locked = True
                            self.locked_buf = self.buf[self.phase_offset:]
                            self.buf = []
                            self.bits_emitted = 0
                        else:
                            self.buf = self.buf[self.sps:]
                continue

            self.locked_buf.append(sample)
            if len(self.locked_buf) >= self.sps:
                window = self.locked_buf[:self.sps]
                if n_produced < len(out_data):
                    out_data[n_produced] = self._decode_bit(window)
                    n_produced += 1
                self.locked_buf = self.locked_buf[self.sps:]
                self.bits_emitted += 1

                if self.bits_emitted >= self.packet_len_bits:
                    # Full packet decoded at this lock -- force a fresh
                    # search before the next bit, so a different tag's
                    # later burst can never ride on this stale phase.
                    self._unlock()

            if n_produced >= len(out_data):
                break

        self.consume(0, len(in_data))
        return n_produced
