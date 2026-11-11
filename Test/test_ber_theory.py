"""
Checks core/ber_theory.py's curves against a quick Monte Carlo of the
receiver chains they claim to describe (reflection levels -> complex noise
-> DC removal -> real part or envelope -> slice at 0). numpy only, no
GNU Radio. From the project root:
    python3 Test/test_ber_theory.py

Assumes GNU Radio's complex noise of "noise voltage" v has total power
v**2 split evenly over I/Q -- the same assumption ber_theory makes;
Test/check_noise_convention.py checks that one against real GNU Radio.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from core import ber_theory as bt  # noqa: E402

N = 400_000
C = 0.07  # carrier amplitude at the receiver for a perfect reflector

CASES = [  # (name, receiver type, gamma0, gamma1, structural scattering)
    ("BPSK default tag", "rx_bpsk", bt.gamma_from_polar(0.8, 0), bt.gamma_from_polar(0.8, 180), 0j),
    ("OOK default tag", "rx_ook", bt.gamma_from_polar(0.1, 180), bt.gamma_from_polar(0.9, 180), 0.05 + 0j),
    ("OOK Ryu 2026 gammas", "rx_ook", bt.gamma_from_polar(0.018, 0), bt.gamma_from_polar(0.617, 16.8), 0j),
]


def simulate(rng, rx, g0, g1, a_s, e_db):
    bits = rng.integers(0, 2, N)
    clean = C * (a_s - np.where(bits == 1, g1, g0))
    a = bt.decision_half_gap(rx, C, g0, g1, a_s)
    sigma = a / np.sqrt(10 ** (e_db / 10))
    y = clean + sigma * (rng.standard_normal(N) + 1j * rng.standard_normal(N)) / np.sqrt(2)
    if rx in bt.ENVELOPE_RX_TYPES:
        y = np.abs(y)
        y = y - y.mean()
    else:
        y = (y - y.mean()).real
    dec = (y > 0).astype(int)
    errors = min(int(np.sum(dec != bits)), int(np.sum(dec == bits)))
    return errors, sigma


def main():
    rng = np.random.default_rng(3)
    for name, rx, g0, g1, a_s in CASES:
        ratio = bt.ook_off_ratio(g0, g1, a_s)
        for e_target in (0.0, 4.0, 7.0):
            errors, sigma = simulate(rng, rx, g0, g1, a_s, e_target)
            e_db = bt.decision_snr_db(rx, C, g0, g1, a_s, sigma)
            assert abs(e_db - e_target) < 1e-9
            theory = bt.ber_bpsk(e_db) if rx == "rx_bpsk" else bt.ber_ook_envelope(e_db, ratio)
            expected = theory * N
            # allow 5 standard deviations of counting noise (+ a little slack)
            tol = 5 * np.sqrt(expected) + 0.03 * expected
            print(f"{name:20s} SNR {e_db:4.1f} dB: simulated {errors / N:.3e}, theory {theory:.3e}")
            assert abs(errors - expected) <= tol, (name, e_db, errors, expected)
    # decision SNR for BPSK is the ring's full-swing SNR minus 6.02 dB
    g0, g1 = bt.gamma_from_polar(0.8, 0), bt.gamma_from_polar(0.8, 180)
    full_swing_db = 20 * np.log10(C * abs(g1 - g0) / 0.01)
    assert abs(full_swing_db - bt.decision_snr_db("rx_bpsk", C, g0, g1, 0j, 0.01) - 6.0206) < 1e-3
    print("test_ber_theory: all checks passed")


if __name__ == "__main__":
    main()
