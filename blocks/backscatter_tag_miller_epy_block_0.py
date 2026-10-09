import numpy as np
from gnuradio import gr

class miller_encoder(gr.interp_block):
    """
    EPC Gen2 Miller Subcarrier Encoder (M=2, 4, 8)
    Inputs: Byte (0 or 1)
    Outputs: Float (+1.0 or -1.0)
    """
    def __init__(self, M=2):
        if M not in [2, 4, 8]:
            raise ValueError("M must be 2, 4, or 8")
        self.M = int(M)
        self.interp_factor = 2 * self.M

        gr.interp_block.__init__(
            self,
            name=f'Miller Encoder (M={self.M})',
            in_sig=[np.uint8],
            out_sig=[np.float32],
            interp=self.interp_factor
        )
        # Subcarrier phase state: +1.0 or -1.0
        self.phase = 1.0
        self.prev_bit = 1

    def work(self, input_items, output_items):
        in_data = input_items[0]
        out_data = output_items[0]

        for i, bit in enumerate(in_data):
            # Rule 1: Boundary transition if bit is 0 and previous bit was 0
            if bit == 0 and self.prev_bit == 0:
                self.phase = -self.phase

            for half_cycle in range(self.interp_factor):
                # Rule 2: Mid-bit transition for bit 1 (occurs at M half-cycles)
                if bit == 1 and half_cycle == self.M:
                    self.phase = -self.phase

                out_data[i * self.interp_factor + half_cycle] = self.phase

                # Alternate subcarrier on every half-cycle boundary
                self.phase = -self.phase

            self.prev_bit = bit

        return len(out_data)
