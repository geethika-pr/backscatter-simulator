import numpy as np
from gnuradio import gr

class blk(gr.interp_block):
    def __init__(self, dummy_param=1.0):  # Added dummy parameter to satisfy GRC parser
        # Input is Magenta (Bytes), Output is Orange (Float)
        gr.interp_block.__init__(
            self,
            name='FM0 Encoder',
            in_sig=[np.uint8],
            out_sig=[np.float32],
            interp=2  # interp_block expects 'interp' instead of 'interpolation'
        )
        self.state = 0.0

    def work(self, input_items, output_items):
        in_data = input_items[0]
        out_data = output_items[0]

        for i in range(len(in_data)):
            bit = in_data[i]

            # Rule 1: ALWAYS transition at the start of the bit
            self.state = 1.0 if self.state == 0.0 else 0.0
            out_data[2*i] = self.state

            # Rule 2: If the data is a 0, transition AGAIN in the middle
            if bit == 0:
                self.state = 1.0 if self.state == 0.0 else 0.0

            out_data[2*i + 1] = self.state

        return len(out_data)
