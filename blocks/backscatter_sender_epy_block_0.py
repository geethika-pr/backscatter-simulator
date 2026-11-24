import numpy as np
from gnuradio import gr

class reader_pie_encoder(gr.basic_block):
    """
    Outputs plain carrier for warmup_us (optional reader warm-up), then the
    specified command sequence once, then continuous unmodulated carrier
    (1.0) indefinitely.
    """
    def __init__(self, samp_rate=2e6, tari_us=12.5, cmd_len=4, warmup_us=0.0):
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
        # Reader warm-up: plain carrier for this long BEFORE the command, the
        # way a real reader powers the field up before talking to tags. Tags
        # charge up during it, and the reader's own receiver (DC blocker)
        # settles on its carrier leakage before any tag replies. 0 = off
        # (command at the very first sample, the old behaviour).
        self.warmup_samples = max(0, int(round(float(warmup_us) * 1e-6 * self.samp_rate)))

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
            buf = [1.0 + 0.0j] * self.warmup_samples
            if self.warmup_samples > 0:
                # Delimiter: one short dip right after the warm-up, so the
                # tag's command detector closes off the long plain-carrier
                # stretch as a separate (ignored) symbol. Without it the
                # warm-up merges into the first command bit, the tag reads
                # e.g. 1101 instead of 0101, and never wakes (a real reader
                # likewise starts every command with a delimiter).
                buf.extend([0.05 + 0.0j] * self.notch_samples)
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
