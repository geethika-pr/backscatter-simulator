"""
Closed-form theoretical BER-vs-SNR reference curves. Zero GNU Radio
dependency (same "pure math, no gnuradio" convention as message_
framer.py) -- shared by `ui/canvas_window.py`'s in-app "Show BER vs SNR
Plot" button and the standalone `ui/plot_ber_vs_snr.py` script, so the
formulas live in exactly one place rather than two copies that could
drift apart.

Three curves, each clearly labeled by its own assumption, NONE presented
as "the" right one for this project's actual receiver chain:

  - BPSK, coherent detection:       Pe = 0.5 * erfc(sqrt(SNR_linear))
  - OOK, noncoherent (envelope):    Pe = 0.5 * exp(-SNR_linear / 4)
  - OOK, coherent (optimal thresh): Pe = 0.5 * erfc(sqrt(SNR_linear / 2))

rx_ook/rx_bpsk use a stock digital.binary_slicer_fb ahead of which this
project's own demod chain sits (see claude/ber-measurement.md) -- which
curve that chain actually resembles hasn't been independently verified,
so BOTH OOK variants are offered side by side rather than silently
picking one.

FM0/Miller are deliberately NOT here: they're line codes layered on top
of amplitude modulation for clock-recovery/error-detection robustness,
not modulations with their own simple closed-form BER-vs-SNR formula --
the "coding gain" they provide depends on the specific decoder, which
isn't something to assert without a validated reference. Comparing
FM0/Miller against SNR is a job for the EMPIRICAL side of this feature
(capture real measured points at a few SNRs) rather than a theoretical
curve, so there's nothing to add here for them.
"""
import math


def ber_bpsk_coherent(snr_db):
    snr_linear = 10 ** (snr_db / 10.0)
    return 0.5 * math.erfc(math.sqrt(snr_linear))


def ber_ook_noncoherent(snr_db):
    snr_linear = 10 ** (snr_db / 10.0)
    return 0.5 * math.exp(-snr_linear / 4.0)


def ber_ook_coherent(snr_db):
    snr_linear = 10 ** (snr_db / 10.0)
    return 0.5 * math.erfc(math.sqrt(snr_linear / 2.0))


# key -> (display label, function). Dict order is draw/legend order.
THEORETICAL_CURVES = {
    "bpsk_coherent": ("BPSK, coherent", ber_bpsk_coherent),
    "ook_noncoherent": ("OOK, noncoherent (envelope)", ber_ook_noncoherent),
    "ook_coherent": ("OOK, coherent (optimal threshold)", ber_ook_coherent),
}
