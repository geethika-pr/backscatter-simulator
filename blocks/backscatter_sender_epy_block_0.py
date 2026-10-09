import numpy as np
from gnuradio import gr

class reader_pie_encoder(gr.basic_block):
    """
    Encodes only the specified command sequence once, then outputs
    continuous unmodulated carrier (1.0) indefinitely.
    """
    def __init__(self, samp_rate=2e6, tari_us=12.5, cmd_len=4):
        gr.basic_block.__init__(
            self,
            name="Reader PIE Encoder",
            in_sig=[np.uint8],
            out_sig=[np.complex64]
        )
        self.samp_rate = float(samp_rate)
        self.tari_samples = int(tari_us * 1e-6 * self.samp_rate)
        self.notch_samples = max(2, int(0.5 * self.tari_samples))
        self.cmd_len = int(cmd_len)

        self.burst_built = False
        self.burst_buf = []
        self.burst_idx = 0

    def general_work(self, input_items, output_items):
        in_bytes = input_items[0]
        out = output_items[0]
        n_out = len(out)
        n_in = len(in_bytes)

        # 1. Build the PIE pulse sequence strictly for cmd_len bits once
        if not self.burst_built and n_in >= self.cmd_len:
            buf = []
            # Slice strictly the first cmd_len bits (e.g. 4 bits)
            target_bits = in_bytes[:self.cmd_len]
            for bit in target_bits:
                total_samples = self.tari_samples if bit == 0 else int(1.75 * self.tari_samples)
                high_samples = total_samples - self.notch_samples
                buf.extend([1.0 + 0.0j] * high_samples)
                buf.extend([0.05 + 0.0j] * self.notch_samples)

            self.burst_buf = np.array(buf, dtype=np.complex64)
            self.burst_built = True

        # Always drain incoming byte stream to prevent buffer stalls
        if n_in > 0:
            self.consume(0, n_in)

        # 2. Output the burst
        written = 0
        if self.burst_built and self.burst_idx < len(self.burst_buf):
            remaining = len(self.burst_buf) - self.burst_idx
            to_copy = min(n_out, remaining)
            out[:to_copy] = self.burst_buf[self.burst_idx : self.burst_idx + to_copy]
            self.burst_idx += to_copy
            written += to_copy

        # 3. Fill remaining space (and all subsequent calls) with continuous wave (1.0)
        if written < n_out:
            out[written:] = 1.0 + 0.0j

        return n_out
