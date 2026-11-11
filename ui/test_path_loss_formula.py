"""
Regression check for the path-loss exponent revision (calibration-plan
item 4): backscatter_channel_1 gained `path_loss_exp`, and every Tier-2
copy of its Friis math in canvas_window.py (_friis_amplitude_gain,
_harvested_power, _wake_up_radius_m, _decode_snr_db, _decode_range_m)
gained a matching optional exponent (default 2.0).

Imports ui.canvas_window directly, like the other ui/test_*_formula.py
files, so it needs the project venv (gnuradio + PyQt6). From the project
root:
    python3 ui/test_path_loss_formula.py

What it checks:
  1. _friis_amplitude_gain matches the block's OWN friis_gain expression
     (copied verbatim from backscatter_channel_1.py below) for many
     distances and exponents, including the 0.05 m floor and the 1 m
     switch-over.
  2. With exponent 2 every function gives exactly what the original
     plain-Friis formulas gave (copied below), so old layouts and the
     existing formula tests are unaffected.
  3. _wake_up_radius_m really inverts the harvesting check: received
     power == p_th at the returned radius, above just inside, below just
     outside -- for exponents 2.0/2.34/2.56/3.0 and for radii both below
     and above 1 m.
  4. _decode_range_m really inverts _decode_snr_db: SNR == threshold at
     the returned range, with DIFFERENT forward/return exponents.
  5. A larger exponent never gives a larger range (more loss = shorter).
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ui.canvas_window import (  # noqa: E402
    _decode_range_m, _decode_snr_db, _friis_amplitude_gain, _harvested_power,
    _wake_up_radius_m,
)


def block_friis_gain(distance, freq, g_reader_dbi, g_tag_dbi, path_loss_exp):
    """Verbatim from blocks/backscatter_channel_1.py's friis_gain variable."""
    return (10**((g_reader_dbi + g_tag_dbi) / 20.0)) * (3e8 / freq) / (4.0 * 3.14159265) * (
        1.0 / max(float(distance), 0.05) if float(distance) < 1.0
        else float(distance) ** (-path_loss_exp / 2.0))


def old_friis(distance, freq, g_reader_dbi, g_tag_dbi):
    """The block's friis_gain BEFORE this revision (plain Friis)."""
    return (10**((g_reader_dbi + g_tag_dbi) / 20.0)) * (3e8 / freq) / (4.0 * 3.14159265 * max(float(distance), 0.05))


def close(a, b, rel=1e-9):
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-300)


def main():
    rng = random.Random(42)
    exps = (2.0, 2.34, 2.56, 3.0, 1.8)

    # 1 + 2: one hop vs the block's own expression, and vs old Friis at n=2
    for _ in range(300):
        d = rng.choice([0.01, 0.05, 0.3, 0.999, 1.0, 1.001, 2.5, 7.0, 40.0]) * rng.uniform(0.9, 1.1)
        f = rng.choice([474.5e6, 915e6, 2.45e9])
        gr, gt = rng.uniform(0, 8), rng.uniform(-2, 4)
        n = rng.choice(exps)
        assert close(_friis_amplitude_gain(d, f, gr, gt, n), block_friis_gain(d, f, gr, gt, n)), (d, n)
        assert close(_friis_amplitude_gain(d, f, gr, gt), old_friis(d, f, gr, gt)), d
        assert close(_harvested_power(20.0, d, f, gr, gt), (20.0 * old_friis(d, f, gr, gt)) ** 2)

    # 3: wake radius inverts the harvesting check, both regimes
    for n in exps:
        for tx, p_th in [(20.0, 0.5), (20.0, 50.0), (1.0, 1e-5), (0.316, 1e-4), (20.0, 1e-3)]:
            r = _wake_up_radius_m(tx, p_th, 915e6, 6.0, 2.0, n)
            p_at = _harvested_power(tx, r, 915e6, 6.0, 2.0, n)
            assert close(p_at, p_th, 1e-7), (n, tx, p_th, r, p_at)
            assert _harvested_power(tx, r * 0.99, 915e6, 6.0, 2.0, n) > p_th
            assert _harvested_power(tx, r * 1.01, 915e6, 6.0, 2.0, n) < p_th
        # n = 2 path identical to the old closed form
        k = (10 ** (8 / 20.0)) * (3e8 / 915e6) / (4.0 * 3.14159265)
        assert close(_wake_up_radius_m(20.0, 0.5, 915e6, 6.0, 2.0), 20.0 * k / math.sqrt(0.5))

    # 4: decode range inverts decode SNR, asymmetric exponents
    gam = (0.1, 180.0, 0.9, 180.0)
    for nf in exps:
        for nr in exps:
            for tx, noise, thr in [(20.0, 0.01, 6.0), (1.0, 2e-7, 7.0), (20.0, 0.5, 0.0)]:
                d = _decode_range_m(tx, 915e6, 6.0, 2.0, *gam, 915e6, 6.0, 2.0, noise,
                                    snr_threshold_db=thr, fwd_path_loss_exp=nf, ret_path_loss_exp=nr)
                snr = _decode_snr_db(d, tx, 915e6, 6.0, 2.0, *gam, 915e6, 6.0, 2.0, noise,
                                     fwd_path_loss_exp=nf, ret_path_loss_exp=nr)
                assert abs(snr - thr) < 1e-6, (nf, nr, tx, noise, d, snr)
                if d > 0.06:
                    inside = _decode_snr_db(d * 0.99, tx, 915e6, 6.0, 2.0, *gam, 915e6, 6.0, 2.0, noise,
                                            fwd_path_loss_exp=nf, ret_path_loss_exp=nr)
                    assert inside > thr
    old = (20.0 * 0.8 * (10 ** (8 / 20.0) * (3e8 / 915e6) / (4 * 3.14159265)) ** 2) ** 2 / (0.01 ** 2 * 10 ** 0.6)
    assert close(_decode_range_m(20.0, 915e6, 6.0, 2.0, *gam, 915e6, 6.0, 2.0, 0.01, snr_threshold_db=6.0),
                 old ** 0.25)

    # 5: more loss never means more range
    for p_th in (1e-5, 0.5):
        radii = [_wake_up_radius_m(1.0, p_th, 915e6, 6.0, 2.0, n) for n in (1.8, 2.0, 2.34, 2.56, 3.0)]
        assert all(a >= b for a, b in zip(radii, radii[1:])), radii

    print("test_path_loss_formula: all checks passed")
    for n in (2.0, 2.34, 2.56):
        print(f"  wake radius @ 30 dBm, -20 dBm tag sensitivity, n={n}: "
              f"{_wake_up_radius_m(1.0, 1e-5, 915e6, 6.0, 2.0, n):.1f} m")


if __name__ == "__main__":
    main()
