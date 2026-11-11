"""
BER-vs-SNR theory curves, and the matching SNR for a simulated link.
Zero GNU Radio dependency (same "pure math, no gnuradio" convention as
message_framer.py / units.py) -- shared by `ui/canvas_window.py`'s in-app
"Show BER vs SNR Plot" button and the standalone `ui/plot_ber_vs_snr.py`.

ONE SNR DEFINITION FOR EVERYTHING (rewritten 2026-11-11; the first
version mixed three textbook conventions on one axis, and the captured
points used a fourth, so dots and curves were 3-6 dB apart for no
physical reason -- see claude/calibration-plan.md item 3):

    E = a**2 / sigma**2          ("SNR at the decision point")

  a       = HALF the gap between the two levels the receiver's slicer
            has to tell apart (bit 0 level vs bit 1 level), after the
            DC blocker has centred them around zero. Not time-related:
            it's the size of the tag's signal swing as the receiver
            sees it.
  sigma^2 = total power of the return channel's complex noise per
            sample = noise_volt_c1**2 (GNU Radio's channel_model splits
            it evenly between I and Q, so each part has sigma^2/2 --
            ASSUMPTION, check with Test/check_noise_convention.py).

Curves on this axis, one per receiver type that has a textbook answer:

  rx_bpsk (DC blocker -> real part -> slicer at 0):
      levels +a / -a, real-part noise variance sigma^2/2
      Pe = Q(a / sqrt(sigma^2/2)) = Q(sqrt(2E))
  rx_ook (|x| envelope -> DC blocker -> slicer at the average level):
      the two envelope levels are Rician-distributed; the DC blocker
      puts the threshold halfway between their noisy MEANS. Computed
      exactly by numerical integration (ber_ook_envelope), because the
      result depends on how much the tag reflects in its "off" state
      (off_ratio) -- the textbook 0.5*exp(-E) formula assumes nothing
      is reflected when "off" and a different threshold, and was up to
      ~1.2 dB pessimistic for this project's default OOK tag.
  rx_fm0 / rx_miller: no curve. They're line codes decoded by this
      project's own state machines; their error behaviour depends on
      those decoders, so there's no textbook formula to assert. Their
      captured points are still placed on the same E axis (they slice
      the real part, like rx_bpsk) so they can be compared by eye.

Note: on this axis, coherent OOK with an ideal threshold is exactly the
same curve as BPSK (same half-gap, same noise). Textbooks show OOK 3 dB
worse than BPSK because they count the energy spent on the "on" level's
average (Eb/N0); for backscatter that average is the reader's carrier,
not the tag's energy, so the decision-point axis is the fairer one here.
"""
import cmath
import math

import numpy as np


def _q(x):
    """Gaussian tail probability Q(x)."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def ber_bpsk(e_db):
    e = 10 ** (e_db / 10.0)
    return _q(math.sqrt(2.0 * e))


def _log_i0(x):
    """log of the modified Bessel function I0, overflow-safe (numpy's i0
    overflows past x ~ 700; switch to the large-x asymptote there)."""
    x = np.asarray(x, dtype=float)
    small = x < 600.0
    out = np.empty_like(x)
    out[small] = np.log(np.i0(x[small]))
    xl = x[~small]
    out[~small] = xl - 0.5 * np.log(2.0 * np.pi * xl) + np.log1p(1.0 / (8.0 * xl))
    return out


def _rice_pdf(r, nu, s):
    """Envelope |nu + noise| with complex Gaussian noise of s^2 per I/Q part."""
    r = np.asarray(r, dtype=float)
    with np.errstate(divide="ignore"):
        logp = (np.log(np.maximum(r, 1e-300)) - 2.0 * np.log(s)
                - (r * r + nu * nu) / (2.0 * s * s) + _log_i0(r * nu / (s * s)))
    return np.where(r > 0, np.exp(logp), 0.0)


def _integrate(f, lo, hi, n=6001):
    if hi <= lo:
        return 0.0
    r = np.linspace(lo, hi, n)
    return float(np.trapz(f(r), r)) if hasattr(np, "trapz") else float(np.trapezoid(f(r), r))


def ber_ook_envelope(e_db, off_ratio=0.0):
    """Exact BER of rx_ook's chain (|x| envelope -> DC blocker -> slice at 0)
    for equally likely bits, on the decision-point axis E.

    off_ratio = |level0| / |level1|: how strongly the tag reflects in its
    "off" state compared with "on" (0 = textbook OOK, nothing reflected).
    The DC blocker puts the threshold at the AVERAGE envelope, i.e. halfway
    between the two states' mean envelopes (noise included), which is what
    makes this differ from the textbook 0.5*exp(-E) approximation.
    Computed by numerically integrating the two Rician distributions.
    """
    e = 10 ** (e_db / 10.0)
    off_ratio = min(max(float(off_ratio), 0.0), 0.999)
    # scale so that the half-gap a = 1: levels nu0, nu1 with nu1 - nu0 = 2
    nu1 = 2.0 / (1.0 - off_ratio)
    nu0 = nu1 * off_ratio
    sigma = 1.0 / math.sqrt(e)          # total complex noise std (a = 1)
    s = sigma / math.sqrt(2.0)          # per I/Q part
    top = nu1 + 14.0 * s + 1.0
    mean0 = _integrate(lambda r: r * _rice_pdf(r, nu0, s), 0.0, nu0 + 14.0 * s + 1.0)
    mean1 = _integrate(lambda r: r * _rice_pdf(r, nu1, s), 0.0, top)
    thr = 0.5 * (mean0 + mean1)
    p_off_wrong = _integrate(lambda r: _rice_pdf(r, nu0, s), thr, max(thr, nu0) + 16.0 * s + 1.0)
    p_on_wrong = _integrate(lambda r: _rice_pdf(r, nu1, s), 0.0, thr)
    return max(0.0, 0.5 * (p_off_wrong + p_on_wrong))


def ook_off_ratio(gamma0, gamma1, a_s=0j):
    """off_ratio for ber_ook_envelope() from a tag's reflection settings
    (the weaker of the two envelope levels over the stronger)."""
    l0 = abs(a_s - gamma0)
    l1 = abs(a_s - gamma1)
    hi, lo = max(l0, l1), min(l0, l1)
    return 0.0 if hi <= 0 else lo / hi


# key -> (display label, function of E in dB). ook_envelope also takes
# off_ratio (see ber_ook_envelope); called with one argument it's the
# textbook case (nothing reflected when "off").
THEORETICAL_CURVES = {
    "bpsk": ("BPSK, coherent (matches rx_bpsk)", ber_bpsk),
    "ook_envelope": ("OOK, envelope detector (matches rx_ook)", ber_ook_envelope),
}

# receiver node type -> which curve it should be compared against
# (None: no textbook curve, points only)
CURVE_FOR_RX_TYPE = {
    "rx_bpsk": "bpsk",
    "rx_ook": "ook_envelope",
    "rx_fm0": None,
    "rx_miller": None,
}

# which receivers decide on the envelope |x| vs on the real part
ENVELOPE_RX_TYPES = {"rx_ook"}


def decision_half_gap(rx_type, carrier_amp_at_rx, gamma0, gamma1, a_s=0j):
    """`a` in the docstring above: half the gap between the bit-0 and
    bit-1 levels the slicer sees.

    carrier_amp_at_rx: tx amplitude * forward Friis gain * return Friis
        gain -- the amplitude a perfect (|Gamma| = 1) reflector would
        produce at the receiver.
    gamma0, gamma1: the tag's two complex reflection coefficients.
    a_s: the tag's static structural-scattering term (as_real + j*as_imag).

    Mirrors complex_gamma_modulator: reflected wave = carrier *
    (A_s - Gamma(m)). The carrier is real (sig_source_c at 0 Hz offset)
    and the channel gains are real, so no extra phase is applied here
    (calibration-plan item 6 will change that).
    """
    level0 = a_s - gamma0
    level1 = a_s - gamma1
    if rx_type in ENVELOPE_RX_TYPES:
        gap = abs(abs(level1) - abs(level0))
    else:
        # complex DC blocker removes the mean, then complex_to_real keeps
        # the real part of the remaining +/- swing
        gap = abs((level1 - level0).real)
    return carrier_amp_at_rx * gap / 2.0


def decision_snr_db(rx_type, carrier_amp_at_rx, gamma0, gamma1, a_s, noise_volt, n_avg=1):
    """E in dB for one link. +inf with no noise, -inf with no signal.

    n_avg: length of the receiver's moving average before the slicer
    (calibration-plan item 7). Averaging n independent noise samples
    divides noise power by n while a steady level is unchanged, so E gains
    10*log10(n). That's the ideal gain: it assumes the averaging window
    sits entirely inside one steady stretch of the signal, which the
    auto length (half the shortest steady stretch) is chosen to allow but
    can't guarantee without symbol timing. For rx_ook the average is taken
    AFTER the envelope, so the exact Rician curve becomes an approximation
    once n_avg > 1 (good at moderate/high SNR)."""
    if noise_volt <= 0:
        return float("inf")
    a = decision_half_gap(rx_type, carrier_amp_at_rx, gamma0, gamma1, a_s)
    if a <= 0:
        return float("-inf")
    return (10.0 * math.log10(a * a / (noise_volt * noise_volt))
            + 10.0 * math.log10(max(1, int(n_avg))))


def recommended_avg_len(rx_type, bit_rate, samp_rate=2e6, miller_m=2):
    """Auto averaging length. Shortest stretch where the signal holds one
    level: a whole bit for OOK/BPSK (NRZ), half a bit for FM0 (it can flip
    mid-bit), 1/(2M) of a bit for Miller-M (M subcarrier cycles per bit).
    OOK/BPSK: half of it (the decision point can then sit anywhere in the
    second half of the bit). FM0/Miller: a QUARTER of it, because their
    decoders sample at fixed quarter-points of each half-period; a
    simulation of the real FM0 decoder (2026-11-11) showed sps/4 (=50 at
    10 kbps) leaves an error floor at high SNR while sps/8 (=25) doesn't.
    Returns >= 1."""
    try:
        sps = float(samp_rate) / float(bit_rate)
    except (TypeError, ValueError, ZeroDivisionError):
        return 1
    if rx_type == "rx_fm0":
        shortest = sps / 2.0
    elif rx_type == "rx_miller":
        shortest = sps / (2.0 * max(1, int(miller_m or 2)))
    else:
        return max(1, int(sps // 2))
    return max(1, int(shortest // 4))


def gamma_from_polar(mag, phase_deg):
    return cmath.rect(float(mag), math.radians(float(phase_deg)))
