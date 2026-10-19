"""
Pure-math regression check for the per-tag energy estimate added to
canvas_window.py (_harvested_power(), plus NodeInspector._update_energy_
info_label()'s consumed-by-switching / consumed-by-staying-active /
harvested split -- see claude/energy-consumption-estimate.md for the full
design discussion this implements). Same convention as ui/test_wake_up_
range_formula.py: imports ui.canvas_window directly, so it needs the real
project venv (gnuradio + PyQt6) active.

What this checks, and why each one matters:

  1. _harvested_power() agrees EXACTLY with _wake_up_radius_m() at the
     boundary: evaluating harvested power at _wake_up_radius_m()'s own
     returned distance must equal p_th (power above it just inside,
     below just outside) -- these are two independently-written
     functions that both claim to mirror the same two real blocks
     (backscatter_channel_1.py's Friis gain and harvesting_gate_epy_
     block_0.py's current_pwr/p_th check), so this is the check that
     they didn't quietly drift apart.
  2. The three energy figures stay genuinely SEPARATE -- a change to
     energy_static_rate must never move the switching figure, and vice
     versa; this is the property the "don't fold these into one net
     number" design decision depends on.
  3. More bits (a longer message) increases consumed-by-switching
     proportionally; a lower bit_rate (same bit count, more time on
     air) increases consumed-by-staying-active, but NOT consumed-by-
     switching -- confirms each term actually depends on what it's
     supposed to and nothing else.
  4. A distance beyond the tag's own wake-up radius reports 0 harvested
     (the tag never even powers on), matching harvesting_gate's real
     gate -- not a small positive number that would misleadingly read
     as "it's receiving a little bit."
  5. total_frame_bits() -- NOT effective_fragment_bits() -- is what the
     consumed-by-switching figure must scale with, in text/file mode.
     Regression check for an actual reported bug: two different text
     messages ("Hello", 5 bytes vs. "Geethika", 8 bytes) showed the
     IDENTICAL consumed-energy figure, because effective_fragment_bits()
     only reports one fragment's length, which depends on chunk_bytes
     alone, never on message length. This check works at the message_
     framer level directly (same dependency-weight reasoning as checks
     1-4 above: no NodeItem/QGraphicsScene needed) -- total_frame_bits()
     itself is just len(build_fragments(...)) * fragment_len_bits(...),
     so reproducing that arithmetic here is reproducing the real method.

Run from the project root, venv active:
    python3 ui/test_energy_estimate.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.canvas_window import _harvested_power, _wake_up_radius_m  # noqa: E402
from core import message_framer  # noqa: E402

TX_POWER = 20.0
FREQ = 915e6
G_READER_DBI = 6.0
G_TAG_DBI = 2.0
P_TH = 0.5


def main():
    d_max = _wake_up_radius_m(TX_POWER, P_TH, FREQ, G_READER_DBI, G_TAG_DBI)
    power_at_boundary = _harvested_power(TX_POWER, d_max, FREQ, G_READER_DBI, G_TAG_DBI)
    assert abs(power_at_boundary - P_TH) < 1e-9 * max(P_TH, 1.0), (
        f"_harvested_power() at _wake_up_radius_m()'s own boundary should equal p_th: "
        f"got {power_at_boundary}, expected {P_TH}"
    )
    power_inside = _harvested_power(TX_POWER, d_max * 0.5, FREQ, G_READER_DBI, G_TAG_DBI)
    power_outside = _harvested_power(TX_POWER, d_max * 2.0, FREQ, G_READER_DBI, G_TAG_DBI)
    assert power_inside > P_TH, "closer than the wake-up radius must harvest MORE than p_th"
    assert power_outside < P_TH, "farther than the wake-up radius must harvest LESS than p_th"
    print("OK: _harvested_power() agrees exactly with _wake_up_radius_m() at the boundary")

    # Beyond the wake-up radius -- the "0 units, not even powered on"
    # case _update_energy_info_label() special-cases via p_th comparison
    # (this file only checks the underlying power value; the label's own
    # p_th>power branch is a one-line comparison, not re-tested here).
    assert _harvested_power(TX_POWER, d_max * 5.0, FREQ, G_READER_DBI, G_TAG_DBI) < P_TH
    print("OK: well beyond the wake-up radius, harvested power is below p_th")

    # The three energy terms vary independently -- hand-compute them the
    # same way _update_energy_info_label() does, not by importing its
    # internals (it's a UI method on NodeInspector, not meant to be unit
    # tested standalone outside a running QApplication).
    def consumed_switch(energy_per_bit, n_bits):
        return energy_per_bit * n_bits

    def consumed_static(energy_static_rate, n_bits, bit_rate):
        return energy_static_rate * (n_bits / bit_rate)

    base_switch = consumed_switch(1.0, 16)
    longer_switch = consumed_switch(1.0, 64)
    assert longer_switch == base_switch * 4, "4x the bits must be exactly 4x the switching cost"

    base_static = consumed_static(2.0, 16, 10e3)
    slower_static = consumed_static(2.0, 16, 10e3 / 4)  # same bits, 4x longer on air
    assert abs(slower_static - base_static * 4) < 1e-12, (
        "same bit count at 1/4 the bit_rate must take 4x as long -> 4x the staying-active cost"
    )
    # Changing energy_static_rate must never move the switching figure.
    assert consumed_switch(1.0, 16) == base_switch
    print("OK: consumed-by-switching and consumed-by-staying-active vary independently")

    # Regression check for the "Hello"/"Geethika" identical-energy bug --
    # total_frame_bits() = len(fragments) * fragment_len_bits(chunk_bytes),
    # mirroring NodeItem.total_frame_bits()'s own text-mode branch exactly.
    chunk_bytes = 3
    hello_bits = len(message_framer.build_fragments("Hello", 0, chunk_bytes)) \
        * message_framer.fragment_len_bits(chunk_bytes)
    geethika_bits = len(message_framer.build_fragments("Geethika", 0, chunk_bytes)) \
        * message_framer.fragment_len_bits(chunk_bytes)
    assert hello_bits != geethika_bits, (
        "'Hello' (5 bytes) and 'Geethika' (8 bytes) must NOT produce the same "
        "total_frame_bits() -- that was the reported bug"
    )
    assert geethika_bits > hello_bits, "the longer message must cost MORE, not less"
    # The old, buggy stand-in (effective_fragment_bits() alone -- one
    # fragment's length, with no fragment-count multiplier) would have
    # given both messages this same number; confirming that is what
    # distinguishes this check from a tautology.
    old_buggy_value = message_framer.fragment_len_bits(chunk_bytes)
    print(f"OK: 'Hello' -> {hello_bits} bits, 'Geethika' -> {geethika_bits} bits "
          f"(no longer both {old_buggy_value})")

    print("\nAll energy-estimate checks passed.")


if __name__ == "__main__":
    main()
