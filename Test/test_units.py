"""
Checks core/units.py, the dBm <-> raw-block-value conversions the
inspector uses for Tx Power, Noise Power and Harvesting Threshold.
No GNU Radio or Qt needed: run from the project root with
    python3 Test/test_units.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import units  # noqa: E402


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def main():
    # anchor points of the |x|**2 = watts convention
    assert close(units.dbm_to_watts(30), 1.0)
    assert close(units.dbm_to_amplitude(30), 1.0)
    assert close(units.dbm_to_amplitude(20), math.sqrt(0.1))
    assert close(units.watts_to_dbm(1e-3), 0.0)

    # today's defaults, read in dBm (the whole point of this feature)
    assert close(units.raw_to_dbm("tx_power_send", 20), 10 * math.log10(400) + 30)  # ~56.02
    assert close(units.raw_to_dbm("noise_volt_c1", 0.01), -10.0)
    assert close(units.raw_to_dbm("p_th", 0.5), 10 * math.log10(0.5) + 30)          # ~26.99

    # 0.5 -> 2.0 noise voltage is +12.04 dB (4x amplitude = 16x power)
    step = units.raw_to_dbm("noise_volt_c1", 2.0) - units.raw_to_dbm("noise_volt_c1", 0.5)
    assert close(step, 20 * math.log10(4))

    # round trips, both kinds, over a wide range
    for name in units.DBM_PARAMS:
        for dbm in (-150.0, -104.0, -42.5, 0.0, 20.0, 30.0, 56.0):
            assert close(units.raw_to_dbm(name, units.dbm_to_raw(name, dbm)), dbm, 1e-9), (name, dbm)

    # amplitude vs power params really are different (sqrt vs not)
    assert close(units.dbm_to_raw("tx_power_send", 30 - 20), math.sqrt(0.01))
    assert close(units.dbm_to_raw("p_th", 30 - 20), 0.01)

    # "off": zero raw <-> -inf dBm, and None/-inf map back to 0
    assert units.raw_to_dbm("noise_volt_c1", 0.0) == float("-inf")
    assert units.raw_to_dbm("p_th", 0.0) == float("-inf")
    assert units.dbm_to_raw("noise_volt_c1", float("-inf")) == 0.0
    assert units.dbm_to_raw("p_th", None) == 0.0

    # thermal noise reference numbers quoted in the tooltips
    assert abs(units.thermal_noise_dbm(2e6) - (-110.97)) < 0.05
    assert abs(units.thermal_noise_dbm(2e6, 7.0) - (-103.97)) < 0.05

    try:
        units.raw_to_dbm("distance", 1.0)
    except KeyError:
        pass
    else:
        raise AssertionError("non-dBm param should raise")

    print("test_units: all checks passed")


if __name__ == "__main__":
    main()
