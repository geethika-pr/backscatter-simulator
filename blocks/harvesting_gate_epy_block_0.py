import numpy as np
from gnuradio import gr

class harvesting_gate(gr.sync_block):
    """
    Checks incoming RF power against a minimum harvesting threshold (p_th).
    If Pin < p_th, clamps m(t) to 0.0 (tag chip is unpowered / dead).
    """
    def __init__(self, p_th=0.001, alpha=0.01):
        gr.sync_block.__init__(
            self,
            name="Harvesting Gate",
            in_sig=[np.complex64, np.float32],  # in0: RF_In, in1: m(t)
            out_sig=[np.float32]                 # out: gated m(t)
        )
        self.p_th = float(p_th)        # Turn-on power threshold (linear scale)
        self.alpha = float(alpha)      # Smoothing factor for power estimation
        self.avg_pwr = 0.0

    def work(self, input_items, output_items):
        rf_in = input_items[0]
        m_t = input_items[1]
        out = output_items[0]

        # Calculate instantaneous power of the incoming carrier
        current_pwr = float(np.mean(np.real(rf_in)**2 + np.imag(rf_in)**2))

        # Smooth with EMA to prevent instantaneous phase/noise dips from flickering the tag
        self.avg_pwr = (1.0 - self.alpha) * self.avg_pwr + self.alpha * current_pwr

        # Gate the modulating control signal
        if self.avg_pwr >= self.p_th:
            out[:] = m_t
        else:
            out[:] = 0.0

        return len(out)
