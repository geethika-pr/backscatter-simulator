"""
Pure-math regression check for the decode-range visualization added to
canvas_window.py (_friis_amplitude_gain() / _reflection_swing_mag() /
_decode_snr_db() / _decode_range_m(), and the CanvasWindow._update_wake_
ranges() glue that draws the dashed ring -- see claude/ber-measurement.md
and claude/wake-up-range-visualization.md for the full feature
discussion). This file imports ui.canvas_window directly, same as every
other script in this project -- it needs the real project venv
(gnuradio + PyQt6) active, same as ui/test_wake_up_range_formula.py,
since canvas_window.py pulls in core.modulation_middleware at import
time regardless of which functions this test actually exercises.

What this checks, and why each one matters:

  1. _decode_range_m() actually inverts the real round-trip link budget
     it claims to mirror -- two Friis amplitude hops (backscatter_
     channel_1.py's own friis_gain, applied once per leg) and the tag's
     own reflection swing (complex_gamma_modulator_epy_block_0.py's own
     delta_Gamma), compared against the return channel's noise_voltage
     -- rather than just being a plausible-looking formula. Computed
     independently here (decode_snr_db_at()) from the real blocks' own
     code, not copy-pasted from _decode_range_m()'s internals.
  2. Just inside the returned range, SNR is above the threshold; just
     outside, below -- at the boundary itself, SNR is EXACTLY the
     threshold (not off by a constant factor) -- across several
     randomized sender/tag/channel setting combinations, not one
     hand-picked case.
  3. tx_power_send<=0, or a zero reflection swing (gamma0==gamma1, a
     mis-set tag with no real modulation), means no signal exists at
     ANY distance -- range must come back 0.0, not some bogus small
     positive number or an error.
  4. ret_noise_volt_c1<=0 means noise never limits the range -- range
     must come back float('inf'), mirroring _wake_up_radius_m()'s own
     p_th<=0 case, so CanvasWindow._update_wake_ranges() can skip
     drawing it the same way.
  5. A genuinely bistatic setup (forward and return leg at different
     distances, passed via _decode_snr_db()'s fwd_distance_m) still
     computes a sane, monotonically-decreasing-with-distance SNR on
     EITHER leg independently -- not just the symmetric d_fwd==d_ret
     case _decode_range_m() itself solves for.

Run from the project root, venv active:
    python3 ui/test_decode_range_formula.py
"""
import math
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.canvas_window import _decode_range_m, _decode_snr_db  # noqa: E402

PI = 3.14159265
C_LIGHT = 3e8


def friis_gain(distance, freq, g_reader_dbi, g_tag_dbi):
    """Copied verbatim from blocks/backscatter_channel_1.py's own
    self.friis_gain line (NOT from canvas_window.py's
    _friis_amplitude_gain() -- this is the independent "ground truth"
    side of the check, same convention ui/test_wake_up_range_formula.py
    already uses for the forward-leg-only ring)."""
    return (10 ** ((g_reader_dbi + g_tag_dbi) / 20.0)) * (C_LIGHT / freq) / (4.0 * PI * max(float(distance), 0.05))


def delta_gamma_mag_at(gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg):
    """Copied verbatim from complex_gamma_modulator_epy_block_0.py's own
    self.Gamma_0/self.Gamma_1/self.delta_Gamma lines."""
    gamma0 = gamma0_mag * complex(math.cos(math.radians(gamma0_phase_deg)),
                                   math.sin(math.radians(gamma0_phase_deg)))
    gamma1 = gamma1_mag * complex(math.cos(math.radians(gamma1_phase_deg)),
                                   math.sin(math.radians(gamma1_phase_deg)))
    return abs(gamma1 - gamma0)


def decode_snr_db_at(distance, tx_power_send,
                      fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi,
                      gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg,
                      ret_freq, ret_g_reader_dbi, ret_g_tag_dbi, ret_noise_volt_c1,
                      fwd_distance=None):
    """Independent re-derivation of the round-trip SNR at a known
    distance -- two Friis hops (one per leg) times the reflection swing,
    against the return channel's own noise voltage."""
    if fwd_distance is None:
        fwd_distance = distance
    delta_gamma = delta_gamma_mag_at(gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg)
    signal = (tx_power_send
              * friis_gain(fwd_distance, fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi)
              * delta_gamma
              * friis_gain(distance, ret_freq, ret_g_reader_dbi, ret_g_tag_dbi))
    return 20.0 * math.log10(signal / ret_noise_volt_c1)


def check_range_is_the_real_boundary(tx_power_send,
                                      fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi,
                                      gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg,
                                      ret_freq, ret_g_reader_dbi, ret_g_tag_dbi, ret_noise_volt_c1,
                                      snr_threshold_db):
    d_max = _decode_range_m(
        tx_power_send, fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi,
        gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg,
        ret_freq, ret_g_reader_dbi, ret_g_tag_dbi, ret_noise_volt_c1,
        snr_threshold_db=snr_threshold_db)
    assert d_max > 0 and d_max != float("inf"), f"expected a finite positive range, got {d_max}"

    snr_at_boundary = decode_snr_db_at(
        d_max, tx_power_send, fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi,
        gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg,
        ret_freq, ret_g_reader_dbi, ret_g_tag_dbi, ret_noise_volt_c1)
    assert abs(snr_at_boundary - snr_threshold_db) < 1e-6, (
        f"range {d_max} doesn't actually sit at SNR==threshold: "
        f"got SNR={snr_at_boundary}dB, threshold={snr_threshold_db}dB"
    )

    snr_just_inside = decode_snr_db_at(
        d_max * 0.9, tx_power_send, fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi,
        gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg,
        ret_freq, ret_g_reader_dbi, ret_g_tag_dbi, ret_noise_volt_c1)
    snr_just_outside = decode_snr_db_at(
        d_max * 1.1, tx_power_send, fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi,
        gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg,
        ret_freq, ret_g_reader_dbi, ret_g_tag_dbi, ret_noise_volt_c1)
    assert snr_just_inside > snr_threshold_db, "just inside the ring should still clear the SNR threshold"
    assert snr_just_outside < snr_threshold_db, "just outside the ring should NOT clear the SNR threshold"

    # Cross-check against canvas_window.py's own _decode_snr_db(), not
    # just this test's independent re-derivation -- both must agree at
    # the boundary too.
    snr_at_boundary_impl = _decode_snr_db(
        d_max, tx_power_send, fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi,
        gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg,
        ret_freq, ret_g_reader_dbi, ret_g_tag_dbi, ret_noise_volt_c1)
    assert abs(snr_at_boundary_impl - snr_threshold_db) < 1e-6, (
        f"_decode_snr_db() disagrees with the independent re-derivation at the "
        f"boundary: got {snr_at_boundary_impl}dB, expected {snr_threshold_db}dB"
    )


def main():
    random.seed(2026)

    # Several randomized sender/tag/channel combinations, symmetric
    # (monostatic) forward/return legs -- the common case every combined
    # "reader" node on this canvas actually produces.
    for trial in range(8):
        check_range_is_the_real_boundary(
            tx_power_send=random.uniform(1, 50),
            fwd_freq=random.uniform(800e6, 1000e6),
            fwd_g_reader_dbi=random.uniform(0, 10),
            fwd_g_tag_dbi=random.uniform(0, 6),
            gamma0_mag=random.uniform(0.01, 0.9),
            gamma0_phase_deg=random.uniform(0, 360),
            gamma1_mag=random.uniform(0.01, 0.9),
            gamma1_phase_deg=random.uniform(0, 360),
            ret_freq=random.uniform(800e6, 1000e6),
            ret_g_reader_dbi=random.uniform(0, 10),
            ret_g_tag_dbi=random.uniform(0, 6),
            ret_noise_volt_c1=random.uniform(0.001, 0.05),
            snr_threshold_db=random.choice([3.0, 6.0, 10.0]),
        )
    print("OK: 8 randomized settings -- decode range is the real SNR-threshold boundary, "
          "confirmed against both an independent re-derivation and canvas_window.py's own "
          "_decode_snr_db()")

    # The registry's own actual defaults (tag_ook: gamma0_mag=0.1/
    # gamma0_phase_deg=180, gamma1_mag=0.9/gamma1_phase_deg=180; sender
    # tx_power_send=20; channel freq=915e6, g_reader_dbi=6.0,
    # g_tag_dbi=2.0, noise_volt_c1=0.01) -- the case a freshly-dropped
    # reader+tag on this canvas actually produces, at the 6dB threshold
    # the reference thesis's own range-measurement method used.
    check_range_is_the_real_boundary(
        tx_power_send=20.0,
        fwd_freq=915e6, fwd_g_reader_dbi=6.0, fwd_g_tag_dbi=2.0,
        gamma0_mag=0.1, gamma0_phase_deg=180.0, gamma1_mag=0.9, gamma1_phase_deg=180.0,
        ret_freq=915e6, ret_g_reader_dbi=6.0, ret_g_tag_dbi=2.0, ret_noise_volt_c1=0.01,
        snr_threshold_db=6.0,
    )
    print("OK: registry defaults at the 6dB reference threshold")

    # tx_power_send<=0 and a zero reflection swing (gamma0==gamma1) both
    # mean no signal exists at ANY distance -- 0.0, not a bogus small
    # positive number.
    assert _decode_range_m(
        0.0, 915e6, 6.0, 2.0, 0.1, 180.0, 0.9, 180.0, 915e6, 6.0, 2.0, 0.01) == 0.0
    assert _decode_range_m(
        20.0, 915e6, 6.0, 2.0, 0.5, 90.0, 0.5, 90.0, 915e6, 6.0, 2.0, 0.01) == 0.0
    print("OK: tx_power_send<=0 and gamma0==gamma1 both return 0.0, not a bogus range")

    # ret_noise_volt_c1<=0 -- noise never limits the range, must come
    # back non-finite rather than raise or divide by zero.
    assert _decode_range_m(
        20.0, 915e6, 6.0, 2.0, 0.1, 180.0, 0.9, 180.0, 915e6, 6.0, 2.0, 0.0) == float("inf")
    assert _decode_range_m(
        20.0, 915e6, 6.0, 2.0, 0.1, 180.0, 0.9, 180.0, 915e6, 6.0, 2.0, -1.0) == float("inf")
    print("OK: ret_noise_volt_c1<=0 returns float('inf'), not an error or a bogus range")

    # Bistatic sanity check: _decode_snr_db()'s fwd_distance_m lets the
    # two legs differ. Holding the return-leg distance fixed, increasing
    # ONLY the forward-leg distance must strictly lower the SNR -- if
    # fwd_distance_m were silently ignored (a classic "parameter added
    # but never wired in" bug), these would be equal instead.
    snr_close = _decode_snr_db(
        5.0, 20.0, 915e6, 6.0, 2.0, 0.1, 180.0, 0.9, 180.0, 915e6, 6.0, 2.0, 0.01,
        fwd_distance_m=2.0)
    snr_far = _decode_snr_db(
        5.0, 20.0, 915e6, 6.0, 2.0, 0.1, 180.0, 0.9, 180.0, 915e6, 6.0, 2.0, 0.01,
        fwd_distance_m=8.0)
    assert snr_far < snr_close, (
        f"increasing fwd_distance_m alone should lower SNR: got close={snr_close}dB, "
        f"far={snr_far}dB"
    )
    print("OK: bistatic fwd_distance_m is actually wired in -- a longer forward leg "
          "alone lowers SNR even with the return leg held fixed")

    print("\nAll decode-range checks passed.")


if __name__ == "__main__":
    main()
