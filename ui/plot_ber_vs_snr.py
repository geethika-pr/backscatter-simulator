#!/usr/bin/env python3
"""
BER-vs-SNR theoretical reference curves -- a quick look at the three
closed-form curves in `core/ber_theory.py` (BPSK coherent, OOK
noncoherent/envelope, OOK coherent/optimal-threshold), no sim run or
venv needed.

For OVERLAYING your own simulator's real measured points on these
curves, use the in-app button instead: each receiver's Fragments dock
in the running app now has "Capture point" (grabs that receiver's
current cumulative BER + the real SNR for its tag's current settings)
and "Show BER vs SNR Plot" (opens this exact same set of curves plus
every point you've captured so far, across as many Start/Stop/restart
cycles as you like -- see CanvasWindow._capture_ber_snr_point()/
_show_ber_vs_snr_plot() in ui/canvas_window.py). That path needs no CSV
export and no hand-written config file -- the app already has every
parameter in memory. This script is now just the "I want to see the
curves themselves, nothing else" quick look (useful on its own, e.g.
for a write-up figure, or on a machine with no gnuradio/PyQt6 at all).

Usage:
    python3 ui/plot_ber_vs_snr.py
    python3 ui/plot_ber_vs_snr.py --out ber_vs_snr.png
    python3 ui/plot_ber_vs_snr.py --curves bpsk_coherent ook_noncoherent
    python3 ui/plot_ber_vs_snr.py --snr-min-db -5 --snr-max-db 15
"""
import argparse
import os
import sys


def _import_curves():
    project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from core.ber_theory import THEORETICAL_CURVES  # noqa: E402
    return THEORETICAL_CURVES


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=None, help="save the figure here instead of opening a window")
    parser.add_argument("--snr-min-db", type=float, default=-10.0)
    parser.add_argument("--snr-max-db", type=float, default=20.0)
    curves_all = _import_curves()
    parser.add_argument("--curves", nargs="+", default=list(curves_all.keys()),
                         choices=list(curves_all.keys()),
                         help="which theoretical curve(s) to draw (default: all three)")
    args = parser.parse_args()

    import numpy as np
    import matplotlib
    if args.out:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    snr_range = np.linspace(args.snr_min_db, args.snr_max_db, 400)
    fig, ax = plt.subplots(figsize=(8, 6))
    for key in args.curves:
        label, fn = curves_all[key]
        # Floor at 1e-300 so a BER of exactly 0 (exp()/erfc() underflow
        # at high SNR) doesn't break the log-scale axis.
        ber_values = [max(fn(s), 1e-300) for s in snr_range]
        ax.semilogy(snr_range, ber_values, label=label, linewidth=1.5)

    ax.set_xlabel("SNR (dB)")
    ax.set_ylabel("Bit Error Rate")
    ax.set_title("BER vs SNR -- theoretical reference curves")
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
