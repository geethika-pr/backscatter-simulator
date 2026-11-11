"""
Physical-unit conversions for the UI layer. Zero GNU Radio / Qt
dependency (same "pure math" convention as ber_theory.py and
message_framer.py).

THE CONVENTION (see claude/calibration-plan.md, item 1):
every GNU Radio block in this project works on complex sample
AMPLITUDES (|x|). We define |x|**2 = watts, i.e. as if the signal sat
across a 1-ohm load. Under that rule:

    power (W)        = amplitude**2
    power (dBm)      = 10*log10(power_W * 1000)
    amplitude        = sqrt(power_W)

    30 dBm  -> 1 W     -> amplitude 1.0
    20 dBm  -> 0.1 W   -> amplitude 0.316
   -104 dBm -> 4e-14 W -> amplitude 2e-7

The blocks themselves are NOT changed: they keep receiving the same raw
numbers they always did. This module only translates between what a
person types (dBm) and what a block receives (amplitude or watts).

Which raw params are which kind (see AMPLITUDE_PARAMS / POWER_PARAMS):
  - tx_power_send  (sender)  -> AMPLITUDE: it's passed straight in as
    analog.sig_source_c's amplitude, despite the name.
  - noise_volt_c1  (channel) -> AMPLITUDE: channels.channel_model's
    noise_voltage, the std-dev of complex noise added per sample, so
    its dBm is the noise power across the WHOLE sample-rate bandwidth.
  - p_th           (tags)    -> POWER (watts): harvesting_gate compares
    it against mean(|x|**2) directly.
"""
import math

BOLTZMANN = 1.380649e-23   # J/K
T0_KELVIN = 290.0          # standard noise temperature

# raw params the UI shows in dBm, and how the raw number relates to power
AMPLITUDE_PARAMS = {"tx_power_send", "noise_volt_c1"}
POWER_PARAMS = {"p_th"}
DBM_PARAMS = AMPLITUDE_PARAMS | POWER_PARAMS


def dbm_to_watts(dbm):
    return 10.0 ** ((float(dbm) - 30.0) / 10.0)


def watts_to_dbm(watts):
    """-inf for 0 or negative power (nothing there)."""
    watts = float(watts)
    if watts <= 0.0:
        return float("-inf")
    return 10.0 * math.log10(watts) + 30.0


def dbm_to_amplitude(dbm):
    return math.sqrt(dbm_to_watts(dbm))


def amplitude_to_dbm(amplitude):
    """Uses |amplitude| -- a negative amplitude is the same power
    (only phase differs). -inf for exactly 0."""
    return watts_to_dbm(float(amplitude) ** 2)


def raw_to_dbm(param_name, raw_value):
    """Raw block value -> dBm, for one of DBM_PARAMS."""
    if param_name in AMPLITUDE_PARAMS:
        return amplitude_to_dbm(raw_value)
    if param_name in POWER_PARAMS:
        return watts_to_dbm(raw_value)
    raise KeyError(f"{param_name!r} is not a dBm-displayed param")


def dbm_to_raw(param_name, dbm):
    """dBm -> raw block value, for one of DBM_PARAMS. dBm of -inf
    (or None) maps to 0.0, i.e. 'off'."""
    if dbm is None or dbm == float("-inf"):
        return 0.0
    if param_name in AMPLITUDE_PARAMS:
        return dbm_to_amplitude(dbm)
    if param_name in POWER_PARAMS:
        return dbm_to_watts(dbm)
    raise KeyError(f"{param_name!r} is not a dBm-displayed param")


def thermal_noise_dbm(bandwidth_hz, noise_figure_db=0.0, temperature_k=T0_KELVIN):
    """Thermal noise floor k*T*B, plus a receiver noise figure, in dBm.
    kT at 290 K is -174 dBm/Hz, so 2 MHz -> about -111 dBm, and with a
    7 dB noise figure (Mishra & Larsson 2019's assumption) -> -104 dBm."""
    return watts_to_dbm(BOLTZMANN * temperature_k * float(bandwidth_hz)) + float(noise_figure_db)
