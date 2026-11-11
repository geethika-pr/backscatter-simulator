#!/usr/bin/env python3
"""
BER-vs-SNR theory curves -- a quick look at the curves in
`core/ber_theory.py`, no sim run or GNU Radio needed (numpy + matplotlib
only). Useful for a write-up figure or on a machine without gnuradio.

Both curves use the SAME SNR definition as the in-app plot:
    SNR = (half the gap between the bit-0 and bit-1 levels)^2 / noise power
measured at the receiver's decision point -- see core/ber_theory.py.

  bpsk          -- matches rx_bpsk (coherent, real-part slicer)
  ook_envelope  -- matches rx_ook (envelope detector + DC blocker); exact
                   result depends on how much the tag reflects when "off"
                   (--off-ratio, 0 = nothing reflected)

For your simulator's own measured points, use the in-app buttons on each
receiver's Fragments pane: "Capture point" after a run, then "Show BER vs
SNR Plot" (draws the curve matching that receiver plus your points).

Usage:
    python3 ui/plot_ber_vs_snr.py
    python3 ui/plot_ber_vs_snr.py --out ber_vs_snr.png
    python3 ui/plot_ber_vs_snr.py --curves ook_envelope --off-ratio 0.16
    python3 ui/plot_ber_vs_snr.py --snr-min-db -5 --snr-max-db 15
"""
import argparse
import os
import sys


def _import_theory():
    project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    import core.ber_theory as ber_theory  # noqa: E402
    return ber_theory


def main():
    bt = _import_theory()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=None, help="save the figure here instead of opening a window")
    parser.add_argument("--snr-min-db", type=float, default=-5.0)
    parser.add_argument("--snr-max-db", type=float, default=15.0)
    parser.add_argument("--off-ratio", type=float, default=0.0,
                        help="OOK only: tag's off-state / on-state reflection level (0..1)")
    parser.add_argument("--curves", nargs="+", default=list(bt.THEORETICAL_CURVES.keys()),
                        choices=list(bt.THEORETICAL_CURVES.keys()),
                        help="which curve(s) to draw (default: both)")
    args = parser.parse_args()

    import numpy as np
    import matplotlib
    if args.out:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    snr_range = np.linspace(args.snr_min_db, args.snr_max_db, 200)
    fig, ax = plt.subplots(figsize=(8, 6))
    for key in args.curves:
        label, fn = bt.THEORETICAL_CURVES[key]
        if key == "ook_envelope":
            ys = [fn(x, args.off_ratio) for x in snr_range]
            label = f"{label}, off/on reflection {args.off_ratio:.2f}"
        else:
            ys = [fn(x) for x in snr_range]
        ax.semilogy(snr_range, [max(y, 1e-12) for y in ys], label=label, linewidth=1.6)

    ax.set_xlabel("SNR at the receiver's decision point (dB)\n"
                  "= (half the gap between the bit-0 and bit-1 levels)² / noise power")
    ax.set_ylabel("Bit error rate (fraction of bits wrong)")
    ax.set_title("BER vs SNR -- theory curves")
    ax.set_ylim(bottom=1e-7)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()

    if args.out:
        fig.savefig(args.out, dpi=150)
        print(f"Saved to {args.out}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
