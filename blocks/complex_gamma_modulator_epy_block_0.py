import numpy as np
from gnuradio import gr

class complex_gamma_modulator(gr.sync_block):
    def __init__(self,
                 as_real=0.05, as_imag=0.02,
                 gamma0_mag=0.2, gamma0_phase_deg=15.0,
                 gamma1_mag=0.85, gamma1_phase_deg=160.0,
                 drift_pct=0.0, jitter_samples=0.0):
        gr.sync_block.__init__(
            self,
            name="Complex Gamma Modulator",
            in_sig=[np.complex64, np.float32],  # in0: RF Carrier, in1: Modulating Signal m(t)
            out_sig=[np.complex64]
        )
        # RF Reflection Physics Constants
        self.A_s = complex(as_real, as_imag)
        self.Gamma_0 = gamma0_mag * np.exp(1j * np.deg2rad(gamma0_phase_deg))
        self.Gamma_1 = gamma1_mag * np.exp(1j * np.deg2rad(gamma1_phase_deg))
        self.delta_Gamma = self.Gamma_1 - self.Gamma_0

        # Clock Impairment Parameters
        self.drift_pct = float(drift_pct)
        self.jitter_samples = float(jitter_samples)
        self.resample_idx = 0.0

    def work(self, input_items, output_items):
        rf_in = input_items[0]   # Carrier wave from reader/ambient
        m_t = input_items[1]     # Pulse train / analog envelope from tag wrapper
        out = output_items[0]
        n_samples = len(rf_in)

        # 1. Apply Clock Drift & Edge Jitter to the baseband modulating signal
        if self.drift_pct != 0.0 or self.jitter_samples > 0.0:
            step = 1.0 + (self.drift_pct / 100.0)
            indices = self.resample_idx + np.arange(n_samples) * step

            if self.jitter_samples > 0.0:
                indices += np.random.normal(0, self.jitter_samples, size=n_samples)

            indices = np.clip(indices, 0, len(m_t) - 1)
            m_t_effective = m_t[indices.astype(np.int64)]

            # Update index accumulator for the next work() call
            self.resample_idx = (indices[-1] + step) - len(m_t)
            if self.resample_idx < 0:
                self.resample_idx = 0.0
        else:
            m_t_effective = m_t

        # 2. Apply Physical Reflection (Green's Reaction Theorem)
        gamma_total = self.A_s - (self.Gamma_0 + self.delta_Gamma * m_t_effective)

        # 3. Modulate and output reflected RF wave
        out[:] = rf_in * gamma_total
        return n_samples
