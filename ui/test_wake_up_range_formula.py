"""
Pure-math regression check for the wake-up-range visualization added to
canvas_window.py (_wake_up_radius_m() / _tag_identity_color(), and the
NodeItem/CanvasWindow glue that draws them -- see claude/scope-and-
direction.md for the full feature discussion). This file imports
ui.canvas_window directly, same as every other script in this project --
it needs the real project venv (gnuradio + PyQt6) active, same as
Test/test_topology_middleware.py, since canvas_window.py pulls in
core.modulation_middleware at import time regardless of which two
functions this test actually exercises.

What this checks, and why each one matters:

  1. _wake_up_radius_m() actually inverts the two real blocks it claims
     to mirror -- backscatter_channel_1.py's Friis amplitude gain and
     harvesting_gate_epy_block_0.py's avg_pwr>=p_th check -- rather than
     just being a plausible-looking formula. Computed independently
     here (harvesting_power_at()) from the SAME two blocks' own code,
     not copy-pasted from _wake_up_radius_m()'s internals, so a typo in
     one wouldn't silently cancel out in the other.
  2. Just inside the returned radius, the tag is powered (power ratio
     comfortably above p_th); just outside, it isn't -- the ring is
     genuinely a boundary, not off by some constant factor.
  3. p_th<=0 returns float('inf') instead of raising or returning a
     bogus negative/zero -- CanvasWindow._update_wake_ranges() relies on
     this to decide "skip this ring" without a try/except.
  4. _tag_identity_color()'s golden-angle hue stepping is deterministic
     (same order_seq -> same color, every call) and spaces any two
     different order_seqs 137.5 degrees apart (mod 360) in hue -- the
     property the whole "match the ring's color to the tag's margin"
     feature depends on.

Run from the project root, venv active:
    python3 ui/test_wake_up_range_formula.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.canvas_window import _wake_up_radius_m, _tag_identity_color  # noqa: E402

PI = 3.14159265
C_LIGHT = 3e8


def friis_gain(distance, freq, g_reader_dbi, g_tag_dbi):
    """Copied verbatim from blocks/backscatter_channel_1.py's own
    self.friis_gain line (NOT from _wake_up_radius_m() -- this is the
    independent "ground truth" side of the check)."""
    return (10 ** ((g_reader_dbi + g_tag_dbi) / 20.0)) * (C_LIGHT / freq) / (4.0 * PI * max(float(distance), 0.05))


def harvesting_power_at(tx_power_send, distance, freq, g_reader_dbi, g_tag_dbi):
    """Mirrors harvesting_gate_epy_block_0.py's own current_pwr line
    (real**2 + imag**2 of the carrier arriving at the tag) for a tag
    sitting at `distance` meters from the sender, carrier amplitude
    tx_power_send * friis_gain(distance)."""
    amplitude = tx_power_send * friis_gain(distance, freq, g_reader_dbi, g_tag_dbi)
    return amplitude ** 2


def check_radius_is_the_real_boundary(tx_power_send, p_th, freq, g_reader_dbi, g_tag_dbi):
    d_max = _wake_up_radius_m(tx_power_send, p_th, freq, g_reader_dbi, g_tag_dbi)
    assert d_max > 0 and d_max != float("inf"), f"expected a finite positive radius, got {d_max}"

    power_at_boundary = harvesting_power_at(tx_power_send, d_max, freq, g_reader_dbi, g_tag_dbi)
    assert abs(power_at_boundary - p_th) < 1e-9 * max(p_th, 1.0), (
        f"radius {d_max} doesn't actually sit at power==p_th: "
        f"got power={power_at_boundary}, p_th={p_th}"
    )

    power_just_inside = harvesting_power_at(tx_power_send, d_max * 0.9, freq, g_reader_dbi, g_tag_dbi)
    power_just_outside = harvesting_power_at(tx_power_send, d_max * 1.1, freq, g_reader_dbi, g_tag_dbi)
    assert power_just_inside > p_th, "just inside the ring should still be powered"
    assert power_just_outside < p_th, "just outside the ring should NOT be powered"


def main():
    # Default-ish settings (NODE_SEED_OVERRIDES' seeded p_th=0.5, the
    # registry's own sender tx_power_send=20 and channel freq/gains) --
    # the case a freshly-dropped sender+tag actually produces.
    check_radius_is_the_real_boundary(
        tx_power_send=20.0, p_th=0.5, freq=915e6, g_reader_dbi=6.0, g_tag_dbi=2.0)
    print("OK: seeded defaults (p_th=0.5) -- ring is the real wake-up boundary")

    # The registry's own raw listed p_th default (0.001, NOT what a
    # freshly-placed tag actually gets -- see NODE_SEED_OVERRIDES) --
    # much more sensitive tag, much bigger radius, same check should
    # still hold.
    check_radius_is_the_real_boundary(
        tx_power_send=20.0, p_th=0.001, freq=915e6, g_reader_dbi=6.0, g_tag_dbi=2.0)
    print("OK: raw registry default (p_th=0.001) -- ring is the real wake-up boundary")

    # A different sender power / frequency / antenna gains -- not just
    # the one hardcoded combination above.
    check_radius_is_the_real_boundary(
        tx_power_send=5.0, p_th=0.2, freq=433e6, g_reader_dbi=3.0, g_tag_dbi=0.0)
    print("OK: non-default sender/channel settings -- ring is the real wake-up boundary")

    # p_th<=0 -- never-turns-off edge case, must come back as a
    # non-finite radius, not raise or return something that'd draw a
    # bogus ellipse.
    assert _wake_up_radius_m(20.0, 0.0, 915e6, 6.0, 2.0) == float("inf")
    assert _wake_up_radius_m(20.0, -1.0, 915e6, 6.0, 2.0) == float("inf")
    print("OK: p_th<=0 returns float('inf'), not an error or a bogus radius")

    # Color determinism + golden-angle spacing.
    color_a_first = _tag_identity_color(3)
    color_a_second = _tag_identity_color(3)
    assert color_a_first.getRgb() == color_a_second.getRgb(), (
        "same order_seq must produce the exact same color every call"
    )
    hue_0 = _tag_identity_color(0).getHsv()[0]
    hue_1 = _tag_identity_color(1).getHsv()[0]
    expected_hue_1 = int((137.5) % 360.0)
    assert hue_1 == expected_hue_1, f"expected hue {expected_hue_1}, got {hue_1}"
    assert hue_0 != hue_1, "two different tags must not land on the same hue"
    # order_seq=None falls back to seq 0's color rather than crashing.
    assert _tag_identity_color(None).getRgb() == _tag_identity_color(0).getRgb()
    print("OK: _tag_identity_color() is deterministic and golden-angle-spaced")

    print("\nAll wake-up-range checks passed.")


if __name__ == "__main__":
    main()
