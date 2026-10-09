import numpy as np
from gnuradio import gr

class auto_normalizer(gr.sync_block):
    """
    Dynamically maps arbitrary analog streams to [0.0, 1.0].
    Includes noise floor protection to prevent gain blowup on silence.
    """
    def __init__(self, alpha=0.005, min_spread=0.1):
        gr.sync_block.__init__(
            self,
            name="Auto Normalizer",
            in_sig=[np.float32],
            out_sig=[np.float32]
        )
        self.min_val = -1.0
        self.max_val = 1.0
        self.alpha = float(alpha)
        self.min_spread = float(min_spread)  # Minimum dynamic range to trigger scaling

    def work(self, input_items, output_items):
        in_sig = input_items[0]
        out_sig = output_items[0]

        curr_min = float(np.min(in_sig))
        curr_max = float(np.max(in_sig))

        # Exponential moving average tracking of signal extrema
        self.min_val = (1.0 - self.alpha) * self.min_val + self.alpha * curr_min
        self.max_val = (1.0 - self.alpha) * self.max_val + self.alpha * curr_max

        span = self.max_val - self.min_val

        # Prevent division by zero / gain blowup on near-flat or silent signals
        if span < self.min_spread:
            span = self.min_spread

        # Normalize and hard clamp to [0.0, 1.0]
        norm = (in_sig - self.min_val) / span
        out_sig[:] = np.clip(norm, 0.0, 1.0)

        return len(out_sig)
