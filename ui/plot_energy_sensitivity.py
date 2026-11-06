#!/usr/bin/env python3
"""
Energy-model sensitivity plots: explores _switch_energy_per_bit_joules()'s
own two formulas (ui/canvas_window.py -- the FET/CMOS vs BJT switch-
device models from the "which switch technology" energy work; see
claude/energy-consumption-estimate.md) across their input ranges.

WHY THIS DOESN'T NEED CALIBRATION FIRST: this is plotting your own
formulas against THEMSELVES -- Vdd, c_gate_f, r_bias_ohm, duty_cycle,
bit_rate are all free inputs you already expose in the inspector, not
quantities validated against a real tag's datasheet or a measured
experiment. The point here is to see the formulas' own SHAPE (where FET
is flat and BJT isn't, where one crosses over the other) so you can
reason about which switch model to pick for a given scenario -- not to
claim either curve matches real RFID hardware. That's the same
calibration-independent category as the BER-vs-SNR theoretical curves
in ui/plot_ber_vs_snr.py, for the same reason.

Three panels:
  A. FET energy/bit vs Vdd, a few c_gate_f values -- confirms the
     textbook C*V^2 shape (quadratic, bit-rate-independent) that
     ui/test_energy_estimate.py already checks numerically; this just
     makes the SHAPE visible across a range instead of at 1-2 spot
     values.
  B. BJT energy/bit vs bit_rate (log-log), a few duty_cycle values, with
     FET's value at the SAME Vdd/c_gate drawn as flat horizontal
     reference lines -- this is the one that actually matters for
     picking a switch technology: BJT's energy/bit falls as 1/bit_rate
     (spending less TIME biased on per bit as you go faster), while FET
     doesn't care about bit_rate at all, so there's always some
     crossover bit_rate below which BJT is cheaper and above which FET
     is. Printed to stdout per duty_cycle, not just shown visually.
  C. BJT energy/bit vs duty_cycle (linear), a few bit_rate values -- the
     direct, undiluted duty_cycle dependence FET categorically lacks.

Usage:
    python3 ui/plot_energy_sensitivity.py
        Needs this project's venv (gnuradio + PyQt6) active, same
        requirement as every other ui/test_*.py and ui/plot_*.py in
        this directory -- importing ui.canvas_window pulls in
        core.modulation_middleware, which needs gnuradio, even though
        _switch_energy_per_bit_joules() itself has no gnuradio
        dependency at all (it's a pure function -- the import chain is
        the only reason the venv is needed here).
    python3 ui/plot_energy_sensitivity.py --out energy_sensitivity.png
        Save instead of opening a window.
    python3 ui/plot_energy_sensitivity.py --vdd 3.3 --r-bias-ohm 1500
        Override the shared Vdd / BJT bias resistor used across all
        three panels (defaults: 3.0V, 2000 Ohm -- the Kimionis-thesis
        Fig 3.5 reference values _switch_energy_per_bit_joules()'s own
        docstring cites).
"""
import argparse
import os
import sys


def _import_switch_energy_fn():
    """Imported lazily (only once argparse/matplotlib setup is done) so
    --help works even without the venv active."""
    project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ui.canvas_window import _switch_energy_per_bit_joules  # noqa: E402
    return _switch_energy_per_bit_joules


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=None, help="save the figure here instead of opening a window")
    parser.add_argument("--vdd", type=float, default=3.0, help="supply voltage (V), shared across all panels")
    parser.add_argument("--r-bias-ohm", type=float, default=2000.0,
                         help="BJT base-bias resistor (Ohm), panels B/C")
    parser.add_argument("--c-gate-values-ff", type=float, nargs="+", default=[5.0, 15.0, 50.0],
                         help="FET gate capacitances to sweep in panel A (femtofarads)")
    parser.add_argument("--duty-cycle-values", type=float, nargs="+", default=[0.1, 0.5, 1.0],
                         help="BJT duty-cycle values to sweep in panel B")
    parser.add_argument("--bit-rate-values", type=float, nargs="+", default=[1e3, 10e3, 100e3],
                         help="bit rates (bit/s) to sweep in panel C")
    parser.add_argument("--vdd-range", type=float, nargs=2, default=[1.0, 5.0], help="panel A Vdd sweep range")
    parser.add_argument("--bit-rate-range", type=float, nargs=2, default=[1e2, 1e6],
                         help="panel B bit_rate sweep range (bit/s)")
    args = parser.parse_args()

    switch_energy = _import_switch_energy_fn()

    import numpy as np
    import matplotlib
    if args.out:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_a, ax_b, ax_c) = plt.subplots(1, 3, figsize=(16, 5))

    # --- Panel A: FET energy/bit vs Vdd, a few c_gate_f values ---
    vdd_range = np.linspace(args.vdd_range[0], args.vdd_range[1], 200)
    for c_gate_ff in args.c_gate_values_ff:
        c_gate_f = c_gate_ff * 1e-15
        energies = [switch_energy("fet", v, c_gate_f=c_gate_f) for v in vdd_range]
        ax_a.semilogy(vdd_range, energies, label=f"c_gate = {c_gate_ff:g} fF")
    ax_a.set_xlabel("Vdd (V)")
    ax_a.set_ylabel("Energy per bit (J)")
    ax_a.set_title("FET mode: flat, quadratic in Vdd\n(bit-rate-independent)")
    ax_a.legend(fontsize=8)
    ax_a.grid(True, which="both", alpha=0.3)

    # --- Panel B: BJT energy/bit vs bit_rate, a few duty_cycle values,
    # plus FET reference lines at the SAME Vdd (first c_gate value) so
    # the flat-vs-1/x shape difference is directly visible. ---
    bit_rate_range = np.geomspace(args.bit_rate_range[0], args.bit_rate_range[1], 200)
    for duty_cycle in args.duty_cycle_values:
        energies = [switch_energy("bjt", args.vdd, r_bias_ohm=args.r_bias_ohm,
                                   duty_cycle=duty_cycle, bit_rate=br)
                    for br in bit_rate_range]
        ax_b.loglog(bit_rate_range, energies, label=f"BJT, duty_cycle = {duty_cycle:g}")
    fet_reference_c_gate_f = args.c_gate_values_ff[0] * 1e-15
    fet_reference_energy = switch_energy("fet", args.vdd, c_gate_f=fet_reference_c_gate_f)
    ax_b.axhline(fet_reference_energy, color="black", linestyle="--", linewidth=1,
                 label=f"FET, c_gate = {args.c_gate_values_ff[0]:g} fF (bit-rate-independent)")
    ax_b.set_xlabel("Bit rate (bit/s)")
    ax_b.set_ylabel("Energy per bit (J)")
    ax_b.set_title("BJT falls as 1/bit_rate; FET doesn't move\n(crossover = where BJT becomes the better choice)")
    ax_b.legend(fontsize=7)
    ax_b.grid(True, which="both", alpha=0.3)

    print("Crossover bit rate (BJT energy/bit == FET reference energy/bit), per duty_cycle:")
    for duty_cycle in args.duty_cycle_values:
        # BJT energy/bit = duty_cycle * (Vdd^2/R_bias) * (1/bit_rate) is
        # monotonically decreasing in bit_rate, so solve directly for
        # where it equals the flat FET value rather than scanning:
        #   fet_energy = duty_cycle * (Vdd^2/R_bias) / bit_rate_crossover
        bjt_power = (args.vdd ** 2) / args.r_bias_ohm
        if fet_reference_energy and fet_reference_energy > 0:
            crossover_bit_rate = duty_cycle * bjt_power / fet_reference_energy
            below_above = ("BJT cheaper below this rate, FET cheaper above it")
            print(f"  duty_cycle={duty_cycle:g}: {crossover_bit_rate:,.0f} bit/s  ({below_above})")
        else:
            print(f"  duty_cycle={duty_cycle:g}: FET reference energy is zero/undefined -- no crossover")

    # --- Panel C: BJT energy/bit vs duty_cycle, a few bit_rate values ---
    duty_cycle_range = np.linspace(0.0, 1.0, 200)
    for bit_rate in args.bit_rate_values:
        energies = [switch_energy("bjt", args.vdd, r_bias_ohm=args.r_bias_ohm,
                                   duty_cycle=dc, bit_rate=bit_rate)
                    for dc in duty_cycle_range]
        ax_c.plot(duty_cycle_range, energies, label=f"bit_rate = {bit_rate:,.0f} bit/s")
    ax_c.set_xlabel("Duty cycle")
    ax_c.set_ylabel("Energy per bit (J)")
    ax_c.set_title("BJT mode: linear in duty_cycle\n(FET has no duty_cycle dependence at all)")
    ax_c.legend(fontsize=8)
    ax_c.grid(True, alpha=0.3)

    fig.suptitle(f"Switch-energy sensitivity (Vdd={args.vdd:g}V, R_bias={args.r_bias_ohm:g} Ohm)")
    fig.tight_layout()

    if args.out:
        fig.savefig(args.out, dpi=150)
        print(f"Saved to {args.out}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
