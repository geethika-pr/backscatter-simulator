"""
QGraphicsView-based drag-and-drop node editor for the backscatter
topology. Shares its backend with ui/main_window.py unchanged
(registry.NODE_TYPES/Port/Param, plan_topology(), TopologyMiddleware --
all from core.modulation_middleware/core.registry); this file is a
separate UI on top of that same core, not a fork of it.

Core interactions:
  - Left palette (registry.NODE_TYPES minus tag_analog/rx_analog -- see
    PLACEABLE_NODE_TYPES) drags onto the canvas to create a node.
  - Nodes show input ports (circles, left edge) and output ports (right
    edge), each with a tooltip for name/direction/dtype. Click-drag an
    output port to an input port to wire them; both ends snap to the
    nearest matching port within a small pixel radius so an exact-pixel
    hit isn't required (see CanvasView.SNAP_PIXELS/_port_near() and
    PortHandle.shape()). Fan-in (multiple edges into one input) is
    allowed -- TopologyMiddleware's adder logic handles it.
  - Selecting a node shows its registry Params in the right-side
    inspector as editable fields, built the same way
    build_fixed_topology()'s own *_params dicts are structured. A tag_*
    node also gets a Payload field (per-run data, not a registry Param).
  - Validate / Start / Stop mirror main_window.py: Validate runs
    plan_topology() over the canvas; Start builds a real
    TopologyMiddleware and runs it; Stop tears it down.
  - Delete removes selected nodes/edges; Clear wipes the canvas (both
    disabled while running). Save/Load persist named layouts as JSON;
    canvas_default_layout.json auto-loads on startup.
  - Plain scroll pans (vertical and horizontal); Ctrl+scroll zooms.

Known gaps, not yet addressed:
  - No port-dtype checking on connect -- a bad wiring (type mismatch,
    dangling required input) only surfaces at Start, via whatever error
    gnuradio's own flowgraph validation raises.
  - No live param editing while running -- stop, edit, restart.
  - tag_analog/rx_analog aren't placeable (same Signal_In/float shape
    TopologyMiddleware's node loop doesn't support yet).
  - No automated test drives real mouse drag/drop (needs a live Qt
    event loop); what's verified headlessly is that the file compiles
    and that scene_to_topology()'s node/edge structure resolves through
    the real plan_topology() to the same shape build_fixed_topology()
    produces for an equivalent hand-built graph.

Run from the project root, venv active:
    python3 ui/canvas_window.py
"""
import sys
import re
import math
import cmath
from collections import defaultdict

import numpy as np

sys.path.insert(0, "blocks")
sys.path.insert(0, ".")

from PyQt6 import QtWidgets, QtCore, QtGui

import csv
import json
import os

import core.registry as registry
import core.message_framer as message_framer
from core.modulation_middleware import (
    plan_topology, TopologyMiddleware, DEFAULT_TAG_OVERRIDES, SI_ISOLATION_DEFAULTS,
    SAMP_RATE, RX_ALREADY_AT_BIT_RATE, PREAMBLE_BITS, PREAMBLE,
)
from core.signal_probe import SignalProbe

# Types excluded from the drag palette, and why:
# - tag_analog/rx_analog: different payload shape (Signal_In/float, not
#   Data_In/byte) that TopologyMiddleware's node loop doesn't support
#   (raises NotImplementedError) -- same gap as main_window.py's
#   modulation dropdown leaving "Analog" out.
# - pie_decoder: not a standalone node -- every tag_* hier-block already
#   builds its own internally, wired to that tag's own enable_gated.
# - packet_hex_decoder: no longer user-wired. Its settings live as
#   NodeItem.decoder_params on the rx_* it decodes for (see
#   NodeInspector.show_node()); scene_to_topology() auto-creates and
#   wires one per receiver, same as the Tx_Leakage auto-wire.
# - channel: every sender->tag_* or tag_*->rx_* edge now implicitly IS a
#   channel (see EdgeItem.channel_params/role) -- scene_to_topology()
#   creates and wires one per edge automatically, with per-tag distance/
#   fading/noise instead of one shared forward/return channel. Edit a
#   tag's channel by clicking the connecting arrow (NodeInspector.
#   show_edge()). Still a real registry.NODE_TYPES entry so an OLD saved
#   layout with explicit channel boxes keeps working; only new
#   placements lose it from the palette.
PLACEABLE_NODE_TYPES = [t for t in registry.NODE_TYPES
                         if t not in ("tag_analog", "rx_analog", "pie_decoder",
                                      "packet_hex_decoder", "channel")]

# A combined "reader" (one sender + one rx_*, see CanvasWindow.
# add_reader()) is a UI-only wrapper, not a real registry.NODE_TYPES
# entry. This sentinel string is what the palette drags and the drop
# handler recognizes to call add_reader() instead of add_node() --
# deliberately not a NODE_TYPES key, so it can't collide with a real one.
READER_PLACEHOLDER_TYPE = "reader"

# Ports always wired automatically, never by hand: tag_*'s Data_In (by
# TopologyMiddleware, from that node's payload) and every sender's
# Tx_Leakage_Out -> every rx_*'s Tx_Leakage_In (by scene_to_topology()).
# Rendered greyed-out and excluded from drag/snap handling, but still
# visible rather than hidden outright.
AUTO_WIRED_PORTS = {
    "tag_ook": {"Data_In"}, "tag_bpsk": {"Data_In"},
    "tag_fm0": {"Data_In"}, "tag_miller": {"Data_In"},
    "sender": {"Tx_Leakage_Out"},
    "rx_ook": {"Tx_Leakage_In"}, "rx_bpsk": {"Tx_Leakage_In"},
    "rx_fm0": {"Tx_Leakage_In"}, "rx_miller": {"Tx_Leakage_In"},
}

# Known-good starting values for a freshly-dropped node, layered on top
# of plain registry defaults in NodeItem.__init__ -- the same validated
# overrides build_fixed_topology() bakes in for the fixed-shape path
# (DEFAULT_TAG_OVERRIDES, sender_params/fwd_channel_params,
# SI_ISOLATION_DEFAULTS). Raw registry defaults alone don't decode (e.g.
# sender's enable_gated=0, most tags' packet_bits=16 -- too short a burst
# to catch, si_isolation_db=25.0 which breaks OOK specifically), so every
# new node is seeded with these validated values instead. "channel" has
# no role yet when freshly dropped, so it's seeded at the forward
# channel's values (distance=1, noise_volt_c1=0.02).
NODE_SEED_OVERRIDES = {
    "sender": {"enable_gated": 1},
    "channel": {"distance": 1, "noise_volt_c1": 0.02},
    "tag_ook": dict(DEFAULT_TAG_OVERRIDES),
    "tag_bpsk": dict(DEFAULT_TAG_OVERRIDES),
    "tag_fm0": dict(DEFAULT_TAG_OVERRIDES),
    "tag_miller": dict(DEFAULT_TAG_OVERRIDES),
    # A freshly-dropped STANDALONE rx_* seeds si_enable=0 (overriding the
    # registry's own default of 1) -- no co-located sender means nothing
    # to self-interference-cancel, and si_enable/si_isolation_db are
    # hidden from a standalone rx's panel entirely (NodeInspector.
    # show_node()). add_reader() flips this back to 1 on a reader's rx
    # half right after this seed applies, since that IS the monostatic
    # case this cancellation is for. An old saved layout's own explicit
    # si_enable value is unaffected either way -- this only seeds new nodes.
    "rx_ook": {"si_isolation_db": SI_ISOLATION_DEFAULTS["OOK"], "si_enable": 0},
    "rx_bpsk": {"si_isolation_db": SI_ISOLATION_DEFAULTS["BPSK"], "si_enable": 0},
    "rx_fm0": {"si_isolation_db": SI_ISOLATION_DEFAULTS["FM0"], "si_enable": 0},
    "rx_miller": {"si_isolation_db": SI_ISOLATION_DEFAULTS["Miller"], "si_enable": 0},
}

# Canvas-to-physical scale (screen pixels per simulated meter), not a
# physical constant -- just one number to retune if the canvas needs to
# feel more/less zoomed-in relative to distance. Keeps a channel's
# Distance (m) param in step with how far apart its two nodes are drawn:
# EdgeItem.sync_distance_from_position() recomputes distance from
# position on creation and every drag; CanvasWindow.
# set_channel_edge_distance() is the inverse (typed distance moves the
# node). Distance is live from the moment an edge is wired, not just
# after the first drag -- more canvas distance always means more Friis
# attenuation in the real channel block.
PIXELS_PER_METER = 500

# Shipped DEFAULT for NodeItem.decode_snr_threshold_db -- the SNR (dB)
# a freshly-placed sender's decode-range ring starts at, before the user
# touches NodeInspector.decode_threshold_spin. 6dB is the figure the
# reference thesis's own empirical range-measurement method used for ITS
# decoder, per the proposal that introduced this feature -- a reasonable
# starting point, not a claim that holds for every modulation/detector
# this project supports. User-editable per sender (see the spinbox's own
# tooltip); this constant only sets where that spinbox starts.
DECODE_RANGE_SNR_THRESHOLD_DB = 6.0

# Param names that are semantically on/off toggles even though the
# registry -- and every real block constructor underneath -- stores them
# as plain int 0/1, not Python bool. Rendered as checkboxes in the
# inspector instead of int spinboxes (that's what "toggle" means to
# someone using the UI, whatever it is further down), writing back int
# 0/1 rather than True/False to match the registry default's own type
# exactly, in case a constructor is strict about it.
BOOLEAN_INT_PARAMS = {"enable_gated", "si_enable", "enable_mac"}

# Per-param override for how many decimal places a float QDoubleSpinBox
# shows (make_param_widget()'s default is 4, which reads as needless
# clutter -- "1.0000" -- for a param nobody actually sets to 4-decimal
# precision by hand). "distance" is the one that got singled out by
# feedback so far; add more names here if another float param needs its
# own tighter display, rather than changing the global default and
# risking a different param that genuinely wants 4.
PARAM_DECIMALS = {"distance": 2}

# Enum-like int params with GRC-documented word labels per value (from
# the source block's .block.yml "label:" field). Rendered as a
# QComboBox, writes back the plain int index.
#
# fading_type_c1: backscatter_channel_1's "Fading Mode (0=Clear,
# 1=Rician, 2=Rayleigh)" -- 0 bypasses fading entirely (plain Friis
# attenuation), 1 is LOS-present (Rician), 2 is no-LOS (Rayleigh).
ENUM_PARAMS = {
    "fading_type_c1": ["Clear (no fading)", "Rician (LOS present)", "Rayleigh (no LOS)"],
}

# Per-node modulation switcher (NodeInspector, any tag_*/rx_*). Analog
# is left out, same reason as PLACEABLE_NODE_TYPES.
#
# Scoped to ONE node only, never cascading to a connected rx/tag:
# carries over params shared by name, reseeds new ones, and re-points
# edges by matching old/new ports POSITIONALLY per direction (handles
# tag_fm0/tag_miller's "Reflection_Out" vs tag_ook/tag_bpsk's "out" --
# same slot, different name). See _convert_node_modulation().
MODULATION_CHOICES = ["OOK", "BPSK", "FM0", "Miller"]

# type_name -> modulation key, the reverse of registry.MODULATION_MAP,
# so the inspector can preselect the right combo entry for whichever
# tag_*/rx_* node is currently selected.
TYPE_TO_MODULATION = {}
for _mod, _types in registry.MODULATION_MAP.items():
    if _mod in MODULATION_CHOICES:
        TYPE_TO_MODULATION[_types["tag_type"]] = _mod
        TYPE_TO_MODULATION[_types["rx_type"]] = _mod
del _mod, _types

# Where the auto-loaded-on-startup / "Save as Default" layout lives --
# next to this file, not inside the project's Test/ or core/ directories,
# since it's UI-session state, not a validated test or backend code.
DEFAULT_LAYOUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "canvas_default_layout.json")


def _check_known_requirements(nodes, edges):
    """Checks requirements plan_topology() doesn't (it only resolves
    port names) but that gnuradio's real flowgraph validation rejects at
    Start with an opaque error. Currently one rule: every rx_* needs its
    Tx_Leakage_In fed -- every rx_* hier-block wires its self-
    interference multiply_const_cc to Tx_Leakage_In unconditionally in
    __init__ (not gated by si_enable), so an unfed input crashes with
    "insufficient connected input ports". scene_to_topology() auto-wires
    that port from any sender on the canvas, so this check really means
    "is there a sender" -- not a stand-in for general port-dtype
    validation (still not implemented).
    """
    node_types = {n["id"]: n["type"] for n in nodes}
    edge_targets = {(to_id, to_port) for (_from_id, _from_port, to_id, to_port) in edges}

    problems = []
    for node_id, type_name in node_types.items():
        if type_name.startswith("rx_") and (node_id, "Tx_Leakage_In") not in edge_targets:
            problems.append(
                f"'{node_id}' ({type_name})'s Tx_Leakage_In has nothing feeding it. "
                "This auto-wires from any 'sender' node's Tx_Leakage_Out now (no "
                "manual wire needed) -- so this means there's no sender node on the "
                "canvas at all, not that a connection is missing. Add one."
            )
    return problems


# Optional per-type icon images. A transparent-background .png named
# after a type (sender.png, tag_ook.png, ..., rx_miller.png) in this
# folder is picked up automatically; a type with no file keeps the
# plain colored rectangle (_node_color()). Shape must already be baked
# into the png's own transparency -- drawn as-is, never masked/cropped.
#
# Exception: a reader's visible half is an ordinary 'sender' NodeItem
# but looks up "reader.png" instead of "sender.png", via NodeItem's
# icon_type_name param (defaults to type_name; overridden by
# add_reader() and _load_layout_dict()), so it reads as visually
# distinct from a plain standalone sender.
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

_icon_pixmap_cache = {}  # type_name -> QPixmap or None (None = looked up, missing/invalid)


def _load_icon_pixmap(type_name):
    """QPixmap for this type's icon, or None if ICON_DIR/<type_name>.png
    doesn't exist or fails to load -- cached after the first lookup per
    type_name so dropping 50 tag_ook nodes doesn't re-hit disk 50 times."""
    if type_name in _icon_pixmap_cache:
        return _icon_pixmap_cache[type_name]
    path = os.path.join(ICON_DIR, f"{type_name}.png")
    pixmap = QtGui.QPixmap(path) if os.path.isfile(path) else None
    if pixmap is not None and pixmap.isNull():
        pixmap = None
    _icon_pixmap_cache[type_name] = pixmap
    return pixmap


def _node_color(type_name):
    """Rough category coloring so same-kind nodes are easy to tell apart
    at a glance -- NOT icons/images, just a flat fill color per category."""
    if type_name.startswith("tag_"):
        return QtGui.QColor("#cfe8ff")
    if type_name.startswith("rx_"):
        return QtGui.QColor("#d7f5d0")
    if type_name == "channel":
        return QtGui.QColor("#e8e8e8")
    if type_name == "sender":
        return QtGui.QColor("#ffe3c2")
    if type_name == "pie_decoder":
        return QtGui.QColor("#e6d6ff")
    if type_name == "packet_hex_decoder":
        return QtGui.QColor("#fff6b3")
    return QtGui.QColor("#f0f0f0")


def _node_type_tooltip(type_name):
    nt = registry.NODE_TYPES[type_name]
    ports = ", ".join(f"{p.name}({p.direction}/{p.dtype})" for p in nt.ports)
    return f"{type_name}\nPorts: {ports}"


def _tag_identity_color(order_seq):
    """Deterministic per-tag color for the wake-up-range ring and its
    matching icon-margin highlight, so a ring can be traced back to its
    tag by color. Golden-angle hue step off NodeItem.order_seq (stable
    across a modulation-switch rebuild, unlike node_id) -- same tag
    always gets the same color, any two tags land far apart in hue.
    order_seq=None falls back to seq 0 rather than crashing."""
    seq = order_seq if order_seq is not None else 0
    hue = int((seq * 137.5) % 360.0)
    return QtGui.QColor.fromHsv(hue, 210, 230)


def _wake_up_radius_m(tx_power_send, p_th, freq, g_reader_dbi, g_tag_dbi):
    """Forward-leg-only wake-up radius (meters): the distance at which a
    tag stops receiving enough power to turn on at all, given this
    sender's tx_power_send and this edge's channel settings. Says
    nothing about whether a tag inside this radius will decode
    correctly (that's the return leg's job) -- a deliberate scope
    limit, not an oversight.

    Derived by inverting backscatter_channel_1's Friis gain and
    harvesting_gate's wake check (tag powered while
    (tx_power_send * friis_gain(d))**2 >= p_th) and solving for d --
    hence sqrt(p_th) in the denominator. noise/fading are not folded in:
    this is a best-case radius for the given settings, not a guarantee.
    p_th<=0 returns float('inf') rather than dividing by zero; the
    caller treats a non-finite/non-positive radius as "don't draw"."""
    if p_th <= 0:
        return float("inf")
    c_light = 3e8
    gain_const = (10 ** ((g_reader_dbi + g_tag_dbi) / 20.0)) * (c_light / freq) / (4.0 * 3.14159265)
    return max(0.0, (float(tx_power_send) * gain_const) / (p_th ** 0.5))


def _harvested_power(tx_power_send, distance_m, freq, g_reader_dbi, g_tag_dbi):
    """Power arriving at a tag `distance_m` meters from its sender --
    the forward evaluation of the same Friis estimate _wake_up_radius_m()
    inverts (same best-case, noise/fading-excluded caveat applies).
    Mirrors backscatter_channel_1's friis_gain exactly, including its
    max(distance, 0.05) floor. Used by NodeInspector.
    _update_energy_info_label(). Kept separate from _wake_up_radius_m()
    since that one solves for distance, this one evaluates at a known
    distance."""
    distance_m = max(float(distance_m), 0.05)  # same floor backscatter_channel_1.py uses
    c_light = 3e8
    gain_const = (10 ** ((g_reader_dbi + g_tag_dbi) / 20.0)) * (c_light / freq) / (4.0 * 3.14159265)
    amplitude = float(tx_power_send) * gain_const / distance_m
    return amplitude ** 2


def _friis_amplitude_gain(distance_m, freq, g_reader_dbi, g_tag_dbi):
    """One-way Friis amplitude gain of a single hop at a known distance
    -- the same gain_const/distance split _wake_up_radius_m() and
    _harvested_power() both already use (confirmed against
    backscatter_channel_1.py's own `friis_gain` variable, including its
    max(distance, 0.05) floor), pulled out as its own function so the
    return-leg decode-range math below can apply ONE hop's formula twice
    (once per leg, each with its own freq/antenna-gain settings) without
    a third near-duplicate inline copy. Deliberately NOT used to
    refactor _wake_up_radius_m()/_harvested_power() themselves -- those
    are already independently verified (ui/test_wake_up_range_formula.py)
    and not worth the regression risk of touching for this."""
    distance_m = max(float(distance_m), 0.05)
    c_light = 3e8
    gain_const = (10 ** ((g_reader_dbi + g_tag_dbi) / 20.0)) * (c_light / freq) / (4.0 * 3.14159265)
    return gain_const / distance_m


def _reflection_swing_mag(gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg):
    """|Gamma_1 - Gamma_0| -- the magnitude of a tag's own reflection-
    coefficient swing between its two modulation states, mirroring
    complex_gamma_modulator_epy_block_0.py's own `delta_Gamma` exactly.
    This swing is what actually carries the bit on the return leg: A_s
    (the tag's static structural-scattering term) is identical in both
    states, so it cancels out of the DIFFERENCE and correctly has no
    effect here -- it's DC offset/self-interference, not information,
    same reasoning the module docstring below gives for leaving SI
    cancellation out of the decode-range scope entirely."""
    gamma0 = gamma0_mag * cmath.exp(1j * math.radians(gamma0_phase_deg))
    gamma1 = gamma1_mag * cmath.exp(1j * math.radians(gamma1_phase_deg))
    return abs(gamma1 - gamma0)


def _decode_snr_db(distance_m, tx_power_send,
                    fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi,
                    gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg,
                    ret_freq, ret_g_reader_dbi, ret_g_tag_dbi, ret_noise_volt_c1,
                    fwd_distance_m=None):
    """SNR (dB) of the information-bearing backscattered signal arriving
    back at the receiver, `distance_m` meters out on the return leg --
    the return-leg, SNR-flavored counterpart to _harvested_power()'s
    forward-leg power evaluation. fwd_distance_m defaults to the same
    distance_m: every combined "reader" node this project places IS
    monostatic (EdgeItem._wire_reader_shadow_return() mirrors one
    distance onto both legs), and it's the natural assumption for a
    standalone sender/rx pair too -- pass it explicitly only for a
    genuinely bistatic setup where the two legs differ.

    Deliberately the same channel-noise-only scope _wake_up_radius_m()
    already uses for the forward leg (see wake-up-range-visualization.md's
    scoping note): the two Friis hops (sender->tag, tag->receiver) and
    the tag's own reflection swing (_reflection_swing_mag()) against
    backscatter_channel_1's own noise_voltage on the RETURN channel --
    NOT self-interference cancellation (si_isolation_db) or DC-blocker/
    slicer behaviour, which would need a validated empirical calibration
    this project isn't comfortable shipping without one (same reason
    those were excluded from the wake-up ring)."""
    if fwd_distance_m is None:
        fwd_distance_m = distance_m
    delta_gamma_mag = _reflection_swing_mag(
        gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg)
    signal = (float(tx_power_send)
              * _friis_amplitude_gain(fwd_distance_m, fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi)
              * delta_gamma_mag
              * _friis_amplitude_gain(distance_m, ret_freq, ret_g_reader_dbi, ret_g_tag_dbi))
    if ret_noise_volt_c1 <= 0:
        return float("inf")
    if signal <= 0:
        return float("-inf")
    return 20.0 * math.log10(signal / float(ret_noise_volt_c1))


def _decode_range_m(tx_power_send,
                     fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi,
                     gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg,
                     ret_freq, ret_g_reader_dbi, ret_g_tag_dbi, ret_noise_volt_c1,
                     snr_threshold_db=6.0):
    """Closed-form maximum SYMMETRIC round-trip distance (same d on both
    the forward and return leg -- see _decode_snr_db()'s docstring for
    why that's the right default) at which the backscattered signal's
    SNR against the return channel's own noise still clears
    snr_threshold_db. The range-via-SNR-threshold counterpart to
    _wake_up_radius_m() -- same inversion technique, same excluded-
    effects scope (see _decode_snr_db()'s docstring), just two Friis
    hops instead of one.

    Each one-way hop is K/d (K = _friis_amplitude_gain(1.0, ...), the
    same K/d split _wake_up_radius_m() already uses for its one hop), so
    the round-trip signal amplitude at distance d is (tx_power_send *
    delta_gamma_mag * K_fwd * K_ret) / d**2 -- a clean 1/d**2, not 1/d,
    since it's two hops. Solving (amplitude/noise)**2 >=
    10**(snr_threshold_db/10) for d is then a direct quartic inversion
    (d = (C**2 / (noise**2 * threshold_linear)) ** 0.25), not a numeric
    sweep -- verified against an independent SNR-at-distance evaluation
    in ui/test_decode_range_formula.py (SNR is exactly snr_threshold_db
    at the returned d, strictly above just inside it, strictly below
    just outside it).

    tx_power_send<=0 or a zero reflection swing (gamma0_mag/phase ==
    gamma1_mag/phase -- a mis-set tag with no actual modulation) means
    there is NO signal at any distance, so this returns 0.0 (not inf --
    the opposite failure mode from _wake_up_radius_m()'s p_th<=0, where
    the limiting resource is absent rather than the signal itself).
    ret_noise_volt_c1<=0 means noise never limits the range, so this
    returns float('inf'), mirroring _wake_up_radius_m()'s own p_th<=0
    case. Callers treat a non-finite/non-positive/zero range the same
    way _wake_up_radius_m()'s callers already do."""
    delta_gamma_mag = _reflection_swing_mag(
        gamma0_mag, gamma0_phase_deg, gamma1_mag, gamma1_phase_deg)
    if tx_power_send <= 0 or delta_gamma_mag <= 0:
        return 0.0
    if ret_noise_volt_c1 <= 0:
        return float("inf")
    k_fwd = _friis_amplitude_gain(1.0, fwd_freq, fwd_g_reader_dbi, fwd_g_tag_dbi)
    k_ret = _friis_amplitude_gain(1.0, ret_freq, ret_g_reader_dbi, ret_g_tag_dbi)
    c_budget = float(tx_power_send) * delta_gamma_mag * k_fwd * k_ret
    threshold_linear = 10 ** (snr_threshold_db / 10.0)
    d_pow4 = (c_budget ** 2) / ((float(ret_noise_volt_c1) ** 2) * threshold_linear)
    return d_pow4 ** 0.25


def _switch_energy_per_bit_joules(mode, vdd, c_gate_f=None, r_bias_ohm=None,
                                   duty_cycle=None, bit_rate=None):
    """Energy (Joules) consumed per transmitted bit by a tag's
    reflection switch, under one of two real switch-device models.
    Pure function (no NodeItem/Qt dependency) so it can be regression-
    tested the same way _harvested_power() is -- see ui/test_energy_
    estimate.py. Returns None if a param the chosen mode needs is
    missing/invalid; callers show "--" in that case.

    "fet" -- CMOS/FET gate, dynamic switching power P = C_gate * Vdd^2
    * f_toggle (the standard textbook C*V^2 result, e.g. Rabaey's
    *Digital Integrated Circuits*). Energy per toggle is power divided
    by toggle rate, which cancels the rate term: E_bit = C_gate *
    Vdd^2 -- flat, bit-value-symmetric, bit-rate-independent. This is
    the same SHAPE the original free-typed energy_per_bit field always
    assumed, so FET mode is a drop-in, now-justified replacement for
    it.

    "bjt" -- bipolar switch held on through a resistive base bias (the
    real Kimionis-thesis tag hardware: Fig 3.5's 2kOhm resistor at
    3V). It draws P = Vdd^2 / R_bias only while the base is actively
    being driven on, NOT a fixed per-toggle charge -- so unlike FET
    mode it is duty-cycle-dependent (how much of each bit period is
    spent biased on; duty_cycle is a documented approximation here,
    not derived from the tag's real transmitted bit values) and
    bit-RATE-dependent (it's a power x time quantity, not a per-
    transition charge): E_bit = duty_cycle * (Vdd^2 / R_bias) *
    (1 / bit_rate). This is the real structural difference from FET
    mode -- BJT mode cannot reuse a flat per-bit constant unchanged.
    """
    if vdd is None or vdd <= 0:
        return None
    if mode == "fet":
        if c_gate_f is None or c_gate_f < 0:
            return None
        return float(c_gate_f) * float(vdd) ** 2
    elif mode == "bjt":
        if not r_bias_ohm or r_bias_ohm <= 0 or duty_cycle is None or not bit_rate:
            return None
        return float(duty_cycle) * (float(vdd) ** 2 / float(r_bias_ohm)) * (1.0 / float(bit_rate))
    return None


def _format_energy_joules(joules):
    """Auto-scaled SI string for a Joule quantity (fJ..J). Used only
    for the "Consumed (switching)" figure, now that it's a real,
    physically-grounded number -- "Consumed (staying active)" stays in
    energy_static_rate's original uncalibrated "units" scale, since
    that field itself hasn't been upgraded."""
    if joules is None:
        return "--"
    j = float(joules)
    if j == 0:
        return "0 J"
    sign = "-" if j < 0 else ""
    j = abs(j)
    for scale, unit in ((1.0, "J"), (1e-3, "mJ"), (1e-6, "µJ"),
                         (1e-9, "nJ"), (1e-12, "pJ")):
        if j >= scale:
            return f"{sign}{j / scale:.3g} {unit}"
    return f"{sign}{j / 1e-15:.3g} fJ"


class PortHandle(QtWidgets.QGraphicsEllipseItem):
    """One connectable port on a node -- a small circle, blue for an
    input, orange for an output, labeled with the port's real registry
    name. `port` is the registry.Port this handle represents; `node_item`
    is the NodeItem it belongs to (back-reference used when finishing a
    drag-to-connect, and by EdgeItem to find this handle's scene
    position)."""

    RADIUS = 6
    # shape() (used for hit-testing) is a larger invisible circle than
    # the small dot paint() actually draws -- without this, a release a
    # couple pixels off-center misses the port and hits the node's rect
    # instead.
    HIT_PADDING = 6

    def __init__(self, node_item, port, auto_wired=False, show_label=True):
        super().__init__(-self.RADIUS, -self.RADIUS, 2 * self.RADIUS, 2 * self.RADIUS, node_item)
        self.node_item = node_item
        self.port = port
        # auto_wired: this port is connected by TopologyMiddleware itself
        # (see AUTO_WIRED_PORTS) -- greyed out and excluded from
        # CanvasView's press/release/snap handling entirely, so it can't
        # be dragged from or to, rather than looking like a normal
        # connectable port that's just been left empty.
        self.auto_wired = auto_wired
        if auto_wired:
            color = "#aaaaaa"
            if port.name == "Data_In":
                reason = "from this tag's Payload field (see the inspector)"
            else:
                reason = "to/from every sender/receiver pair automatically"
            tooltip = (f"{port.name} ({port.direction}, {port.dtype}) -- auto-wired "
                       f"{reason}; nothing to connect here")
        else:
            color = "#4a90d9" if port.direction == "out" else "#d9824a"
            tooltip = f"{port.name} ({port.direction}, {port.dtype})"
        self._normal_pen = QtGui.QPen(QtGui.QColor("#222222"), 1)
        self._active_pen = QtGui.QPen(QtGui.QColor("#000000"), 3)
        self.setBrush(QtGui.QBrush(QtGui.QColor(color)))
        self.setPen(self._normal_pen)
        self.setZValue(2)
        self.setToolTip(tooltip)

        # show_label=False (icon-bearing nodes) drops the on-canvas
        # port-name text -- still available via tooltip. position_
        # label() below no-ops with no label, so callers need no branch.
        if show_label:
            label_text = port.name
            label = QtWidgets.QGraphicsSimpleTextItem(label_text, node_item)
            font = label.font()
            font.setPointSize(7)
            label.setFont(font)
            if auto_wired:
                label.setBrush(QtGui.QBrush(QtGui.QColor("#999999")))
            # input labels sit to the right of their dot (dot is on the
            # left edge); output labels sit to the left of theirs (dot is
            # on the right edge) -- positioned once here, moved together
            # with the port handle since both are children of the same
            # NodeItem.
            self._label = label
        else:
            self._label = None

    def shape(self):
        r = self.RADIUS + self.HIT_PADDING
        path = QtGui.QPainterPath()
        path.addEllipse(-r, -r, 2 * r, 2 * r)
        return path

    def set_highlighted(self, active):
        """Visual feedback during a drag -- the drag's source port stays
        highlighted the whole time, and whatever "in" port is currently
        closest to the cursor (CanvasView._port_near's snap target)
        highlights too, so it's clear exactly which ports a connection
        is actually forming between before you release. auto_wired ports
        never participate in a drag, so never highlight."""
        if self.auto_wired:
            return
        self.setPen(self._active_pen if active else self._normal_pen)

    def position_label(self, node_width):
        if self._label is None:
            return
        if self.port.direction == "in":
            self._label.setPos(self.pos().x() + self.RADIUS + 3, self.pos().y() - 7)
        else:
            text_w = self._label.boundingRect().width()
            self._label.setPos(self.pos().x() - self.RADIUS - 3 - text_w, self.pos().y() - 7)

    def set_auto_wired(self, auto_wired):
        """Flips this already-built port between normal and auto_wired
        styling at runtime -- unlike other auto_wired ports (baked in
        once via AUTO_WIRED_PORTS), a tag's Carrier_Out/Reflection_Out
        is normally manual and only becomes "spoken for" when a reader
        wires into it (CanvasWindow._wire_reader_shadow_return()), going
        back to normal when that edge is deleted."""
        self.auto_wired = auto_wired
        if auto_wired:
            color = "#aaaaaa"
            tooltip = (f"{self.port.name} ({self.port.direction}, {self.port.dtype}) -- wired "
                       "automatically by the combined reader this tag is connected to; "
                       "nothing to connect here")
        else:
            color = "#4a90d9" if self.port.direction == "out" else "#d9824a"
            tooltip = f"{self.port.name} ({self.port.direction}, {self.port.dtype})"
        self.setBrush(QtGui.QBrush(QtGui.QColor(color)))
        self.setToolTip(tooltip)
        if self._label is not None:
            self._label.setBrush(QtGui.QBrush(QtGui.QColor("#999999" if auto_wired else "#000000")))


class NodeItem(QtWidgets.QGraphicsRectItem):
    """One placed node on the canvas. Holds the live, editable state for
    that node -- params (dict, seeded from registry defaults, edited via
    NodeInspector), payload (list[0/1] for tag_* nodes only, None
    otherwise), and decoder_params (packet_hex_decoder's own param dict,
    for rx_* nodes only, None otherwise -- see its own comment below) --
    which scene_to_topology() reads straight off when building the real
    (nodes, edges) spec for TopologyMiddleware."""

    WIDTH = 170
    HEADER_H = 26
    # Spacing between adjacent port dots on the same side. Bumped up from
    # an earlier 18px specifically because that was close enough to
    # CanvasView.SNAP_PIXELS' forgiving click radius that two adjacent
    # ports (e.g. a sender's Carrier_Out and Tx_Leakage_Out) could
    # plausibly snap-grab the wrong one -- a likely contributor to a
    # mis-wired Tx_Leakage_In that still crashed after "connecting" it.
    # 24px keeps a comfortable gap beyond SNAP_PIXELS=12.
    PORT_SPACING = 24
    MIN_HEIGHT = 50

    # Icon-node layout constants -- independent of WIDTH/HEADER_H (the
    # plain-rectangle layout); display size on screen, independent of
    # the source .png's actual resolution.
    ICON_SIZE = 96
    # Gap between the icon's own left/right edge and the node's -- this
    # IS the node's left/right edge too, so it's also exactly how close
    # a port dot sits to the image (feedback: "make the circle blue hook
    # closer to the png image"). Small on purpose.
    ICON_PORT_MARGIN = 14
    ICON_TOP_MARGIN = 6
    ICON_LABEL_H = 28  # room for the type/id text below the image

    def __init__(self, node_id, type_name, canvas, pos, icon_type_name=None):
        self.node_id = node_id
        self.type_name = type_name
        self.canvas = canvas  # CanvasWindow, used to keep edges in sync on move

        # Which icon file to look up (see ICON_DIR's own comment) --
        # normally just type_name itself (every ordinary node), but
        # add_reader()/_load_layout_dict() pass "reader" here for a
        # combined reader's visible sender half, so it gets its own
        # distinct icon instead of a plain sender's. Stored (not just
        # used inline below) so it's available if anything else ever
        # needs to know which icon this item is actually showing.
        self.icon_type_name = icon_type_name if icon_type_name is not None else type_name

        # order_seq: monotonically increasing id assigned once by
        # add_node(), carried over across a modulation-switch rebuild
        # even though node_id can change. Used by _find_upstream_tags()
        # so tag ordering depends on placement order alone, not node_id
        # or edge insertion order. None here is a placeholder.
        self.order_seq = None

        # Wake-up-range visualization (CanvasWindow._update_wake_
        # ranges()). show_wake_ranges is sender-only in practice but set
        # on every NodeItem so callers don't need a type_name check.
        # show_decode_range is the RECEIVER-side counterpart -- owned by
        # a standalone rx_* node or a reader's hidden rx half (see
        # NodeInspector._add_decode_range_controls()), not the sender,
        # since whether a backscattered signal is still decodable is a
        # receiver-side question. Also set on every NodeItem (not just
        # rx_* ones) for the same no-type_name-check-needed reason.
        # decode_snr_threshold_db is the user-editable SNR figure the
        # dashed decode-range ring treats as "can still be decoded" --
        # NOT a universal truth (see the spinbox's own tooltip and
        # claude/wake-up-range-visualization.md's revision note): it's a
        # reference line the user sets and checks against their own
        # measured BER, since the right SNR for "acceptable decode"
        # genuinely differs by modulation/detector and isn't something
        # this project can claim one fixed number for. Starts at the
        # module default (DECODE_RANGE_SNR_THRESHOLD_DB) rather than a
        # literal 6.0 here, so there's one place to change the shipped
        # default. _identity_highlight_item: lazily-created colored-
        # margin child, see set_identity_highlight() below.
        self.show_wake_ranges = False
        self.show_decode_range = False
        self.decode_snr_threshold_db = DECODE_RANGE_SNR_THRESHOLD_DB
        self._identity_highlight_item = None

        # Combined "reader" block (CanvasWindow.add_reader()): None for
        # every ordinary node. For a reader, the visible node is the
        # sender half; reader_partner points at an rx_* NodeItem that's
        # real bookkeeping but never added to the scene (add_node()'s
        # add_to_scene=False) -- it never draws or can be clicked, so
        # there's nothing to keep in sync. reader_partner on the hidden
        # rx points back at the sender. Plain nodes are always None, so
        # reader checks elsewhere are a simple "is not None" test.
        self.reader_partner = None

        nt = registry.NODE_TYPES[type_name]
        # Auto-wired ports (AUTO_WIRED_PORTS) are excluded from
        # in_ports/out_ports entirely -- no dot, no label. Nothing else
        # looks one up by name (CanvasView._port_near() already refuses
        # to drag from/to an auto-wired port).
        auto_wired_names = AUTO_WIRED_PORTS.get(type_name, set())
        self.in_ports = [p for p in nt.ports if p.direction == "in" and p.name not in auto_wired_names]
        self.out_ports = [p for p in nt.ports if p.direction == "out" and p.name not in auto_wired_names]
        port_rows = max(len(self.in_ports), len(self.out_ports), 1)

        # Icon if ICON_DIR/<type_name>.png exists, else the plain colored
        # rectangle. Either way this stays a QGraphicsRectItem -- the
        # icon is just drawn on top, not a different item type. Layout
        # (size, label position) is picked once here, before
        # super().__init__(), since the icon layout is a different shape
        # (narrower, ports hugging the image), not a resized rectangle.
        icon_pixmap = _load_icon_pixmap(self.icon_type_name)

        if icon_pixmap is not None:
            width = self.ICON_SIZE + 2 * self.ICON_PORT_MARGIN
            # Ports are centered on the icon's OWN vertical span (or a
            # taller band if this type somehow has more port rows than
            # fit inside the image) -- not spread across the whole node
            # including the label row below, the way the plain
            # rectangle's are.
            port_band_h = max(port_rows * self.PORT_SPACING, self.ICON_SIZE)
            height = self.ICON_TOP_MARGIN + port_band_h + self.ICON_LABEL_H
            port_first_y = self.ICON_TOP_MARGIN + port_band_h / 2 - (port_rows - 1) * self.PORT_SPACING / 2
        else:
            width = self.WIDTH
            height = max(self.MIN_HEIGHT, self.HEADER_H + port_rows * self.PORT_SPACING + 10)
            port_first_y = self.HEADER_H + self.PORT_SPACING / 2

        self.width = width  # instance, not the class's WIDTH -- an icon node is narrower

        super().__init__(0, 0, width, height)
        self.setPos(pos.x() - width / 2, pos.y() - height / 2)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)

        if icon_pixmap is not None:
            self.setBrush(QtGui.QBrush(QtCore.Qt.BrushStyle.NoBrush))
            # NoPen/NoBrush: self is still a rectangle underneath (the
            # icon is drawn on top, not a different item type), so any
            # visible pen would outline a rectangle around a round icon.
            # Qt's own dashed selection box still appears on select
            # regardless of pen/brush -- expected.
            self.setPen(QtGui.QPen(QtCore.Qt.PenStyle.NoPen))
            scaled = icon_pixmap.scaled(
                self.ICON_SIZE, self.ICON_SIZE,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation)
            icon_item = QtWidgets.QGraphicsPixmapItem(scaled, self)
            icon_item.setPos(
                (width - scaled.width()) / 2,
                self.ICON_TOP_MARGIN + (port_band_h - scaled.height()) / 2)
            icon_item.setZValue(-1)  # under the ports/label, above the rect

            # Name/id move BELOW the image instead of overlapping its
            # top-left corner, centered under it -- per feedback, once a
            # real icon made the old top-left overlay placement obvious.
            self.label = QtWidgets.QGraphicsTextItem(self)
            self.label.setPlainText(f"{type_name}\n{node_id}")
            text_option = self.label.document().defaultTextOption()
            text_option.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
            self.label.document().setDefaultTextOption(text_option)
            self.label.setTextWidth(width)
            self.label.setPos(0, self.ICON_TOP_MARGIN + port_band_h)
        else:
            self.setBrush(QtGui.QBrush(_node_color(type_name)))
            self.setPen(QtGui.QPen(QtGui.QColor("#333333")))
            self.label = QtWidgets.QGraphicsTextItem(f"{type_name}\n{node_id}", self)
            self.label.setPos(4, 2)

        label_font = self.label.font()
        label_font.setPointSize(8)
        self.label.setFont(label_font)

        # params: every registry Param for this type, seeded at its
        # default and then at NODE_SEED_OVERRIDES' known-good value where
        # one exists -- edited in place by NodeInspector, read whole by
        # scene_to_topology() as that node's "params" dict. payload is
        # per-run DATA (not a registry Param), so it's a separate
        # attribute, same distinction build_fixed_topology() draws
        # between a tag node's "params" and its "payload" key.
        self.params = {p.name: p.default for p in nt.params}
        self.params.update(NODE_SEED_OVERRIDES.get(type_name, {}))
        self.payload = [1, 0, 1, 1, 0] if type_name.startswith("tag_") else None

        # Long-message tags (message_framer.py). "raw": self.payload is
        # the one frame sent, repeated forever. "text": frames
        # message_text into fragments at scene_to_topology() build time
        # and loops the whole message; self.payload is irrelevant
        # (effective_fragment_bits() below is used instead). None/empty
        # for non-tag_* nodes, same as self.payload.
        self.payload_mode = "raw" if type_name.startswith("tag_") else None
        self.message_text = "" if type_name.startswith("tag_") else None
        self.chunk_bytes = 4 if type_name.startswith("tag_") else None
        # "file" mode: third payload_mode -- frames a file's raw bytes
        # via message_framer.build_fragments_from_bytes() (no UTF-8
        # round-trip). file_name is display-only, never transmitted.
        self.file_bytes = b"" if type_name.startswith("tag_") else None
        self.file_name = "" if type_name.startswith("tag_") else None

        # Readable UI-facing replacement for the raw packet_bits param
        # (registry's "Number of Packet Bits") -- "how many times the
        # full on-air cycle (preamble + payload, see full_cycle_bits())
        # repeats inside one wake burst", converted behind the scenes
        # into packet_bits itself instead of making the user hand-edit a
        # ~10000 magic number. None for a non-tag node, same pattern as
        # payload/chunk_bytes/etc. above. packet_bits is derived from it
        # immediately below using the simulator's own default PREAMBLE
        # length as a placeholder (no receiver is wired yet at
        # construction time) -- NodeInspector._update_packets_per_wake_
        # info() recomputes it again against the REAL wired preamble the
        # first time this tag's inspector panel is shown or edited.
        self.packets_per_wake = 3 if type_name.startswith("tag_") else None
        if self.packets_per_wake is not None:
            self.params["packet_bits"] = self.packets_per_wake * self.full_cycle_bits(len(PREAMBLE))

        # Energy-consumption ESTIMATE (NodeInspector._update_energy_
        # info_label()) -- tag-only, UI-only (not registry Params, not
        # read by any GNU Radio block).
        #
        # energy_per_bit is now DERIVED, not free-typed: it's
        # recomputed every refresh from energy_switch_mode plus that
        # mode's own params, via _switch_energy_per_bit_joules(). It's
        # kept as a real attribute (rather than purely a local) only so
        # Save/Load and Copy/Paste have a last-known value to persist,
        # same pattern as before -- the mode + params are the real
        # source of truth now.
        #
        # energy_switch_mode picks which real switch-device physics
        # the "Consumed (switching)" figure uses:
        #   "fet": CMOS/FET gate, dynamic switching power P = C_gate *
        #          Vdd^2 * f -- bit-value-symmetric, bit-rate-
        #          independent per-toggle energy (the standard CV^2
        #          result). energy_c_gate_fF is that gate capacitance.
        #   "bjt": bipolar switch held on via a resistive base bias
        #          (the real Kimionis-thesis tag hardware: Fig 3.5's
        #          2kOhm resistor at 3V) -- draws Vdd^2/R_bias only
        #          while actively biased on, so it's duty-cycle- and
        #          bit-rate-dependent, not a flat per-toggle charge.
        #          energy_r_bias_ohm/energy_duty_cycle are that model's
        #          params.
        # energy_vdd is shared by both modes (same supply rail).
        # Defaults: Vdd=3.0V (the real reference tag's CR2032 coin-cell
        # rail); C_gate=15fF (order-of-magnitude only -- a small-logic-
        # gate textbook example, not pinned to one verified datasheet,
        # deliberately adjustable); R_bias=2000 Ohm (directly from the
        # real Kimionis thesis's own measured circuit, Fig 3.5);
        # duty_cycle=0.5 (a documented approximation for arbitrary
        # data, not derived from the tag's actual transmitted bits).
        #
        # energy_static_rate: optional idle draw, defaults to 0 --
        # UNCHANGED, still illustrative/uncalibrated "units", not
        # joules -- this round's physical grounding only reaches the
        # switching figure, not this one.
        self.energy_per_bit = 1.0 if type_name.startswith("tag_") else None
        self.energy_switch_mode = "fet" if type_name.startswith("tag_") else None
        self.energy_c_gate_fF = 15.0 if type_name.startswith("tag_") else None
        self.energy_vdd = 3.0 if type_name.startswith("tag_") else None
        self.energy_r_bias_ohm = 2000.0 if type_name.startswith("tag_") else None
        self.energy_duty_cycle = 0.5 if type_name.startswith("tag_") else None
        self.energy_static_rate = 0.0 if type_name.startswith("tag_") else None

        # Which output ports get a live "View signal" window --
        # ticked BEFORE Start, not opened while running (tapping an
        # already-running flowgraph visibly froze/stuttered the sim).
        # Tapped once, right after on_start() builds the topology but
        # before mw.start() launches it. Not persisted across Save/Load.
        # See CanvasWindow._open_pending_signal_views().
        self.signal_probe_ports = set()

        # decoder_params: packet_hex_decoder's own param dict, per-
        # receiver (not a separate node, not shared/global). bit_rate/
        # samp_rate seeded here are overwritten by scene_to_topology()'s
        # derivation every Validate/Start regardless of what's set.
        self.decoder_params = (
            {p.name: p.default for p in registry.NODE_TYPES["packet_hex_decoder"].params}
            if type_name.startswith("rx_") else None
        )

        # Auto-wired names are already filtered out above, so none of
        # these are ever auto_wired=True -- that constructor arg is kept
        # for any future port that needs to show but not connect.
        # show_label=False for icon nodes: no on-canvas port-name text.
        show_port_labels = icon_pixmap is None
        self.port_handles = {}  # (direction, name) -> PortHandle
        for i, p in enumerate(self.in_ports):
            h = PortHandle(self, p, show_label=show_port_labels)
            h.setPos(0, port_first_y + i * self.PORT_SPACING)
            h.position_label(width)
            self.port_handles[("in", p.name)] = h
        for i, p in enumerate(self.out_ports):
            h = PortHandle(self, p, show_label=show_port_labels)
            h.setPos(width, port_first_y + i * self.PORT_SPACING)
            h.position_label(width)
            self.port_handles[("out", p.name)] = h

    def set_identity_highlight(self, color):
        """Shows/hides the colored margin around this node's icon/rect.
        CanvasWindow._update_wake_ranges() sets `color` to the tag's
        _tag_identity_color() while its ring is visible, None to hide.
        A thin QGraphicsRectItem child drawn outside this node's rect()
        -- works the same for icon or plain-rectangle nodes. Reused
        (repositioned/recolored) rather than recreated, since
        _update_wake_ranges() redraws the whole set on most edits."""
        if color is None:
            if self._identity_highlight_item is not None:
                self._identity_highlight_item.setVisible(False)
            return
        margin = 5
        rect = self.rect().adjusted(-margin, -margin, margin, margin)
        if self._identity_highlight_item is None:
            self._identity_highlight_item = QtWidgets.QGraphicsRectItem(rect, self)
            self._identity_highlight_item.setBrush(QtGui.QBrush(QtCore.Qt.BrushStyle.NoBrush))
            self._identity_highlight_item.setZValue(5)  # above icon/label, below Qt's selection box
        else:
            self._identity_highlight_item.setRect(rect)
        self._identity_highlight_item.setPen(QtGui.QPen(color, 3))
        self._identity_highlight_item.setVisible(True)

    def effective_fragment_bits(self):
        """Length, in bits, of ONE transmitted frame -- feeds the
        receiver's payload_len/decoder payload_len_bits (see
        CanvasWindow._sync_decoder_params()). Raw mode: len(self.payload).
        Text/file mode: message_framer.fragment_len_bits(chunk_bytes) --
        depends only on chunk_bytes, not message/file content. 0 for a
        non-tag node or an empty raw-mode tag."""
        if self.payload_mode in ("text", "file"):
            return message_framer.fragment_len_bits(self.chunk_bytes)
        return len(self.payload) if self.payload else 0

    def total_frame_bits(self):
        """Total bits over ONE FULL payload cycle (every fragment, back
        to back) -- not just one fragment's length. Needed by
        NodeInspector._update_energy_info_label(): a longer message
        needs more fragments, so energy cost must scale with fragment
        count, not just effective_fragment_bits() alone (a real bug:
        different-length messages showed identical energy cost before
        this fix). Raw mode: same as effective_fragment_bits() (one
        frame looped). Text/file mode: actual fragment count (mirrors
        _update_message_info_label()'s build_fragments() call, 0 instead
        of raising on too-many-fragments) times effective_fragment_bits()."""
        if self.payload_mode not in ("text", "file"):
            return self.effective_fragment_bits()
        try:
            if self.payload_mode == "file":
                fragments = message_framer.build_fragments_from_bytes(
                    self.file_bytes, 0, self.chunk_bytes)
            else:
                fragments = message_framer.build_fragments(self.message_text, 0, self.chunk_bytes)
        except ValueError:
            return 0
        return len(fragments) * self.effective_fragment_bits()

    def full_cycle_bits(self, preamble_bits_len):
        """Total bits in ONE full on-air transmission cycle, preamble
        included -- mirrors plan_topology()'s real frame_bits
        construction (core/modulation_middleware.py) exactly: raw mode
        sends one preamble + one payload (preamble_bits_len +
        effective_fragment_bits()); text/file mode prepends a preamble
        to EVERY fragment (fragment count * (preamble_bits_len +
        effective_fragment_bits())), not just once for the whole
        message. Used only to turn packets_per_wake (a readable repeat
        count) into packet_bits (the raw burst-duration bit count the
        Tier-1 tag block actually wants) -- see NodeInspector.
        _update_packets_per_wake_info()."""
        if self.payload_mode not in ("text", "file"):
            return preamble_bits_len + self.effective_fragment_bits()
        try:
            if self.payload_mode == "file":
                fragments = message_framer.build_fragments_from_bytes(
                    self.file_bytes, 0, self.chunk_bytes)
            else:
                fragments = message_framer.build_fragments(self.message_text, 0, self.chunk_bytes)
        except ValueError:
            return preamble_bits_len + self.effective_fragment_bits()
        num_fragments = len(fragments) or 1
        return num_fragments * (preamble_bits_len + self.effective_fragment_bits())

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if self.canvas is not None:
                self.canvas.update_edges_for_node(self)
        return result


def _edge_channel_role(from_node, to_node):
    """Which implicit channel this edge represents, if any: "forward"
    (sender->tag), "return" (tag->rx), or None otherwise -- including an
    old saved layout's edges touching an explicit "channel" NodeItem box,
    which scene_to_topology() still passes through unchanged for
    backward compatibility."""
    if from_node.type_name == "sender" and to_node.type_name.startswith("tag_"):
        return "forward"
    if from_node.type_name.startswith("tag_") and to_node.type_name.startswith("rx_"):
        return "return"
    return None


class EdgeItem(QtWidgets.QGraphicsPathItem):
    """One connection between an output and input port, drawn as a
    straight line. from_node/to_node are NodeItems; from_port_name/
    to_port_name are registry port names -- the 4-tuple
    scene_to_topology() turns into a plan_topology() edge.

    role/channel_params: a sender->tag_* or tag_*->rx_* edge IS a
    channel (one per edge), seeded like a node's params and live-edited
    via NodeInspector.show_edge(); scene_to_topology() reads
    channel_params when auto-building that edge's channel node. None
    for edges _edge_channel_role() doesn't recognize."""

    # Line thickness in scene pixels. Was 2 -- feedback was "a bit
    # thicker", not a different style, so a modest bump rather than a
    # big jump. Same width for every edge, channel-bearing or not.
    PEN_WIDTH = 5

    def __init__(self, from_node, from_port_name, to_node, to_port_name):
        super().__init__()
        self.from_node = from_node
        self.from_port_name = from_port_name
        self.to_node = to_node
        self.to_port_name = to_port_name
        self.role = _edge_channel_role(from_node, to_node)
        if self.role is not None:
            ch_nt = registry.NODE_TYPES["channel"]
            self.channel_params = {p.name: p.default for p in ch_nt.params}
            if self.role == "forward":
                self.channel_params.update(NODE_SEED_OVERRIDES.get("channel", {}))
            # "return": registry's own defaults already are the
            # validated return-channel values, nothing to layer on.
            #
            # Cast to float: registry.Param("distance", 2, ...) is a
            # plain int, which would build an int QSpinBox -- but
            # sync_distance_from_position() always writes back a float,
            # and QSpinBox.setValue() raises on one. This value is
            # immediately replaced below anyway, but the cast stays as
            # a type guarantee regardless.
            self.channel_params["distance"] = float(self.channel_params["distance"])
        else:
            self.channel_params = None
        # This edge's channel node id from the LAST scene_to_topology()
        # run (regenerated every call) -- None until the first Start/
        # Validate. Lets CanvasWindow._open_pending_signal_views() find
        # this edge's live channel block in TopologyMiddleware.nodes.
        self.live_channel_id = None
        # Same idea as NodeItem.signal_probe_ports -- a channel only ever
        # has the one "out" port, but the set form keeps show_edge()'s
        # checkbox-building code identical to show_node()'s (see
        # NodeInspector._add_signal_checkboxes_for()).
        self.signal_probe_ports = set()

        # Combined-reader bookkeeping (CanvasWindow.add_reader()/
        # _wire_reader_shadow_return()). is_reader_shadow: True only for
        # the invisible tag->rx return edge a reader auto-creates -- never
        # added to the scene and never serialized (reconstructible from
        # reader_partner + the visible forward edge). reader_shadow: set
        # on the visible forward edge, pointing at its shadow, so a
        # channel_params edit mirrors onto both ("one channel, same
        # physical path"). None for non-reader edges.
        self.is_reader_shadow = False
        self.reader_shadow = None
        # Selectable (so clicking it opens NodeInspector.show_edge(), the
        # same "click it like a block" the id/param widgets below give a
        # real node) only when it actually carries a channel -- an old
        # layout's plain sender->channel/channel->tag wire isn't itself
        # the thing to edit, the explicit channel box next to it is.
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable,
                     self.role is not None)
        pen_color = "#5a7fa8" if self.role is not None else "#555555"
        self.setPen(QtGui.QPen(QtGui.QColor(pen_color), self.PEN_WIDTH))
        self.setZValue(-1)
        self.update_path()
        # Compute real distance from node positions at edge creation
        # time, rather than leaving the seeded placeholder (1.0/2.0)
        # until a later drag refreshes it.
        self.sync_distance_from_position()

    def update_path(self):
        p1 = self.from_node.port_handles[("out", self.from_port_name)].scenePos()
        p2 = self.to_node.port_handles[("in", self.to_port_name)].scenePos()
        # Straight line, not a curve. A curve's ENDPOINTS are still the
        # true port positions a bezier's visible bow just doesn't read as
        # "this far apart" the way a plain segment does -- and now that a
        # channel-bearing edge's length doubles as its distance display
        # (see sync_distance_from_position()), a straight line is what
        # makes that reading honest at a glance, not just an arbitrary
        # style choice.
        path = QtGui.QPainterPath(p1)
        path.lineTo(p2)
        self.setPath(path)

    def sync_distance_from_position(self):
        """Recomputes this edge's channel distance from the current gap
        between its two endpoints, using PIXELS_PER_METER. Called from
        __init__ (fresh edge) and from CanvasWindow.
        update_edges_for_node() (a node moved)."""
        if self.role is None or self.channel_params is None:
            return
        p1 = self.from_node.port_handles[("out", self.from_port_name)].scenePos()
        p2 = self.to_node.port_handles[("in", self.to_port_name)].scenePos()
        self.channel_params["distance"] = round(QtCore.QLineF(p1, p2).length() / PIXELS_PER_METER, 2)


class NodePaletteList(QtWidgets.QListWidget):
    """Left-side list of placeable node types -- drag an entry onto the
    canvas to create that node. Standard Qt drag-and-drop (QDrag/
    QMimeData carrying the type name as plain text); CanvasView's
    dropEvent reads it back out."""

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        for type_name in PLACEABLE_NODE_TYPES:
            item = QtWidgets.QListWidgetItem(type_name)
            item.setToolTip(_node_type_tooltip(type_name))
            self.addItem(item)
        # The combined reader (see READER_PLACEHOLDER_TYPE/CanvasWindow.
        # add_reader()) isn't a registry.NODE_TYPES entry, so it can't go
        # through the loop above (_node_type_tooltip() looks its argument
        # up in registry.NODE_TYPES and would KeyError on it) -- added as
        # its own item, with its own hand-written tooltip, instead.
        reader_item = QtWidgets.QListWidgetItem(READER_PLACEHOLDER_TYPE)
        reader_item.setToolTip(
            "Combined reader: one block, one OUTPUT port. Drag this onto the "
            "canvas -- it shows up as a single sender-looking box, one output, "
            "no input -- then wire that one output straight to a tag's "
            "Carrier_In with one arrow to connect both the forward "
            "(sender-to-tag) and return (tag-to-receiver) legs at once, "
            "sharing one measured distance, with self-interference "
            "cancellation on by default. The receiver side has no box of its "
            "own; edit its settings (including modulation) from the "
            "'Receiver (this reader)' section on this same panel."
        )
        self.addItem(reader_item)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if item is None:
            return
        mime = QtCore.QMimeData()
        mime.setText(item.text())
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.exec(QtCore.Qt.DropAction.CopyAction)


class CanvasView(QtWidgets.QGraphicsView):
    """The actual drawing surface. Owns: accepting node-type drops from
    the palette, and the click-drag-release gesture that draws an edge
    between an output port and an input port. Everything else (adding
    the resulting node/edge to the model, keeping edges attached to
    moving nodes) is delegated back to `window` (the CanvasWindow) so
    this class stays about input handling, not app state."""

    # Snap tolerance for a port click/release, in viewport pixels (stays
    # constant across zoom via mapToScene). A second net on top of
    # PortHandle.shape()'s hit area: if itemAt() misses the exact pixel,
    # search a small ring for the nearest matching port instead of
    # giving up. Kept below PORT_SPACING (24px) so it can't snap to the
    # wrong adjacent port.
    SNAP_PIXELS = 12

    def __init__(self, window):
        scene = QtWidgets.QGraphicsScene()
        scene.setSceneRect(-2000, -2000, 4000, 4000)
        super().__init__(scene)
        self.window = window
        self.setAcceptDrops(True)
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        self._pending_edge_start = None  # PortHandle (an "out" port) mid-drag, or None
        self._temp_line = None
        self._hover_target = None  # PortHandle currently snap-highlighted during a drag

    def _port_near(self, viewport_pos, direction):
        """The topmost non-auto_wired PortHandle of the given direction
        exactly under viewport_pos, or -- if none -- the nearest one
        within SNAP_PIXELS screen pixels, or None if there's nothing
        close. auto_wired ports (see AUTO_WIRED_PORTS) never match --
        they're not connectable from the canvas at all."""
        def _matches(candidate):
            return (isinstance(candidate, PortHandle)
                    and candidate.port.direction == direction
                    and not candidate.auto_wired)

        item = self.itemAt(viewport_pos)
        if _matches(item):
            return item

        search_rect = QtCore.QRect(
            viewport_pos.x() - self.SNAP_PIXELS, viewport_pos.y() - self.SNAP_PIXELS,
            self.SNAP_PIXELS * 2, self.SNAP_PIXELS * 2,
        )
        scene_search_rect = self.mapToScene(search_rect).boundingRect()
        target_scene_pos = self.mapToScene(viewport_pos)

        best, best_dist = None, None
        for candidate in self.scene().items(scene_search_rect):
            if _matches(candidate):
                dist = (candidate.scenePos() - target_scene_pos).manhattanLength()
                if best is None or dist < best_dist:
                    best, best_dist = candidate, dist
        return best

    def dragEnterEvent(self, event):
        text = event.mimeData().text() if event.mimeData().hasText() else None
        if text in registry.NODE_TYPES or text == READER_PLACEHOLDER_TYPE:
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        text = event.mimeData().text() if event.mimeData().hasText() else None
        if text in registry.NODE_TYPES or text == READER_PLACEHOLDER_TYPE:
            event.acceptProposedAction()

    def dropEvent(self, event):
        type_name = event.mimeData().text()
        scene_pos = self.mapToScene(event.position().toPoint())
        if type_name == READER_PLACEHOLDER_TYPE:
            self.window.add_reader(scene_pos)
            event.acceptProposedAction()
            return
        if type_name not in registry.NODE_TYPES:
            return
        self.window.add_node(type_name, scene_pos)
        event.acceptProposedAction()

    def mousePressEvent(self, event):
        out_handle = self._port_near(event.position().toPoint(), "out")
        if out_handle is not None:
            self._start_edge_drag(out_handle)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._temp_line is not None:
            pos = event.position().toPoint()
            start = self._pending_edge_start.scenePos()
            end = self.mapToScene(pos)
            path = QtGui.QPainterPath(start)
            path.lineTo(end)
            self._temp_line.setPath(path)

            # Live snap-target feedback: whichever "in" port _port_near()
            # would land on right now gets highlighted, so it's visible
            # BEFORE releasing which port a connection is actually about
            # to form to -- directly answers "did I grab/land on the
            # right one" without having to guess from the crash after.
            candidate = self._port_near(pos, "in")
            if candidate is not self._hover_target:
                if self._hover_target is not None:
                    self._hover_target.set_highlighted(False)
                if candidate is not None:
                    candidate.set_highlighted(True)
                self._hover_target = candidate
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._temp_line is not None:
            in_handle = self._port_near(event.position().toPoint(), "in")
            if in_handle is not None:
                self._finish_edge_drag(in_handle)
            else:
                self._cancel_edge_drag()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        # Ctrl+scroll (either axis) zooms, same convention as most apps
        # with a pannable canvas (image editors, map views, node editors).
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            dy = event.angleDelta().y() or event.angleDelta().x()
            if dy == 0:
                return
            factor = 1.15 if dy > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
            return
        # No modifier: defer to QGraphicsView's own scrollbar wheel
        # handling (pans vertical from angleDelta().y(), horizontal from
        # angleDelta().x()) instead of overriding it to always zoom.
        super().wheelEvent(event)

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        grid = 20
        left = int(rect.left()) - (int(rect.left()) % grid)
        top = int(rect.top()) - (int(rect.top()) % grid)
        lines = []
        x = left
        while x < rect.right():
            lines.append(QtCore.QLineF(x, rect.top(), x, rect.bottom()))
            x += grid
        y = top
        while y < rect.bottom():
            lines.append(QtCore.QLineF(rect.left(), y, rect.right(), y))
            y += grid
        # Low alpha by request -- grid is decorative, not a measuring aid.
        pen = QtGui.QPen(QtGui.QColor(200, 200, 200, 35))
        pen.setWidth(0)
        painter.setPen(pen)
        painter.drawLines(lines)

    def _start_edge_drag(self, out_handle):
        self._pending_edge_start = out_handle
        out_handle.set_highlighted(True)
        self._temp_line = QtWidgets.QGraphicsPathItem()
        self._temp_line.setPen(QtGui.QPen(QtGui.QColor("#4a90d9"), 2, QtCore.Qt.PenStyle.DashLine))
        self.scene().addItem(self._temp_line)

    def _finish_edge_drag(self, in_handle):
        out_handle = self._pending_edge_start
        self.window.add_edge(out_handle.node_item, out_handle.port.name,
                              in_handle.node_item, in_handle.port.name)
        self._cancel_edge_drag()

    def _cancel_edge_drag(self):
        if self._pending_edge_start is not None:
            self._pending_edge_start.set_highlighted(False)
        if self._hover_target is not None:
            self._hover_target.set_highlighted(False)
        if self._temp_line is not None:
            self.scene().removeItem(self._temp_line)
        self._temp_line = None
        self._pending_edge_start = None
        self._hover_target = None


class _NoScrollMixin:
    """Prevents mouse-wheel-while-merely-hovering over a spin box/combo
    from silently changing its value while scrolling the sidebar --
    only a clicked/tabbed-into (focused) field reacts to wheel; otherwise
    the event passes up to the enclosing scroll area.

    GOTCHA: checking self.hasFocus() in wheelEvent() alone is NOT
    enough -- QAbstractSpinBox/QComboBox default to focusPolicy()==
    WheelFocus, and Qt's dispatch grants focus as a side effect of
    delivering the wheel event, before wheelEvent() ever runs. The
    actual fix is forcing focusPolicy() to StrongFocus (click/Tab only,
    excludes WheelFocus) so hovering alone never grants focus."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoScrollSpinBox(_NoScrollMixin, QtWidgets.QSpinBox):
    pass


class NoScrollDoubleSpinBox(_NoScrollMixin, QtWidgets.QDoubleSpinBox):
    pass


class NoScrollComboBox(_NoScrollMixin, QtWidgets.QComboBox):
    pass


def make_param_widget(name, current_value, on_change):
    """Builds the one widget for a single registry Param, dispatched by
    name (ENUM_PARAMS / BOOLEAN_INT_PARAMS) and then by the Python type
    of its current value -- a standalone function (not a NodeInspector
    method) so show_node() can build decoder-param widgets with the same
    dispatch logic as a node's own params, just writing back into
    node_item.decoder_params instead of node_item.params. on_change(name,
    value) is called whenever the widget's value changes."""
    if name in ENUM_PARAMS:
        w = NoScrollComboBox()
        w.addItems(ENUM_PARAMS[name])
        try:
            idx = int(current_value)
        except (TypeError, ValueError):
            idx = 0
        if 0 <= idx < w.count():
            w.setCurrentIndex(idx)
        w.currentIndexChanged.connect(lambda idx, n=name: on_change(n, idx))
        return w
    if name in BOOLEAN_INT_PARAMS:
        w = QtWidgets.QCheckBox()
        w.setChecked(bool(current_value))
        w.stateChanged.connect(lambda _state, n=name, box=w: on_change(n, int(box.isChecked())))
        return w
    # bool before int: Python's bool is an int subclass, so this order
    # matters even though no current registry Param defaults to a bool
    # -- future-proofing, not dead code by accident.
    if isinstance(current_value, bool):
        w = QtWidgets.QCheckBox()
        w.setChecked(current_value)
        w.stateChanged.connect(lambda _state, n=name, box=w: on_change(n, box.isChecked()))
    elif isinstance(current_value, int):
        w = NoScrollSpinBox()
        w.setRange(-10_000_000, 10_000_000)
        w.setValue(current_value)
        w.editingFinished.connect(lambda n=name, box=w: on_change(n, box.value()))
    elif isinstance(current_value, float):
        w = NoScrollDoubleSpinBox()
        w.setRange(-1e12, 1e12)
        w.setDecimals(PARAM_DECIMALS.get(name, 4))
        w.setValue(current_value)
        w.editingFinished.connect(lambda n=name, box=w: on_change(n, box.value()))
    elif isinstance(current_value, (tuple, list)):
        # list, not just tuple: a registry default is a tuple, but any
        # param saved to layout JSON and reloaded comes back as a list
        # (JSON has no tuple type) -- checking tuple alone meant a
        # reloaded command_bits fell through to the generic str(value)
        # branch below, and editing it then handed the widget's raw
        # text straight to the sender's constructor as a literal string
        # (e.g. "[1, 1, 0, 1]"), crashing on Start with a pybind11
        # argument-type error. Always reconstructed as a tuple on
        # change, same as a fresh node's default -- gnuradio's blocks
        # accept a tuple wherever a list is declared, confirmed by the
        # existing hardcoded tuple default already working.
        w = QtWidgets.QLineEdit(",".join(str(v) for v in current_value))
        w.editingFinished.connect(lambda n=name, box=w: on_change(
            n, tuple(int(v.strip()) for v in box.text().split(",") if v.strip() != "")))
    else:
        w = QtWidgets.QLineEdit(str(current_value))
        w.editingFinished.connect(lambda n=name, box=w: on_change(n, box.text()))
    return w


_PAYLOAD_LEN_BITS_TOOLTIP = (
    "Auto-derived from whatever tag(s) feed this receiver -- not something to "
    "set by hand (_sync_decoder_params() overwrites it on every rebuild). For a "
    "Text/File-mode tag this total is chunk_bytes*8 PLUS a 40-bit header the "
    "simulation adds to track tag/fragment identity + a checksum -- that header "
    "isn't part of what you typed, so this number will read larger than "
    "chunk_bytes*8 alone (see the tag's own 'Bytes per fragment' tooltip for the "
    "breakdown). For a Raw-bits tag there's no header at all, so this is exactly "
    "that tag's payload length."
)


def _format_sim_header(sim_header):
    """One short string for the Fragments table's "Sim Info" column --
    see TopologyMiddleware.get_packets()'s "sim_header" key. None (a
    raw/non-framed tag's packet, which has no header at all) renders
    as an em dash rather than an empty cell, so it reads as "not
    applicable here" rather than "missing/broken"."""
    if sim_header is None:
        return "—"
    status = "" if sim_header["crc_ok"] else " ✗ CRC fail"
    return (
        f"tag {sim_header['tag_id']}, frag {sim_header['frag_index']}/"
        f"{sim_header['frag_count']}{status}"
    )


def _hex_str_to_bits(hex_str, n_bits):
    """Reverses packet_hex_decoder_epy_block_0.py's own bit-packing
    (_dispatch_packet(): 8 bits/byte, MSB first, a final partial byte
    left-shift-padded with zeros) so a decoded packet's hex dump --
    already emitted by that Tier-1 block exactly as-is, nothing added
    there -- can be compared bit-for-bit against ground truth for BER.
    n_bits (pkt["bits"], the real collected-bit count before padding)
    truncates off any padding bits the last partial byte added."""
    bits = []
    for byte_str in hex_str.split():
        val = int(byte_str, 16)
        bits.extend((val >> shift) & 1 for shift in range(7, -1, -1))
    return bits[:n_bits]


def _worst_bit_run(fragments, preamble_bits):
    """Longest run of identical bits across the CONTINUOUSLY LOOPED
    transmission (preamble + every fragment, wrapped once to catch the
    loop seam). This is what the DC-blocker droop problem (see
    whitening-fix-findings.md) depends on -- the header is whitened,
    but the payload is deliberately NOT, so any message whose bytes
    contain a long run can still need a larger dc_len.
    _update_message_info_label() surfaces this directly instead of
    "crank dc_len until it works"."""
    stream = []
    for frag in fragments:
        stream += preamble_bits + list(frag)
    looped = stream + stream
    best = cur = 0
    prev = None
    for b in looped:
        cur = cur + 1 if b == prev else 1
        best = max(best, cur)
        prev = b
    return best


class WaveformWidget(QtWidgets.QWidget):
    """Minimal, dependency-free oscilloscope: a rolling sample window
    drawn with plain QPainter -- not gr-qtgui (Qt-binding risk) or
    matplotlib/pyqtgraph (extra dependency); see core/signal_probe.py.
    Complex signal: real part solid, imaginary dashed. Float/byte: one
    line. Y-axis auto-scales to the window. Also shows a numeric power
    readout and, for complex signals, a color-swatch legend.

    2026-10-22: switched from "accumulate and trim" to "replace with the
    latest snapshot" -- matches core/signal_probe.py's own switch to a
    fixed-size ring with no decimation. There's no backlog to trim here
    any more: SignalProbe.read_snapshot() already hands back something
    bounded to _NUM_POINTS (1024), so set_samples() just displays
    whatever it's given, same as GNU Radio's own free-running sink mode
    (each poll shows "the latest window", not an attempt to stitch
    together every sample that ever arrived)."""

    # Below this, treat the signal as silence rather than reporting a
    # huge negative number every frame it's gated off -- a REAL
    # vector_sink reporting exactly 0.0 (a gated-off tag, a channel with
    # nothing feeding it yet) would otherwise make 10*log10(0) blow up
    # to -inf and print nonsense.
    POWER_FLOOR_DB = -120.0

    def __init__(self, is_complex, samp_rate=None):
        super().__init__()
        self.is_complex = is_complex
        # Read straight off the tapped block's own get_samp_rate() (see
        # SignalProbe._read_samp_rate()) -- None if that node type's
        # sample-rate param wasn't recognized. Only used for the
        # informational time-span readout in paintEvent(); never for any
        # buffering/decimation decision.
        self.samp_rate = samp_rate
        self._real = np.empty(0, dtype=np.float64)
        self._imag = np.empty(0, dtype=np.float64) if is_complex else None
        self.setMinimumSize(480, 260)
        self.setStyleSheet("background-color: #1e1e1e;")

    def set_samples(self, samples):
        """Replaces the displayed window wholesale with SignalProbe's
        current ring-buffer snapshot. Not an accumulate-and-trim: the
        ring itself already bounds how much there is (see
        core/signal_probe.py's module docstring for why that's the
        fix), so there's nothing left to trim here -- just convert and
        draw whatever came back."""
        if not samples:
            return
        arr = np.asarray(samples, dtype=np.complex128 if self.is_complex else np.float64)
        if self.is_complex:
            self._real = arr.real
            self._imag = arr.imag
        else:
            self._real = arr
        self.update()

    def _power_db(self):
        """Mean power of the current rolling window, in dB relative to
        amplitude 1.0 (dBFS-style -- no absolute calibration in this
        signal chain, labeled "rel." wherever drawn)."""
        if self._real.size == 0:
            return None
        if self.is_complex:
            mean_sq = float(np.mean(self._real * self._real + self._imag * self._imag))
        else:
            mean_sq = float(np.mean(self._real * self._real))
        if mean_sq <= 0.0:
            return self.POWER_FLOOR_DB
        return max(10.0 * math.log10(mean_sq), self.POWER_FLOOR_DB)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QtGui.QColor("#1e1e1e"))

        if self._real.size == 0:
            painter.setPen(QtGui.QColor("#888888"))
            painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                              "Waiting for samples…")
            return

        if self.is_complex:
            vmax = float(max(self._real.max(), self._imag.max()))
            vmin = float(min(self._real.min(), self._imag.min()))
        else:
            vmax = float(self._real.max())
            vmin = float(self._real.min())
        if vmax == vmin:  # flat signal (e.g. a silent gated tag) -- avoid a divide-by-zero
            vmax += 1.0
            vmin -= 1.0
        span = vmax - vmin

        def y_of(v):
            return h - 1 - int((v - vmin) / span * (h - 1))

        # Zero line, when zero actually falls within the current window.
        if vmin <= 0.0 <= vmax:
            zero_y = y_of(0.0)
            painter.setPen(QtGui.QColor("#444444"))
            painter.drawLine(0, zero_y, w, zero_y)

        def draw_trace(trace, color, dashed):
            n = trace.size
            if n < 2:
                return
            pen = QtGui.QPen(QtGui.QColor(color))
            pen.setWidth(2)
            if dashed:
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            pixel_cols = max(int(w), 1)
            if n <= pixel_cols:
                # Sparse (at most ~1 sample/pixel): a single connected
                # path reads as a continuous trace, and cost is trivial
                # at this point count regardless of content.
                path = QtGui.QPainterPath()
                step = w / max(n - 1, 1)
                path.moveTo(0.0, y_of(float(trace[0])))
                for i in range(1, n):
                    path.lineTo(i * step, y_of(float(trace[i])))
                painter.drawPath(path)
            else:
                # Dense: one independent vertical segment per pixel
                # column (that column's min/max -- NumPy reduceat(),
                # not average, so brief spikes stay visible), drawn via
                # drawLines() rather than a single continuous
                # QPainterPath. This isn't just a style choice -- a
                # stroked QPainterPath's join/miter computation between
                # consecutive points gets measurably expensive once the
                # trace reverses direction often (e.g. noise/silence
                # being sliced essentially at random, every sample)
                # even at a FIXED, already-decimated point count:
                # measured 200x+ slower for a maximally jagged trace
                # than a smooth one at the same point count, in this
                # project's own offscreen-Qt timing probe. Independent
                # per-column lines have no joins between unrelated
                # columns, so cost stays flat (~pixel_cols cheap draws)
                # no matter how noisy the underlying samples are.
                starts = np.linspace(0, n, pixel_cols + 1)[:-1].astype(np.int64)
                col_mins = np.minimum.reduceat(trace, starts)
                col_maxs = np.maximum.reduceat(trace, starts)
                col_width = w / pixel_cols
                lines = [
                    QtCore.QLineF(col * col_width, y_of(float(col_mins[col])),
                                   col * col_width, y_of(float(col_maxs[col])))
                    for col in range(starts.size)
                ]
                painter.drawLines(lines)

        real_color = "#5dade2"
        imag_color = "#f5b041"
        draw_trace(self._real, real_color, dashed=False)
        if self.is_complex:
            draw_trace(self._imag, imag_color, dashed=True)

        # Legend -- an actual swatch (a short line segment in the
        # trace's own color/style) next to each trace's label, not just
        # text colored to match: text stays in the same muted ink
        # either way so it's readable regardless of the trace color
        # underneath it. Only drawn for the complex case -- two traces
        # is the only time identity ("which line is which") isn't
        # already obvious from there being just the one.
        muted = QtGui.QColor("#aaaaaa")
        if self.is_complex:
            legend_y = 14
            for i, (label, color, dashed) in enumerate(
                    [("I", real_color, False), ("Q", imag_color, True)]):
                x0 = 8 + i * 70
                pen = QtGui.QPen(QtGui.QColor(color))
                pen.setWidth(2)
                if dashed:
                    pen.setStyle(QtCore.Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(x0, legend_y, x0 + 20, legend_y)
                painter.setPen(muted)
                painter.drawText(x0 + 26, legend_y + 4, label)

        # Numeric readout -- mean power over the current rolling window,
        # in dB relative to amplitude 1.0 (see _power_db()'s own
        # docstring on why "rel." rather than a real dBm/dBW figure).
        # Muted ink, top-right corner, same as the legend labels -- this
        # is informational text, not another trace, so it doesn't get a
        # trace color of its own.
        db = self._power_db()
        if db is not None:
            db_text = f"Power: {db:.1f} dB (rel.)"
            painter.setPen(muted)
            metrics = painter.fontMetrics()
            text_w = metrics.horizontalAdvance(db_text)
            painter.drawText(w - text_w - 8, 16, db_text)

        # Time-span readout -- how much real time the current window
        # actually represents (sample count / the tapped block's own
        # samp_rate, read once at connect time -- see SignalProbe), so
        # "it looks dense/compressed" has an honest number next to it
        # instead of being a mystery. Bottom-left corner: never competes
        # with the legend (top-left) or the power readout (top-right).
        # Omitted entirely when samp_rate wasn't resolvable for this
        # node type, rather than showing a guessed figure.
        if self.samp_rate and self._real.size:
            span_s = self._real.size / self.samp_rate
            if span_s < 1e-3:
                span_text = f"{span_s * 1e6:.0f} µs"
            elif span_s < 1.0:
                span_text = f"{span_s * 1e3:.1f} ms"
            else:
                span_text = f"{span_s:.2f} s"
            readout = f"{self._real.size} pts @ {self.samp_rate / 1e6:.2f} MSps ({span_text})"
            painter.setPen(muted)
            painter.drawText(8, h - 8, readout)


class SignalViewWindow(QtWidgets.QWidget):
    """Free-floating, non-modal window showing every ticked SignalProbe's
    live output stacked vertically in one column (GRC-style, not one
    popup per port), inside a QScrollArea so many ticked ports don't
    grow the window unbounded. One shared poll timer for all rows.
    Tears every probe down on close, whichever side closes first (the
    user, or CanvasWindow.on_stop() force-closing before the sim
    itself stops)."""

    POLL_INTERVAL_MS = 50

    def __init__(self, on_closed):
        super().__init__()
        self._on_closed = on_closed
        self._torn_down = False
        self._rows = []  # list of (probe, waveform_widget), polled together
        self.setWindowTitle("Signal Monitor")
        self.resize(560, 420)

        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        self._rows_container = QtWidgets.QWidget()
        self._rows_layout = QtWidgets.QVBoxLayout(self._rows_container)
        scroll.setWidget(self._rows_container)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(self.POLL_INTERVAL_MS)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

    def add_probe(self, title, probe):
        """Adds one more stacked row for an already-created SignalProbe.
        A divider precedes every row after the first, so N rows get
        exactly N-1 separators rather than a trailing one after the
        last row."""
        if self._rows:
            divider = QtWidgets.QFrame()
            divider.setFrameShape(QtWidgets.QFrame.Shape.HLine)
            divider.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
            self._rows_layout.addWidget(divider)

        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet("font-weight: bold;")
        self._rows_layout.addWidget(title_label)

        waveform = WaveformWidget(is_complex=(probe.dtype == "complex"),
                                   samp_rate=probe.samp_rate)
        self._rows_layout.addWidget(waveform)

        self._rows.append((probe, waveform))

    def _poll(self):
        for probe, waveform in self._rows:
            waveform.set_samples(probe.read_snapshot())

    def closeEvent(self, event):
        self.timer.stop()
        if not self._torn_down:
            for probe, _waveform in self._rows:
                probe.teardown()
            self._torn_down = True
        self._on_closed(self)
        super().closeEvent(event)


class NodeInspector(QtWidgets.QWidget):
    """Right-side property panel for the currently selected node.
    Rebuilds its param form from registry.NODE_TYPES[...].params on
    every selection change -- one widget per Param, dispatched by
    Python type (bool->checkbox, int->spinbox, float->double spinbox,
    tuple->comma text, else plain text)."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.current_node = None
        self.current_edge = None  # set instead of current_node when an
                                   # EdgeItem (a per-tag channel -- see
                                   # show_edge()) is what's selected
        self.param_widgets = {}
        self.decoder_param_widgets = {}
        self.reader_rx_param_widgets = {}
        self.reader_decoder_param_widgets = {}
        # Which NodeItem the wake-range checkbox edits -- a standalone
        # sender, or a reader's sender half (current_node is always
        # normalized to the rx half for a reader, so this can't just
        # reuse current_node). None when neither applies.
        self._wake_range_owner = None
        # Which NodeItem the "Show decode range" checkbox/threshold
        # spinbox edit -- a standalone rx_*, or a reader's hidden rx half
        # (reader_rx_item). Lives on the RECEIVER, deliberately not the
        # same owner as _wake_range_owner above -- see the dynamic rows
        # in show_node() that set this. None when neither applies.
        self._decode_range_owner = None
        # (label_widget, field_widget) pairs hidden/shown by the
        # Advanced toggle, rebuilt on every form rebuild.
        self._advanced_param_rows = []
        # Persists across selections so switching nodes doesn't
        # collapse the toggle each time.
        self._show_advanced = False

        self.setMinimumWidth(300)
        outer = QtWidgets.QVBoxLayout(self)

        self.title_label = QtWidgets.QLabel("No node selected")
        self.title_label.setStyleSheet("font-weight: bold;")
        outer.addWidget(self.title_label)

        id_row = QtWidgets.QFormLayout()
        self.id_edit = QtWidgets.QLineEdit()
        self.id_edit.editingFinished.connect(self._on_id_changed)
        id_row.addRow("Node ID", self.id_edit)
        outer.addLayout(id_row)

        # Everything below the Node ID -- modulation/payload selectors,
        # the params form, and the ports summary -- lives in ONE
        # scrollable area, not a fixed header above a separately-
        # scrolling params box.
        self.scroll_content = QtWidgets.QWidget()
        self.scroll_layout = QtWidgets.QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)

        # Modulation switcher, tag_*/rx_* only (see MODULATION_CHOICES).
        # Separate from params_form: a structural type choice, not a
        # tunable value.
        self.modulation_label = QtWidgets.QLabel("Modulation")
        self.modulation_combo = NoScrollComboBox()
        self.modulation_combo.addItems(MODULATION_CHOICES)
        self._updating_modulation_combo = False
        self.modulation_combo.currentTextChanged.connect(self._on_modulation_changed)
        self.scroll_layout.addWidget(self.modulation_label)
        self.scroll_layout.addWidget(self.modulation_combo)

        # "Show tag wake-up ranges" -- sender-only. Takes effect
        # immediately (CanvasWindow._update_wake_ranges()); state lives
        # on the sender's show_wake_ranges, not persisted across
        # Save/Load -- a view toggle, not backend state. The decode-range
        # ring is a SEPARATE toggle living on the receiver instead (see
        # "Show decode range" in the Decoder/Receiver section below) --
        # it used to be bolted onto this same checkbox, but that put a
        # receiver-side concern (can THIS decoder actually decode it)
        # behind a control that only exists on the sender, which doesn't
        # generalize to a standalone, separately-placed rx at all.
        self.wake_range_checkbox = QtWidgets.QCheckBox("Show tag wake-up ranges")
        self.wake_range_checkbox.setToolTip(
            "Draws one colored ring per tag wired directly to this sender (a forward "
            "edge), centered on the sender, sized by that tag's own Harvesting "
            "Threshold (p_th) against this sender's Tx Power and each edge's own "
            "channel settings (frequency, antenna gains) -- see "
            "CanvasWindow._update_wake_ranges(). Forward leg only: being inside the "
            "ring means that tag CAN receive enough power to turn on, not that it "
            "will decode correctly -- that's the separate, receiver-owned 'Show "
            "decode range' ring (select the receiver, or this reader's own Receiver "
            "Settings section below)."
        )
        self.wake_range_checkbox.toggled.connect(self._on_wake_range_toggled)
        self.scroll_layout.addWidget(self.wake_range_checkbox)

        # Payload mode: "Raw bits" is one hand-typed frame repeated
        # forever; "Text message" frames message_text into fragments
        # and loops the whole thing (message_framer.py). tag_* only.
        self.payload_mode_label = QtWidgets.QLabel("Payload source")
        self.payload_mode_combo = NoScrollComboBox()
        self.payload_mode_combo.addItems(["Raw bits", "Text message", "File"])
        self._updating_payload_mode_combo = False
        self.payload_mode_combo.currentTextChanged.connect(self._on_payload_mode_changed)
        self.scroll_layout.addWidget(self.payload_mode_label)
        self.scroll_layout.addWidget(self.payload_mode_combo)

        self.payload_label = QtWidgets.QLabel("Payload (bits, comma-separated)")
        self.payload_edit = QtWidgets.QLineEdit()
        self.payload_edit.editingFinished.connect(self._on_payload_changed)
        self.scroll_layout.addWidget(self.payload_label)
        self.scroll_layout.addWidget(self.payload_edit)

        # Text mode: message content, bytes per fragment, and a live
        # read-only summary of how that resolves.
        self.message_text_label = QtWidgets.QLabel("Message")
        self.message_text_edit = QtWidgets.QLineEdit()
        self.message_text_edit.editingFinished.connect(self._on_message_text_changed)
        self.scroll_layout.addWidget(self.message_text_label)
        self.scroll_layout.addWidget(self.message_text_edit)

        # File mode: a file picker instead of typed text. Framed the
        # same way as text mode (message_framer.build_fragments_from_
        # bytes()); chunk_bytes/message_info_label below are shared
        # with text mode rather than duplicated.
        self.file_picker_button = QtWidgets.QPushButton("Choose File...")
        self.file_picker_button.clicked.connect(self._on_choose_file_clicked)
        self.file_info_label = QtWidgets.QLabel("")
        self.file_info_label.setWordWrap(True)
        self.file_info_label.setStyleSheet("color: #666666;")
        self.scroll_layout.addWidget(self.file_picker_button)
        self.scroll_layout.addWidget(self.file_info_label)

        self.chunk_bytes_label = QtWidgets.QLabel("Bytes per fragment")
        self.chunk_bytes_spin = NoScrollSpinBox()
        self.chunk_bytes_spin.setRange(1, message_framer.MAX_CHUNK_BYTES)
        self.chunk_bytes_spin.valueChanged.connect(self._on_chunk_bytes_changed)
        self.scroll_layout.addWidget(self.chunk_bytes_label)
        self.scroll_layout.addWidget(self.chunk_bytes_spin)

        self.message_info_label = QtWidgets.QLabel("")
        self.message_info_label.setWordWrap(True)
        self.message_info_label.setStyleSheet("color: #666666;")
        self.scroll_layout.addWidget(self.message_info_label)

        # Preamble is deliberately receiver-side-only -- a tag has no
        # preamble of its own, it transmits whatever its governing
        # receiver's decoder expects. This label shows the tag's actual
        # resolved preamble and where to change it, without a second
        # editable copy that could drift out of sync.
        self.preamble_info_label = QtWidgets.QLabel("")
        self.preamble_info_label.setWordWrap(True)
        # Styled as a standout callout (not the same flat gray as the
        # other info labels around it) -- this is the one place a tag's
        # actual on-air preamble is visible at all, and it was easy to
        # miss sitting among plain helper text. See this method's own
        # docstring for why it's read-only here.
        self.preamble_info_label.setStyleSheet(
            "color: #5c4500; font-weight: 600; padding: 3px 6px; "
            "background-color: #fff3cd; border: 1px solid #ffca28; "
            "border-radius: 4px;"
        )
        self.scroll_layout.addWidget(self.preamble_info_label)

        # Readable replacement for the tag's own (now-disabled, see
        # show_node()'s packet_bits special case) packet_bits param --
        # "how many times the full on-air cycle above repeats inside one
        # wake burst". Persistent (not rebuilt per show_node() call),
        # same reasoning as preamble_info_label just above: both depend
        # on live content state (message/file/chunk_bytes) that can
        # change while a tag's inspector panel stays open. Converted
        # into packet_bits behind the scenes by _update_packets_per_
        # wake_info() -- see NodeItem.packets_per_wake/full_cycle_bits().
        self.packets_per_wake_label = QtWidgets.QLabel("Packets to Send")
        self.packets_per_wake_spin = NoScrollSpinBox()
        self.packets_per_wake_spin.setRange(1, 1000)
        self.packets_per_wake_spin.valueChanged.connect(self._on_packets_per_wake_changed)
        self.scroll_layout.addWidget(self.packets_per_wake_label)
        self.scroll_layout.addWidget(self.packets_per_wake_spin)

        # Energy model -- tag-only; see _update_energy_info_label().
        # NodeItem attributes, not registry Params, not read by any
        # GNU Radio block (claude/energy-consumption-estimate.md).
        # "Consumed (switching)" is now derived from a real FET/CMOS or
        # BJT switch-device formula (_switch_energy_per_bit_joules()),
        # in real joules -- "Consumed (staying active)" (energy_
        # static_rate) is UNCHANGED, still illustrative/uncalibrated
        # "units". Grouped in one QGroupBox so show_node()/show_edge()
        # toggle one widget's visibility instead of every child
        # individually.
        self.energy_group = QtWidgets.QGroupBox("Energy")
        energy_layout = QtWidgets.QVBoxLayout(self.energy_group)
        self.scroll_layout.addWidget(self.energy_group)

        # Switch-technology mode: picks which real switch-device
        # physics the "Consumed (switching)" figure below is derived
        # from (_switch_energy_per_bit_joules()) -- no longer a free-
        # typed constant. "fet": CMOS/FET gate, dynamic C*V^2 switching
        # (bit-value-symmetric, bit-rate-independent). "bjt": bipolar
        # switch held on via a resistive base bias, matching the real
        # Kimionis-thesis tag hardware (duty-cycle- and bit-rate-
        # dependent -- see _switch_energy_per_bit_joules()'s docstring
        # for why these two need different shapes, not just different
        # constants).
        self.energy_mode_label = QtWidgets.QLabel("Switch technology")
        self.energy_mode_combo = NoScrollComboBox()
        self.energy_mode_combo.addItem("FET / CMOS (dynamic C·V²)", "fet")
        self.energy_mode_combo.addItem("BJT (resistive base bias)", "bjt")
        self.energy_mode_combo.setToolTip(
            "Which real switch-device physics the switching-energy figure "
            "below is derived from. FET/CMOS: flat cost per bit, independent "
            "of bit rate. BJT: cost depends on duty cycle and bit rate -- "
            "matches the bipolar transistors (NXP/Agilent/CEL) actually used "
            "in the reference backscatter-tag hardware this model cites."
        )
        self.energy_mode_combo.currentIndexChanged.connect(self._on_energy_mode_changed)
        energy_layout.addWidget(self.energy_mode_label)
        energy_layout.addWidget(self.energy_mode_combo)

        self.energy_vdd_label = QtWidgets.QLabel("Supply voltage Vdd (V)")
        self.energy_vdd_spin = NoScrollDoubleSpinBox()
        self.energy_vdd_spin.setRange(0.01, 1e3)
        self.energy_vdd_spin.setDecimals(3)
        self.energy_vdd_spin.setSingleStep(0.1)
        self.energy_vdd_spin.setToolTip(
            "Shared by both switch-technology modes. Default 3.0V -- the "
            "reference tag's CR2032 coin-cell supply rail."
        )
        self.energy_vdd_spin.valueChanged.connect(self._on_energy_vdd_changed)
        energy_layout.addWidget(self.energy_vdd_label)
        energy_layout.addWidget(self.energy_vdd_spin)

        self.energy_c_gate_label = QtWidgets.QLabel("Gate capacitance C_gate (fF)")
        self.energy_c_gate_spin = NoScrollDoubleSpinBox()
        self.energy_c_gate_spin.setRange(0.0, 1e9)
        self.energy_c_gate_spin.setDecimals(3)
        self.energy_c_gate_spin.setSingleStep(1.0)
        self.energy_c_gate_spin.setToolTip(
            "FET/CMOS mode only. Order-of-magnitude default (15fF, a small-"
            "logic-gate textbook example) -- not pinned to one verified "
            "datasheet. Adjust freely; E_bit = C_gate * Vdd^2."
        )
        self.energy_c_gate_spin.valueChanged.connect(self._on_energy_c_gate_changed)
        energy_layout.addWidget(self.energy_c_gate_label)
        energy_layout.addWidget(self.energy_c_gate_spin)

        self.energy_r_bias_label = QtWidgets.QLabel("Base-bias resistor R_bias (Ω)")
        self.energy_r_bias_spin = NoScrollDoubleSpinBox()
        self.energy_r_bias_spin.setRange(0.01, 1e9)
        self.energy_r_bias_spin.setDecimals(2)
        self.energy_r_bias_spin.setSingleStep(100.0)
        self.energy_r_bias_spin.setToolTip(
            "BJT mode only. Default 2000Ω -- directly from the Kimionis "
            "reference thesis's own measured tag circuit (Fig 3.5)."
        )
        self.energy_r_bias_spin.valueChanged.connect(self._on_energy_r_bias_changed)
        energy_layout.addWidget(self.energy_r_bias_label)
        energy_layout.addWidget(self.energy_r_bias_spin)

        self.energy_duty_cycle_label = QtWidgets.QLabel("Duty cycle (fraction of bit time biased on)")
        self.energy_duty_cycle_spin = NoScrollDoubleSpinBox()
        self.energy_duty_cycle_spin.setRange(0.0, 1.0)
        self.energy_duty_cycle_spin.setDecimals(3)
        self.energy_duty_cycle_spin.setSingleStep(0.05)
        self.energy_duty_cycle_spin.setToolTip(
            "BJT mode only. Default 0.5 -- a documented approximation for "
            "arbitrary data, not derived from this tag's actual transmitted "
            "bit values."
        )
        self.energy_duty_cycle_spin.valueChanged.connect(self._on_energy_duty_cycle_changed)
        energy_layout.addWidget(self.energy_duty_cycle_label)
        energy_layout.addWidget(self.energy_duty_cycle_spin)

        self.energy_static_rate_label = QtWidgets.QLabel("Energy per second (while active)")
        self.energy_static_rate_spin = NoScrollDoubleSpinBox()
        self.energy_static_rate_spin.setRange(0.0, 1e9)
        self.energy_static_rate_spin.setDecimals(4)
        self.energy_static_rate_spin.setSingleStep(0.1)
        self.energy_static_rate_spin.setToolTip(
            "Optional baseline draw for staying active during transmission, "
            "independent of bit count. Defaults to 0."
        )
        self.energy_static_rate_spin.valueChanged.connect(self._on_energy_static_rate_changed)
        energy_layout.addWidget(self.energy_static_rate_label)
        energy_layout.addWidget(self.energy_static_rate_spin)

        # Clear visual break between the two editable inputs above and
        # the read-only computed results below.
        self.energy_result_divider = QtWidgets.QFrame()
        self.energy_result_divider.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        self.energy_result_divider.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        energy_layout.addWidget(self.energy_result_divider)

        # Four computed results (time/switch/static/harvest), each a
        # bold value line + a small grey calculation line underneath.
        # Read-only QLabels, not line edits.
        self._energy_result_rows = []
        for attr_prefix in ("time", "switch", "static", "harvest"):
            value_label = QtWidgets.QLabel("")
            value_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
            value_label.setWordWrap(True)
            desc_label = QtWidgets.QLabel("")
            desc_label.setStyleSheet("color: #888888; font-size: 8pt;")
            desc_label.setWordWrap(True)
            energy_layout.addWidget(value_label)
            energy_layout.addWidget(desc_label)
            setattr(self, f"energy_{attr_prefix}_value_label", value_label)
            setattr(self, f"energy_{attr_prefix}_desc_label", desc_label)
            self._energy_result_rows.append((value_label, desc_label))

        # Advanced toggle hides every row whose registry Param has
        # basic=False, driven by Param.basic (registry.py), not a
        # hardcoded field list. self.advanced_toggle_button is NOT
        # created here -- it's a row INSIDE params_form (see
        # _insert_advanced_toggle_row()), rebuilt fresh on every form
        # rebuild same as every other param widget; self._show_advanced
        # is the state that actually survives rebuilds.
        self.params_form_widget = QtWidgets.QWidget()
        self.params_form = QtWidgets.QFormLayout(self.params_form_widget)
        # Label above field (WrapAllRows), not side-by-side -- long
        # labels with a fixed panel width used to push edit boxes out
        # of view, requiring horizontal scroll.
        self.params_form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapAllRows)
        self.params_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        # params_form_widget is a plain child of scroll_layout -- scrolls
        # together with the selectors above in one scroll area.
        self.scroll_layout.addWidget(self.params_form_widget)

        self.ports_label = QtWidgets.QLabel("")
        self.ports_label.setWordWrap(True)
        self.scroll_layout.addWidget(self.ports_label)

        # "View signal" checkboxes, one per output port shown. Ticking
        # one only records intent (node_item.signal_probe_ports); the
        # tap connects once at Start, before mw.start() launches it
        # (CanvasWindow._open_pending_signal_views()) -- tapping a
        # running flowgraph mid-run visibly froze the sim. Rebuilt per
        # selection like params_form. Disabled while running (see
        # _update_signal_checkboxes_enabled()).
        self.signal_checks_widget = QtWidgets.QWidget()
        self.signal_checks_layout = QtWidgets.QVBoxLayout(self.signal_checks_widget)
        self.signal_checks_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.addWidget(self.signal_checks_widget)
        self._signal_checks = []  # (checkbox, probe_owner, port_name) triples

        self.scroll_layout.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.scroll_content)
        outer.addWidget(scroll, 1)

        self.setEnabled(False)

    def show_node(self, node_item):
        # A reader's hidden rx half is never scene-added, so node_item
        # can only ever be the sender half in practice -- the
        # startswith("rx_") branch below is defensive/dead but kept as
        # cheap insurance. node_item is normalized to the sender half
        # here so everything below can treat it as an ordinary sender,
        # with reader_rx_item tracked separately for the modulation
        # combo and "Receiver (this reader)" section.
        reader_rx_item = None
        if node_item is not None and node_item.reader_partner is not None:
            if node_item.type_name.startswith("rx_"):
                node_item = node_item.reader_partner
            reader_rx_item = node_item.reader_partner

        self.current_node = node_item
        self.current_edge = None
        self._clear_params_form()
        self._clear_signal_checks()

        if node_item is None:
            self.setEnabled(False)
            self.title_label.setText("No node selected")
            self.id_edit.setText("")
            self.id_edit.setEnabled(True)
            self.modulation_label.setVisible(False)
            self.modulation_combo.setVisible(False)
            self.wake_range_checkbox.setVisible(False)
            self._wake_range_owner = None
            self._decode_range_owner = None
            self.payload_mode_label.setVisible(False)
            self.payload_mode_combo.setVisible(False)
            self.payload_label.setVisible(True)
            self.payload_edit.setVisible(True)
            self.payload_edit.setText("")
            self.message_text_label.setVisible(False)
            self.message_text_edit.setVisible(False)
            self.file_picker_button.setVisible(False)
            self.file_info_label.setVisible(False)
            self.chunk_bytes_label.setVisible(False)
            self.chunk_bytes_spin.setVisible(False)
            self.message_info_label.setVisible(False)
            self.preamble_info_label.setVisible(False)
            self.packets_per_wake_label.setVisible(False)
            self.packets_per_wake_spin.setVisible(False)
            self.energy_group.setVisible(False)
            self.ports_label.setText("")
            return

        self.setEnabled(True)
        self.id_edit.setEnabled(True)
        self.title_label.setText(
            f"{node_item.type_name}  (combined reader -- rx: {reader_rx_item.type_name})"
            if reader_rx_item is not None else node_item.type_name)
        self.id_edit.setText(node_item.node_id)

        # Modulation selector: "a selector to choose the modulation
        # technique for the RX" was the original ask, verbatim -- for a
        # reader that's the hidden rx's own type, never node_item's own
        # (node_item is the sender here, and TYPE_TO_MODULATION has no
        # "sender" entry at all). Unaffected for a plain standalone
        # tag_*/rx_* node, where reader_rx_item is just None.
        modulation_source = reader_rx_item if reader_rx_item is not None else node_item
        modulation = TYPE_TO_MODULATION.get(modulation_source.type_name)
        show_modulation = modulation is not None
        self.modulation_label.setVisible(show_modulation)
        self.modulation_combo.setVisible(show_modulation)
        if show_modulation:
            self._updating_modulation_combo = True
            idx = self.modulation_combo.findText(modulation)
            if idx >= 0:
                self.modulation_combo.setCurrentIndex(idx)
            self._updating_modulation_combo = False

        # "Show tag wake-up ranges" lives conceptually on a SENDER
        # (show_wake_ranges, see that checkbox's own comment) -- node_
        # item IS always the sender half now, whether standalone or a
        # reader's visible half (see the normalization at the top of
        # this method), so this no longer needs a separate reader case
        # at all: it's just "is this a sender", full stop.
        is_sender = node_item.type_name == "sender"
        self._wake_range_owner = node_item if is_sender else None
        show_wake_range_ui = is_sender
        self.wake_range_checkbox.setVisible(show_wake_range_ui)
        if show_wake_range_ui:
            self.wake_range_checkbox.blockSignals(True)
            self.wake_range_checkbox.setChecked(self._wake_range_owner.show_wake_ranges)
            self.wake_range_checkbox.blockSignals(False)

        # "Show decode range" lives on the RECEIVER, not the sender --
        # see the dynamic row built in the standalone-rx "Decoder (this
        # receiver)" section and the reader's "Receiver Settings (this
        # reader)" section below, both of which set
        # self._decode_range_owner themselves right before building it.
        # Defaulted to None here so a node type that's neither (a tag_*,
        # a plain sender with no reader_partner) correctly shows nothing.
        self._decode_range_owner = None

        is_tag = node_item.payload is not None
        is_raw_mode = is_tag and node_item.payload_mode == "raw"
        is_text_mode = is_tag and node_item.payload_mode == "text"
        is_file_mode = is_tag and node_item.payload_mode == "file"
        # chunk_bytes/message_info_label are shared between text and
        # file mode -- "how many bytes per fragment" and "how many
        # fragments does this resolve to" mean exactly the same thing
        # either way, see message_framer.build_fragments_from_bytes().
        is_framed_mode = is_text_mode or is_file_mode

        self.payload_mode_label.setVisible(is_tag)
        self.payload_mode_combo.setVisible(is_tag)
        if is_tag:
            self._updating_payload_mode_combo = True
            idx = self.payload_mode_combo.findText(
                "File" if is_file_mode else "Text message" if is_text_mode else "Raw bits")
            if idx >= 0:
                self.payload_mode_combo.setCurrentIndex(idx)
            self._updating_payload_mode_combo = False

        self.payload_label.setVisible(is_raw_mode)
        self.payload_edit.setVisible(is_raw_mode)
        if is_raw_mode:
            self.payload_edit.setText(",".join(str(b) for b in node_item.payload))

        self.message_text_label.setVisible(is_text_mode)
        self.message_text_edit.setVisible(is_text_mode)
        if is_text_mode:
            self.message_text_edit.setText(node_item.message_text)

        self.file_picker_button.setVisible(is_file_mode)
        self.file_info_label.setVisible(is_file_mode)
        if is_file_mode:
            self._update_file_info_label()

        self.chunk_bytes_label.setVisible(is_framed_mode)
        self.chunk_bytes_spin.setVisible(is_framed_mode)
        self.message_info_label.setVisible(is_framed_mode)
        if is_framed_mode:
            self.chunk_bytes_spin.blockSignals(True)
            self.chunk_bytes_spin.setValue(node_item.chunk_bytes)
            self.chunk_bytes_spin.blockSignals(False)
            self._update_message_info_label()

        self.preamble_info_label.setVisible(is_tag)
        if is_tag:
            self._update_preamble_info_label(node_item)

        self.packets_per_wake_label.setVisible(is_tag)
        self.packets_per_wake_spin.setVisible(is_tag)
        if is_tag:
            # Before the generic params loop below builds the (now-
            # disabled) packet_bits widget -- self.param_widgets is
            # already the fresh, empty dict by this point (see
            # _clear_params_form(), called at the very top of this
            # method), so this only writes into node_item.params; the
            # widget itself picks up the freshly-derived value when the
            # loop creates it from node_item.params.get("packet_bits", ...).
            self._update_packets_per_wake_info(node_item)

        self.energy_group.setVisible(is_tag)
        if is_tag:
            mode_idx = self.energy_mode_combo.findData(node_item.energy_switch_mode)
            self.energy_mode_combo.blockSignals(True)
            self.energy_mode_combo.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)
            self.energy_mode_combo.blockSignals(False)

            self.energy_vdd_spin.blockSignals(True)
            self.energy_vdd_spin.setValue(node_item.energy_vdd)
            self.energy_vdd_spin.blockSignals(False)
            self.energy_c_gate_spin.blockSignals(True)
            self.energy_c_gate_spin.setValue(node_item.energy_c_gate_fF)
            self.energy_c_gate_spin.blockSignals(False)
            self.energy_r_bias_spin.blockSignals(True)
            self.energy_r_bias_spin.setValue(node_item.energy_r_bias_ohm)
            self.energy_r_bias_spin.blockSignals(False)
            self.energy_duty_cycle_spin.blockSignals(True)
            self.energy_duty_cycle_spin.setValue(node_item.energy_duty_cycle)
            self.energy_duty_cycle_spin.blockSignals(False)

            self.energy_static_rate_spin.blockSignals(True)
            self.energy_static_rate_spin.setValue(node_item.energy_static_rate)
            self.energy_static_rate_spin.blockSignals(False)

            self._update_energy_mode_visibility(node_item.energy_switch_mode)
            self._update_energy_info_label()

        # Recompute decoder config before building the params form, so
        # the panel never shows a stale placement-time default.
        # notify=False: this call IS the refresh, must not retrigger
        # show_node(). For a reader, redirected to the hidden rx
        # (node_item is the sender; its decoder_params is always None).
        decoder_sync_target = reader_rx_item if reader_rx_item is not None else node_item
        if decoder_sync_target.decoder_params is not None:
            self.window._sync_decoder_params(
                decoder_sync_target, self.window._find_upstream_tags(decoder_sync_target),
                notify=False)

        nt = registry.NODE_TYPES[node_item.type_name]
        # "Transmission Settings" header, reader-only -- a plain
        # standalone sender has nothing to separate its params from, so
        # it keeps the header-less look. Modulation combo/wake-range
        # tick/Gated Mode stay at the top either way.
        if reader_rx_item is not None:
            self._add_section_header("Transmission Settings")
        # Toggle lands right before the first advanced (basic=False)
        # row, same as every other param -- enable_gated included,
        # since the registry now marks it basic=False everywhere it
        # appears. Types without any advanced params at all (none
        # currently) would fall back to the trailing insert below.
        advanced_toggle_inserted = False
        for p in nt.params:
            # SI cancellation is meaningless for a standalone rx (no
            # co-located sender) -- hidden entirely rather than just
            # defaulted off. A reader's own si_enable/si_isolation_db
            # show normally in "Receiver (this reader)" below, since
            # that IS the monostatic case this cancellation is for.
            if p.name in ("si_enable", "si_isolation_db") and node_item.type_name.startswith("rx_"):
                continue
            widget = self._make_param_widget(p.name, node_item.params.get(p.name, p.default))
            if p.name == "packet_bits" and node_item.payload is not None:
                # Derived now from the readable "Packets to Send" field
                # above (NodeItem.packets_per_wake/full_cycle_bits(), see
                # _update_packets_per_wake_info()) instead of hand-typed
                # directly -- same "disable + explain" convention as
                # bit_rate/samp_rate in the decoder section below, and
                # for the same reason: a value typed here would silently
                # stop matching what plan_topology() actually puts on
                # the wire the next time the message/preamble changes.
                widget.setEnabled(False)
                widget.setToolTip(
                    "Derived automatically from 'Packets to Send' above (one "
                    "full preamble+payload cycle's bit length, repeated that "
                    "many times) -- edit Packets to Send instead of this field."
                )
            if p.name == "enable_gated":
                # Same placement rule as every other param (not p.basic
                # decides basic vs. advanced) -- just with the inline
                # label-beside-checkbox layout instead of _add_param_row's
                # label-above-field. basic=False (the current registry
                # default for this param): toggle goes in BEFORE the row,
                # and the row starts hidden/shown with the toggle, like
                # any other advanced field. basic=True (kept for any
                # future param that wants this inline style while staying
                # always-visible): preserves the original behaviour of
                # forcing the toggle immediately after it.
                if not advanced_toggle_inserted and not p.basic:
                    self._insert_advanced_toggle_row()
                    advanced_toggle_inserted = True
                self._add_inline_checkbox_row(p.label or p.name, widget, not p.basic)
                self.param_widgets[p.name] = widget
                if p.basic and not advanced_toggle_inserted:
                    self._insert_advanced_toggle_row()
                    advanced_toggle_inserted = True
                continue
            if not advanced_toggle_inserted and not p.basic:
                self._insert_advanced_toggle_row()
                advanced_toggle_inserted = True
            self._add_param_row(p.label or p.name, widget, not p.basic)
            self.param_widgets[p.name] = widget
        if not advanced_toggle_inserted:
            self._insert_advanced_toggle_row()

        # Decoder settings, standalone rx_* only in this block (a
        # reader's decoder settings live in the "Receiver (this reader)"
        # section below, reusing this same per-param logic against
        # reader_rx_item). bit_rate/samp_rate stay disabled -- always
        # derived by scene_to_topology(), a wrong value here would be a
        # lie, not just unused.
        # dnt is defined unconditionally since both the standalone-rx
        # block and the reader's decoder block below need it.
        dnt = registry.NODE_TYPES["packet_hex_decoder"]
        if node_item.decoder_params is not None:
            self.params_form.addRow(QtWidgets.QLabel("<b>Decoder (this receiver)</b>"))
            for p in dnt.params:
                # FM0/Miller carry their own payload_len/preamble
                # (registry.py). Skip showing them again here -- the
                # receiver's own params section is the single editable
                # copy, mirrored into decoder_params by
                # _sync_decoder_params. OOK/BPSK have no such params, so
                # nothing is redundant for them.
                if p.name == "payload_len_bits" and "payload_len" in node_item.params:
                    continue
                if p.name == "preamble" and "preamble" in node_item.params:
                    continue
                widget = make_param_widget(
                    p.name, node_item.decoder_params.get(p.name, p.default),
                    self._on_decoder_param_changed)
                if p.name in ("bit_rate", "samp_rate"):
                    widget.setEnabled(False)
                    widget.setToolTip(
                        "Derived automatically for this receiver (see "
                        "scene_to_topology()'s decoder auto-wiring) -- OOK/BPSK and "
                        "FM0/Miller need different values here, so it's not something "
                        "to set by hand. Edit bit rate on the tag or this receiver "
                        "itself instead."
                    )
                elif p.name == "preamble":
                    widget.setToolTip(
                        "What this receiver's decoder hunts for, AND what every "
                        "upstream tag actually transmits -- plan_topology() resolves "
                        "each tag's preamble by walking forward to its own receiver's "
                        "decoder, so editing it here changes both sides of the match "
                        "consistently (used to be a fixed, hardcoded value ignored by "
                        "this field -- fixed now, see plan_topology()'s docstring)."
                    )
                elif p.name == "payload_len_bits":
                    widget.setToolTip(_PAYLOAD_LEN_BITS_TOOLTIP)
                self._add_param_row(p.label or p.name, widget, not p.basic)
                self.decoder_param_widgets[p.name] = widget

            # Decode-range ring controls -- receiver-owned (see
            # NodeItem.__init__'s show_decode_range comment), so they
            # belong in THIS receiver's own section, not the sender's.
            self._add_decode_range_controls(node_item)

        # "Receiver Settings (this reader)" -- the hidden rx half,
        # rendered as its own section with a divider (_add_section_
        # header()) to stay visually separate from "Transmission
        # Settings" above. Includes si_enable/si_isolation_db
        # unconditionally (unlike a standalone rx) -- this IS the
        # monostatic case SI cancellation is for. Same registry loop as
        # above, writing into reader_rx_item.params, reusing the one
        # Advanced toggle already inserted rather than adding a second.
        if reader_rx_item is not None:
            self._add_section_header("Receiver Settings (this reader)")
            rnt = registry.NODE_TYPES[reader_rx_item.type_name]
            for p in rnt.params:
                widget = make_param_widget(
                    p.name, reader_rx_item.params.get(p.name, p.default),
                    self._on_reader_rx_param_changed)
                self._add_param_row(p.label or p.name, widget, not p.basic)
                self.reader_rx_param_widgets[p.name] = widget

            # This reader's own decoder settings -- same per-param logic
            # as the standalone-rx "Decoder (this receiver)" block above
            # (bit_rate/samp_rate disabled, FM0/Miller's own payload_len/
            # preamble deduped against reader_rx_item.params instead of
            # node_item's), just targeting the hidden rx and writing
            # through _on_reader_decoder_param_changed instead.
            if reader_rx_item.decoder_params is not None:
                self.params_form.addRow(QtWidgets.QLabel("<b>Decoder (this reader's receiver)</b>"))
                for p in dnt.params:
                    if p.name == "payload_len_bits" and "payload_len" in reader_rx_item.params:
                        continue
                    if p.name == "preamble" and "preamble" in reader_rx_item.params:
                        continue
                    widget = make_param_widget(
                        p.name, reader_rx_item.decoder_params.get(p.name, p.default),
                        self._on_reader_decoder_param_changed)
                    if p.name in ("bit_rate", "samp_rate"):
                        widget.setEnabled(False)
                        widget.setToolTip(
                            "Derived automatically for this receiver (see "
                            "scene_to_topology()'s decoder auto-wiring) -- OOK/BPSK and "
                            "FM0/Miller need different values here, so it's not something "
                            "to set by hand. Edit bit rate on the tag or this reader's "
                            "receiver section above instead."
                        )
                    elif p.name == "preamble":
                        widget.setToolTip(
                            "What this receiver's decoder hunts for, AND what every "
                            "upstream tag actually transmits -- plan_topology() resolves "
                            "each tag's preamble by walking forward to its own receiver's "
                            "decoder, so editing it here changes both sides of the match "
                            "consistently."
                        )
                    elif p.name == "payload_len_bits":
                        widget.setToolTip(_PAYLOAD_LEN_BITS_TOOLTIP)
                    self._add_param_row(p.label or p.name, widget, not p.basic)
                    self.reader_decoder_param_widgets[p.name] = widget

            # Decode-range ring controls for this reader's hidden rx
            # half -- same helper as the standalone-rx case above, just
            # targeting reader_rx_item instead of node_item.
            self._add_decode_range_controls(reader_rx_item)

        port_descriptions = ", ".join(f"{p.name} ({p.direction}/{p.dtype})" for p in nt.ports)
        if reader_rx_item is not None:
            rx_port_descriptions = ", ".join(
                f"{p.name} ({p.direction}/{p.dtype})" for p in registry.NODE_TYPES[reader_rx_item.type_name].ports)
            self.ports_label.setText(
                f"Ports (this block): {port_descriptions}\n"
                f"Ports (hidden receiver): {rx_port_descriptions}")
        else:
            self.ports_label.setText(f"Ports: {port_descriptions}")

        self._add_signal_checkboxes_for(node_item.type_name, node_item, node_item.node_id)
        if reader_rx_item is not None:
            # So "View signal: Data_Out" (the decoded bitstream) is still
            # reachable for a reader, same as it always was when the rx
            # itself used to be the visible half -- this is the one
            # capability that would otherwise quietly disappear by only
            # ever building checkboxes for node_item's own ports.
            self._add_signal_checkboxes_for(
                reader_rx_item.type_name, reader_rx_item, reader_rx_item.node_id)

    def show_edge(self, edge_item):
        """The per-tag channel on a sender->tag_* or tag_*->rx_* edge
        (EdgeItem.role/channel_params), shown in this same Block panel
        instead of a separate tab."""
        self.current_node = None
        self.current_edge = edge_item
        self._clear_params_form()
        self._clear_signal_checks()

        self.setEnabled(True)
        role_text = ("Forward (sender -> tag)" if edge_item.role == "forward"
                     else "Return (tag -> receiver)")
        self.title_label.setText(f"channel -- {role_text}")
        self.id_edit.setText(f"{edge_item.from_node.node_id}  →  {edge_item.to_node.node_id}")
        self.id_edit.setEnabled(False)  # identified by its endpoints, not renamed
        self.modulation_label.setVisible(False)
        self.modulation_combo.setVisible(False)
        self.wake_range_checkbox.setVisible(False)
        self._wake_range_owner = None
        self._decode_range_owner = None
        self.payload_mode_label.setVisible(False)
        self.payload_mode_combo.setVisible(False)
        self.payload_label.setVisible(False)
        self.payload_edit.setVisible(False)
        self.message_text_label.setVisible(False)
        self.message_text_edit.setVisible(False)
        self.file_picker_button.setVisible(False)
        self.file_info_label.setVisible(False)
        self.chunk_bytes_label.setVisible(False)
        self.chunk_bytes_spin.setVisible(False)
        self.message_info_label.setVisible(False)
        self.preamble_info_label.setVisible(False)
        self.packets_per_wake_label.setVisible(False)
        self.packets_per_wake_spin.setVisible(False)
        self.energy_group.setVisible(False)

        ch_nt = registry.NODE_TYPES["channel"]
        # channel has no "enable_gated" row -- always the generic rule,
        # right before the first basic=False row (see the matching
        # comment in show_node()).
        advanced_toggle_inserted = False
        for p in ch_nt.params:
            widget = make_param_widget(
                p.name, edge_item.channel_params.get(p.name, p.default),
                self._on_channel_param_changed)
            if not advanced_toggle_inserted and not p.basic:
                self._insert_advanced_toggle_row()
                advanced_toggle_inserted = True
            self._add_param_row(p.label or p.name, widget, not p.basic)
            self.param_widgets[p.name] = widget
        if not advanced_toggle_inserted:
            self._insert_advanced_toggle_row()

        # Interference_In is deliberately not surfaced at all -- nothing
        # wires it on a per-edge channel (TopologyMiddleware zero-sources
        # any channel's unconnected Interference_In automatically, see
        # plan_topology()'s own docstring), so there's nothing here to
        # click or connect for it.
        self.ports_label.setText(
            "This channel's Interference_In is left unconnected (silently "
            "zero-sourced) -- nothing to wire for it here."
        )

        # live_channel_id is only assigned by scene_to_topology() (i.e.
        # once Validate/Start has actually run) -- doesn't matter here,
        # unlike the old button version: ticking this checkbox just
        # records intent on edge_item.signal_probe_ports, and
        # CanvasWindow._open_pending_signal_views() reads
        # edge_item.live_channel_id fresh, AFTER the next
        # scene_to_topology() call, not whatever stale value this show_
        # edge() call happened to see.
        self._add_signal_checkboxes_for("channel", edge_item, "channel")

    def refresh_after_run_state_change(self):
        """Called from CanvasWindow.on_start()/on_stop() -- re-shows
        whatever node or edge is currently selected so its "View signal"
        checkboxes reflect the state that just changed: enabled/disabled
        (ticking one has no effect while already running, so they're
        greyed out then). A no-op when nothing is selected -- there are
        no checkboxes to refresh in that case."""
        if self.current_node is not None:
            self.show_node(self.current_node)
        elif self.current_edge is not None:
            self.show_edge(self.current_edge)

    def _clear_params_form(self):
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        self.param_widgets = {}
        self.decoder_param_widgets = {}
        self.reader_rx_param_widgets = {}
        self.reader_decoder_param_widgets = {}
        # The form was just torn down -- every row in it, including any
        # advanced ones, is gone, so the tracking list has to start empty
        # again too (the rebuild below repopulates it).
        self._advanced_param_rows = []

    def _clear_signal_checks(self):
        while self.signal_checks_layout.count():
            item = self.signal_checks_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._signal_checks = []

    def _add_signal_checkboxes_for(self, type_name, owner, title_prefix):
        """Adds one "View signal: <port>" checkbox per OUTPUT port
        `type_name` declares. `owner` is the NodeItem/EdgeItem the state
        lives on (owner.signal_probe_ports); ticking only edits that set
        -- CanvasWindow._open_pending_signal_views() acts on it at the
        next Start. Shared by show_node()/show_edge(). `title_prefix` is
        unused now (titles are built in _open_pending_signal_views()),
        kept so callers don't need to change."""
        nt = registry.NODE_TYPES[type_name]
        out_ports = [p for p in nt.ports if p.direction == "out"]
        for p in out_ports:
            label = f"View signal: {p.name}" if len(out_ports) > 1 else "View signal"
            checkbox = QtWidgets.QCheckBox(label)
            checkbox.blockSignals(True)
            checkbox.setChecked(p.name in owner.signal_probe_ports)
            checkbox.blockSignals(False)
            checkbox.toggled.connect(
                lambda checked, ow=owner, pn=p.name: self._on_signal_check_toggled(ow, pn, checked)
            )
            self.signal_checks_layout.addWidget(checkbox)
            self._signal_checks.append((checkbox, owner, p.name))
        self._update_signal_checks_enabled()

    def _on_signal_check_toggled(self, owner, port_name, checked):
        if checked:
            owner.signal_probe_ports.add(port_name)
        else:
            owner.signal_probe_ports.discard(port_name)

    def _update_signal_checks_enabled(self):
        """Greys out signal checkboxes while running -- ticking one then
        would silently do nothing until the next Start."""
        running = self.window.mw is not None
        for checkbox, _owner, _port_name in self._signal_checks:
            checkbox.setEnabled(not running)
            checkbox.setToolTip(
                "Stop the simulation to change which signals are shown -- "
                "ticking this only takes effect the next time you Start."
                if running else ""
            )

    def _make_param_widget(self, name, current_value):
        return make_param_widget(name, current_value, self._on_param_changed)

    def _add_param_row(self, label_text, widget, is_advanced):
        """Adds one row to params_form; an advanced (basic=False) row
        is also tracked for the toggle and starts matching the toggle's
        current state, so switching nodes while "Show advanced" is on
        doesn't visually reset it. The one place that reads Param.basic
        -- applies uniformly to every node/edge type, no special-casing."""
        self.params_form.addRow(label_text, widget)
        if is_advanced:
            label_widget = self.params_form.labelForField(widget)
            self._advanced_param_rows.append((label_widget, widget))
            widget.setVisible(self._show_advanced)
            if label_widget is not None:
                label_widget.setVisible(self._show_advanced)

    def _insert_advanced_toggle_row(self):
        """Builds a fresh Advanced-toggle button row at the caller's
        current position in params_form. Rebuilt every call (not a
        persistent instance) since _clear_params_form() deletes every
        row's Qt widgets on rebuild -- self._show_advanced is the state
        that actually persists."""
        self.advanced_toggle_button = QtWidgets.QPushButton()
        self.advanced_toggle_button.setCheckable(True)
        self.advanced_toggle_button.blockSignals(True)
        self.advanced_toggle_button.setChecked(self._show_advanced)
        self.advanced_toggle_button.blockSignals(False)
        self._update_advanced_toggle_text()
        self.advanced_toggle_button.toggled.connect(self._on_advanced_toggled)
        self.params_form.addRow(self.advanced_toggle_button)

    def _add_inline_checkbox_row(self, label_text, checkbox_widget, is_advanced=False):
        """Gated Mode only: keeps its checkbox beside its label instead
        of WrapAllRows' label-above-field stacking (a form-wide setting
        with no per-row override), via a composite single-field widget.
        is_advanced mirrors _add_param_row()'s tracking so this row
        also respects the Advanced toggle -- there's no separate label
        column to register here (addRow() takes the one composite
        widget), so the whole row_widget (label + checkbox together)
        is what gets hidden/shown."""
        row_widget = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QtWidgets.QLabel(label_text))
        row_layout.addWidget(checkbox_widget)
        row_layout.addStretch(1)
        self.params_form.addRow(row_widget)
        if is_advanced:
            self._advanced_param_rows.append((None, row_widget))
            row_widget.setVisible(self._show_advanced)

    def _add_decode_range_controls(self, receiver_item):
        """Builds the "Show decode range" checkbox + "Decode SNR
        Threshold (dB)" spinbox as two dynamic rows in params_form,
        targeting receiver_item -- a standalone rx_* node, or a reader's
        hidden rx half. Called from both places show_node() needs it
        (its own comment at each call site says which) rather than
        duplicated, since the logic is identical either way, just a
        different target NodeItem. These live on the RECEIVER, not the
        sender -- deliberately a separate control from wake_range_
        checkbox above (see that checkbox's own comment for why: a
        decode-SNR concern bolted onto a sender-only control doesn't
        generalize to a standalone, separately-placed rx).

        Always visible (no Advanced-toggle gating), same treatment the
        sender's own "Show tag wake-up ranges" gets in scroll_layout --
        this is a view toggle plus its calibration value, not a
        physics param. Fresh widgets every show_node() call (same as
        every other row in this method), since params_form itself is
        cleared and rebuilt each time."""
        self._decode_range_owner = receiver_item
        checkbox = QtWidgets.QCheckBox("Show decode range")
        checkbox.setToolTip(
            "Draws a dashed ring, centered on THIS receiver, around every tag wired "
            "to it that also has a known sender -- the maximum distance at which the "
            "backscattered signal's SNR against this return leg's own noise still "
            "clears the Decode SNR Threshold below. Return leg only, same as the "
            "sender's solid wake-up ring is forward-leg only -- being inside this "
            "ring means the channel-noise SNR clears the threshold, not that self-"
            "interference cancellation or the real DC-blocker/slicer will actually "
            "lock onto it (deliberately excluded, same reason as the wake-up ring)."
        )
        checkbox.setChecked(receiver_item.show_decode_range)
        checkbox.toggled.connect(self._on_decode_range_toggled)
        self.params_form.addRow(checkbox)

        threshold_label = QtWidgets.QLabel("Decode SNR Threshold (dB)")
        threshold_spin = NoScrollDoubleSpinBox()
        threshold_spin.setRange(-40.0, 60.0)
        threshold_spin.setDecimals(1)
        threshold_spin.setSingleStep(0.5)
        threshold_spin.setValue(receiver_item.decode_snr_threshold_db)
        threshold_spin.setToolTip(
            "SNR (dB) the ring above treats as \"can still be decoded\" for THIS "
            "receiver. NOT a universal truth -- it's a reference value you set and "
            "check against your own measured BER (see the Fragments table's BER "
            "column), since the SNR a modulation/decoder actually needs differs by "
            "type. Default 6dB is just where this feature's reference thesis "
            "happened to measure range for its own decoder; raise this if you're "
            "seeing real bit errors near the ring's edge, lower it if the ring looks "
            "too conservative."
        )
        threshold_spin.valueChanged.connect(self._on_decode_threshold_changed)
        self.params_form.addRow(threshold_label, threshold_spin)

    def _add_section_header(self, text):
        """Adds one bold, centered section-header row to params_form
        ("Transmission Settings" / "Receiver Settings") with a thin
        divider ahead of it (skipped if it'd be the form's first row).
        Deliberately not a QGroupBox -- stays inside the one shared
        QFormLayout, same lightweight pattern as the Decoder
        sub-section's plain bold-label marker. Centered, not
        left-aligned, so it reads as a heading rather than another
        field label."""
        if self.params_form.rowCount() > 0:
            divider = QtWidgets.QFrame()
            divider.setFrameShape(QtWidgets.QFrame.Shape.HLine)
            divider.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
            self.params_form.addRow(divider)
        label = QtWidgets.QLabel(f"<b>{text}</b>")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.params_form.addRow(label)

    def _update_advanced_toggle_text(self):
        self.advanced_toggle_button.setText(
            "Hide advanced parameters ▲" if self._show_advanced
            else "Show advanced parameters ▼")

    def _on_advanced_toggled(self, checked):
        self._show_advanced = checked
        self._update_advanced_toggle_text()
        for label_widget, field_widget in self._advanced_param_rows:
            field_widget.setVisible(checked)
            if label_widget is not None:
                label_widget.setVisible(checked)

    def _on_decoder_param_changed(self, name, value):
        if self.current_node is not None and self.current_node.decoder_params is not None:
            self.current_node.decoder_params[name] = value

    def _on_reader_rx_param_changed(self, name, value):
        """Edits the OTHER half of a combined reader -- the hidden rx's
        own params dict -- from the "Receiver (this reader)" section in
        show_node(). self.current_node is always normalized to the
        sender half for a reader (see show_node()'s own comment), so
        this can't just reuse _on_param_changed(); it writes through
        current_node.reader_partner instead."""
        if self.current_node is not None and self.current_node.reader_partner is not None:
            self.current_node.reader_partner.params[name] = value
            # si_enable/bit_rate and friends don't feed _wake_up_radius_m()
            # the way tx_power_send does, but refreshing unconditionally
            # here is cheap and matches _on_param_changed()'s own
            # "just always refresh" reasoning rather than special-casing
            # which of the rx's own param names would actually matter.
            self.window._update_wake_ranges()

    def _on_reader_decoder_param_changed(self, name, value):
        """Edits the OTHER half of a combined reader -- the hidden rx's
        own decoder_params dict -- from the "Decoder (this reader's
        receiver)" section in show_node(). Mirrors _on_decoder_param_
        changed() exactly, just through current_node.reader_partner
        instead of current_node itself, same reasoning as _on_reader_rx_
        param_changed() right above."""
        if (self.current_node is not None and self.current_node.reader_partner is not None
                and self.current_node.reader_partner.decoder_params is not None):
            self.current_node.reader_partner.decoder_params[name] = value

    def _on_wake_range_toggled(self, checked):
        if self._wake_range_owner is not None:
            self._wake_range_owner.show_wake_ranges = checked
            self.window._update_wake_ranges()

    def _on_decode_range_toggled(self, checked):
        if self._decode_range_owner is not None:
            self._decode_range_owner.show_decode_range = checked
            self.window._update_wake_ranges()

    def _on_decode_threshold_changed(self, value):
        if self._decode_range_owner is not None:
            self._decode_range_owner.decode_snr_threshold_db = value
            self.window._update_wake_ranges()

    def _on_param_changed(self, name, value):
        if self.current_node is not None:
            self.current_node.params[name] = value
            # tx_power_send (sender) and p_th (any tag) both feed
            # _wake_up_radius_m() -- cheap enough at this canvas's scale
            # to just always refresh rather than checking `name` against
            # those two specifically.
            self.window._update_wake_ranges()
            # bit_rate and p_th both feed _update_energy_info_label()
            # too (duration = N/bit_rate; p_th gates the harvested
            # figure) -- same "just always refresh" reasoning. A no-op
            # for a non-tag node (that label isn't even visible then).
            self._update_energy_info_label()

    def _on_channel_param_changed(self, name, value):
        if self.current_edge is None or self.current_edge.channel_params is None:
            return
        self.current_edge.channel_params[name] = value
        # freq/g_reader_dbi/g_tag_dbi all feed _wake_up_radius_m() too --
        # same reasoning as _on_param_changed() above, always refresh
        # rather than special-casing which param names matter. The
        # "distance" branch below ALSO ends up here indirectly (via
        # set_channel_edge_distance() -> itemChange() -> update_edges_
        # for_node()), so this call is harmless-redundant in that case,
        # not wrong.
        self.window._update_wake_ranges()
        if name == "distance":
            # Keep the canvas honest: typing a new distance moves the
            # tag end of this edge to match, the same proportional-
            # distance relationship a drag keeps (see
            # CanvasWindow.set_channel_edge_distance() and
            # EdgeItem.sync_distance_from_position()). That setPos()
            # call re-derives this exact field's value right back from
            # the new position afterward (refresh_channel_distance()),
            # so this is a request, not a guaranteed final number --
            # it'll settle within rounding of what was typed.
            self.window.set_channel_edge_distance(self.current_edge, value)

    def refresh_channel_distance(self, edge_item):
        """Keeps the Distance field live while its edge is open and a
        node is being dragged (or after set_channel_edge_distance()'s own
        move) -- without this, the inspector would keep showing whatever
        distance was true when the panel was last (re)built, not the
        number that now matches the arrow actually on screen."""
        if self.current_edge is not edge_item:
            return
        widget = self.param_widgets.get("distance")
        if widget is None:
            return
        widget.blockSignals(True)
        widget.setValue(edge_item.channel_params["distance"])
        widget.blockSignals(False)

    def _on_modulation_changed(self, text):
        if self._updating_modulation_combo or self.current_node is None:
            return
        # For a reader, this combo is showing (and editing) the HIDDEN
        # rx's modulation, not current_node's own -- current_node is
        # always the sender half here (see show_node()'s normalization),
        # and _convert_node_modulation() only knows how to rebuild a
        # tag_*/rx_* node in the first place (TYPE_TO_MODULATION has no
        # "sender" entry, so passing current_node itself for a reader
        # would silently do nothing -- findText() on the combo wouldn't
        # even have matched a modulation to show it in the first place
        # if that were the target).
        target = self.current_node
        if target.type_name == "sender" and target.reader_partner is not None:
            target = target.reader_partner
        self.window._convert_node_modulation(target, text)

    def _on_payload_changed(self):
        if self.current_node is None:
            return
        try:
            bits = [int(b.strip()) for b in self.payload_edit.text().split(",") if b.strip() != ""]
        except ValueError:
            QtWidgets.QMessageBox.warning(self.window, "Invalid payload",
                                           "Payload must be comma-separated 0/1 bits.")
            return
        self.current_node.payload = bits
        self._update_energy_info_label()  # raw-mode payload length feeds N bits
        self._update_packets_per_wake_info(self.current_node)  # raw-mode length feeds packet_bits too

    def _on_payload_mode_changed(self, text):
        if self._updating_payload_mode_combo or self.current_node is None:
            return
        self.current_node.payload_mode = {"Text message": "text", "File": "file"}.get(text, "raw")
        self.show_node(self.current_node)  # rebuild to flip which widgets show

    def _on_message_text_changed(self):
        if self.current_node is None:
            return
        self.current_node.message_text = self.message_text_edit.text()
        self._update_message_info_label()
        self._update_energy_info_label()
        self._update_packets_per_wake_info(self.current_node)

    def _on_choose_file_clicked(self):
        """File-mode's equivalent of _on_message_text_changed() --
        reads the chosen file's raw bytes ONCE, here, into node.
        file_bytes (NOT re-read from disk at build/Start time), so a
        tag keeps transmitting the file it was shown even if the
        original file on disk later changes or moves. file_name is
        kept purely for display and as the default name later offered
        back when SAVING a reassembled copy (see poll_packets()'s
        "Save as file..." row action) -- message_framer never sees it,
        only the bytes themselves."""
        if self.current_node is None:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self.window, "Choose a file to transmit")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            QtWidgets.QMessageBox.warning(self.window, "Couldn't read file", str(e))
            return
        self.current_node.file_bytes = data
        self.current_node.file_name = os.path.basename(path)
        self._update_file_info_label()
        self._update_message_info_label()
        self._update_energy_info_label()
        self._update_packets_per_wake_info(self.current_node)

    def _on_chunk_bytes_changed(self, value):
        if self.current_node is None:
            return
        self.current_node.chunk_bytes = value
        self._update_message_info_label()
        self._update_energy_info_label()
        self._update_packets_per_wake_info(self.current_node)

    def _on_packets_per_wake_changed(self, value):
        if self.current_node is None:
            return
        self.current_node.packets_per_wake = value
        self._update_packets_per_wake_info(self.current_node)

    def _update_file_info_label(self):
        """Live summary of the currently-chosen file (name + size), same
        spot message_info_label occupies for text mode's fragment
        count -- so a tag with no file chosen yet doesn't read as
        broken/empty, just not-yet-configured."""
        node = self.current_node
        if node is None or node.payload_mode != "file":
            return
        if not node.file_name:
            self.file_info_label.setText("No file chosen yet.")
        else:
            self.file_info_label.setText(f"{node.file_name}  ({len(node.file_bytes)} byte(s))")

    def _update_message_info_label(self):
        """Live summary of what message/file + chunk-size resolves to
        (fragment count, bits/fragment) -- without this, a too-many-
        fragments error only surfaces as a ValueError at Start. Shared
        by text/file mode. For tag_ook/tag_bpsk, also shows the longest
        identical-bit run (DC-blocker droop, whitening-fix-findings.md)
        -- skipped for tag_fm0/tag_miller, whose self-clocking line
        coding never produces a long enough run to matter."""
        node = self.current_node
        if node is None or node.payload_mode not in ("text", "file"):
            return
        try:
            if node.payload_mode == "file":
                fragments = message_framer.build_fragments_from_bytes(
                    node.file_bytes, 0, node.chunk_bytes)
            else:
                fragments = message_framer.build_fragments(node.message_text, 0, node.chunk_bytes)
        except ValueError as e:
            self.message_info_label.setText(str(e))
            return
        chunk_bits = node.chunk_bytes * 8
        frag_bits = message_framer.fragment_len_bits(node.chunk_bytes)
        header_bits = message_framer.HEADER_BYTES * 8
        # Kept to ONE short line on purpose -- this used to spell out
        # byte/fragment/bit counts in a full sentence, which ate too
        # much vertical space in the sidebar (especially with the
        # decoder section open below it). The "why" (auto-sets payload
        # length) now lives in this label's tooltip instead.
        #
        # Shows chunk_bits (YOUR payload, chunk_bytes*8) here, not
        # frag_bits (chunk + the simulator's HEADER_BYTES header) --
        # frag_bits is what actually goes out over the air and is what
        # the receiver's payload_len_bits gets auto-set to, but it's
        # not a number you chose, so it doesn't belong in the one line
        # you see as "your" fragment size. The full on-wire breakdown
        # is in this label's tooltip instead, same as the "why".
        text = f"{len(fragments)} fragment(s), {chunk_bits} bit(s) payload each"
        tooltip = (
            "Sets this tag's receiver's payload length automatically.\n\n"
            f"On the wire each fragment is {frag_bits} bit(s): your {chunk_bits}-bit "
            f"payload plus a {header_bits}-bit header the simulation adds (tag/"
            f"fragment identity + checksum) to track messages across fragments and "
            f"tags -- not something you typed, and not shown as part of \"your\" "
            f"number above."
        )

        if node.type_name in ("tag_ook", "tag_bpsk"):
            rx = self.window._find_downstream_receiver(node)
            preamble_str = (rx.decoder_params.get("preamble")
                             if rx is not None and rx.decoder_params else None)
            preamble_bits = [int(b) for b in preamble_str] if preamble_str else PREAMBLE_BITS
            worst_bits = _worst_bit_run(fragments, preamble_bits)
            samp_rate = node.params.get("samp_rate", SAMP_RATE)
            bit_rate = node.params.get("bit_rate") or 1
            worst_samples = worst_bits * (samp_rate / bit_rate)
            text += f"  |  worst run: {worst_bits} bit(s)"
            tooltip += (
                f"\n\nLongest run of identical bits this content puts on the wire: "
                f"{worst_bits} bit(s), ~{int(worst_samples)} samples at this tag's own "
                f"bit_rate/samp_rate. OOK/BPSK's receiver has a DC blocker ahead of the "
                f"slicer (dc_len, in samples) that droops/fails to decode on a run "
                f"longer than its own length, independent of TX power -- if you see "
                f"that, try setting the receiver's dc_len to at least "
                f"~{int(worst_samples * 1.5)}."
            )
        self.message_info_label.setText(text)
        self.message_info_label.setToolTip(tooltip)

    def _update_preamble_info_label(self, tag_item):
        """Shows which preamble this tag actually transmits, resolved
        from its receiver, without a second editable copy here."""
        rx = self.window._find_downstream_receiver(tag_item)
        if rx is None:
            self.preamble_info_label.setText("Preamble: not wired yet.")
            return
        preamble = rx.decoder_params.get("preamble") if rx.decoder_params else None
        # Kept to ONE short line on purpose -- this used to be a full
        # explanatory paragraph, which ate too much vertical space in
        # the sidebar (especially with the decoder section open below
        # it). The "why can't I edit it here" explanation now lives in
        # this label's tooltip instead of its always-visible text.
        self.preamble_info_label.setText(f"Preamble: {preamble}  (set on '{rx.node_id}')")
        self.preamble_info_label.setToolTip(
            "Set on the receiver's Decoder settings, not here -- editing it "
            "there updates every tag feeding that receiver together, so it "
            "can't desync tag vs. receiver."
        )

    def _update_packets_per_wake_info(self, tag_item):
        """Recomputes tag_item's derived packet_bits from its readable
        'Packets to Send' repeat count and writes the result straight
        into tag_item.params["packet_bits"] -- same "Tier 2 writes into
        the Tier 1 param dict" pattern CanvasWindow._sync_decoder_params()
        already uses for a receiver's payload_len_bits. packet_bits
        itself is never hand-edited any more (see show_node()'s
        packet_bits-disabling branch in the generic params loop) --
        this is the one place that sets it.

        Resolves the preamble length the same way _update_preamble_
        info_label() does just above (walk forward to the governing
        receiver's decoder_params, falling back to the simulator's own
        default PREAMBLE if this tag isn't wired to a receiver yet), so
        the packet_bits this computes always matches what plan_
        topology() will actually put on the wire for THIS tag.

        Safe to call both mid-show_node() -- before the generic params
        loop builds the packet_bits widget, where self.param_widgets is
        already the fresh, empty dict (see _clear_params_form(), called
        at the very top of show_node()), so this only writes into
        tag_item.params there -- and from a live edit handler after the
        panel is already built, where self.param_widgets["packet_bits"]
        is the real on-screen widget and gets updated in place so it
        doesn't go stale without a full show_node() rebuild."""
        if tag_item is None or tag_item.packets_per_wake is None:
            return
        rx = self.window._find_downstream_receiver(tag_item)
        preamble_str = (rx.decoder_params.get("preamble")
                         if rx is not None and rx.decoder_params else None)
        preamble_len = len(preamble_str) if preamble_str else len(PREAMBLE)
        cycle_bits = tag_item.full_cycle_bits(preamble_len)
        packet_bits = max(1, tag_item.packets_per_wake * cycle_bits)
        tag_item.params["packet_bits"] = packet_bits

        self.packets_per_wake_spin.blockSignals(True)
        self.packets_per_wake_spin.setValue(tag_item.packets_per_wake)
        self.packets_per_wake_spin.blockSignals(False)
        self.packets_per_wake_label.setToolTip(
            f"One full on-air cycle (preamble + payload, repeated per fragment "
            f"in Text/File mode) is {cycle_bits} bit(s) at this tag's own "
            f"bit_rate -- {tag_item.packets_per_wake} x that = {packet_bits} "
            f"raw bit(s), written into 'Number of Packet Bits' below."
        )

        widget = self.param_widgets.get("packet_bits")
        if widget is not None:
            widget.blockSignals(True)
            widget.setValue(packet_bits)
            widget.blockSignals(False)

    def _update_energy_mode_visibility(self, mode):
        """Shows only the fields the active switch_mode actually uses:
        FET mode needs C_gate, BJT mode needs R_bias + duty_cycle; Vdd
        is shared by both so it's never hidden."""
        is_fet = (mode == "fet")
        self.energy_c_gate_label.setVisible(is_fet)
        self.energy_c_gate_spin.setVisible(is_fet)
        self.energy_r_bias_label.setVisible(not is_fet)
        self.energy_r_bias_spin.setVisible(not is_fet)
        self.energy_duty_cycle_label.setVisible(not is_fet)
        self.energy_duty_cycle_spin.setVisible(not is_fet)

    def _on_energy_mode_changed(self, index):
        if self.current_node is not None:
            mode = self.energy_mode_combo.itemData(index)
            self.current_node.energy_switch_mode = mode
            self._update_energy_mode_visibility(mode)
            self._update_energy_info_label()

    def _on_energy_vdd_changed(self, value):
        if self.current_node is not None:
            self.current_node.energy_vdd = value
            self._update_energy_info_label()

    def _on_energy_c_gate_changed(self, value):
        if self.current_node is not None:
            self.current_node.energy_c_gate_fF = value
            self._update_energy_info_label()

    def _on_energy_r_bias_changed(self, value):
        if self.current_node is not None:
            self.current_node.energy_r_bias_ohm = value
            self._update_energy_info_label()

    def _on_energy_duty_cycle_changed(self, value):
        if self.current_node is not None:
            self.current_node.energy_duty_cycle = value
            self._update_energy_info_label()

    def _on_energy_static_rate_changed(self, value):
        if self.current_node is not None:
            self.current_node.energy_static_rate = value
            self._update_energy_info_label()

    def refresh_energy_info(self, node_item):
        """Keeps the energy-estimate label live while dragging a tag or
        its sender -- distance feeds the harvested-power figure."""
        if self.current_node is node_item:
            self._update_energy_info_label()

    def _update_energy_info_label(self):
        """Recomputes and displays four rows for the current tag, kept
        as separate figures rather than one combined number:
          1. Transmission time: duration = N bits / bit_rate (pure
             arithmetic, not a live clock -- shown first so it doesn't
             read as a running timer).
          2. Consumed by switching: a real per-bit joule figure from
             _switch_energy_per_bit_joules(), under whichever switch_
             mode ("fet"/"bjt") is selected, times N bits (N =
             node.total_frame_bits(), the full payload cycle, not just
             one fragment). Unlike before, this is no longer a free-
             typed constant -- see that function's docstring for why
             FET and BJT need genuinely different formula shapes, not
             just different numbers plugged into the same one.
          3. Consumed by staying active: energy_static_rate * duration
             (defaults to 0, opt-in) -- UNCHANGED, still illustrative/
             uncalibrated "units", not joules.
          4. Harvested: _harvested_power() at this tag's current
             forward-edge distance over the same duration; 0 if below
             the tag's p_th, matching harvesting_gate's real gate.
        Each row is a bold value label + a small grey calculation label
        (self._energy_result_rows)."""
        node = self.current_node
        if node is None or node.energy_switch_mode is None:
            return
        bit_rate = node.params.get("bit_rate")
        n_bits = node.total_frame_bits()
        if not bit_rate or n_bits <= 0:
            self.energy_time_value_label.setText("Energy estimate unavailable")
            self.energy_time_desc_label.setText(
                "Requires a bit_rate and a non-empty payload/message")
            for value_label, desc_label in self._energy_result_rows[1:]:
                value_label.setText("")
                desc_label.setText("")
            return
        duration = n_bits / float(bit_rate)

        # Row 1: transmission duration.
        self.energy_time_value_label.setText(f"Transmission time: {duration:.3g}s")
        self.energy_time_desc_label.setText(
            f"{n_bits} bit(s) / {bit_rate:.3g} bit/s")

        # Row 2: consumed by switching -- the PRIMARY figure. Real
        # joules now, derived from the selected switch-device model;
        # node.energy_per_bit is kept in sync purely so Save/Load and
        # Copy/Paste have a last-known value to persist (the mode +
        # its own params are the actual source of truth).
        switch_e_per_bit = _switch_energy_per_bit_joules(
            node.energy_switch_mode, node.energy_vdd,
            c_gate_f=(node.energy_c_gate_fF * 1e-15
                      if node.energy_c_gate_fF is not None else None),
            r_bias_ohm=node.energy_r_bias_ohm,
            duty_cycle=node.energy_duty_cycle,
            bit_rate=bit_rate,
        )
        if switch_e_per_bit is None:
            self.energy_switch_value_label.setText("Consumed (switching): --")
            self.energy_switch_desc_label.setText(
                "Missing/invalid switch-model parameter for the selected mode")
        else:
            node.energy_per_bit = switch_e_per_bit
            mode_label = "FET/CMOS" if node.energy_switch_mode == "fet" else "BJT"
            self.energy_switch_value_label.setText(
                f"Consumed (switching): {_format_energy_joules(switch_e_per_bit * n_bits)}")
            self.energy_switch_desc_label.setText(
                f"{n_bits} bit(s) x {_format_energy_joules(switch_e_per_bit)}/bit ({mode_label})")

        # Row 3: consumed by staying active -- secondary, opt-in.
        self.energy_static_value_label.setText(
            f"Consumed (staying active): {node.energy_static_rate * duration:.3g} units")
        self.energy_static_desc_label.setText(
            f"{duration:.3g}s x {node.energy_static_rate:.3g}/s")

        # Row 4: harvested -- tertiary, gated by this tag's own p_th
        # exactly like harvesting_gate's real check.
        fwd_edge = next((e for e in self.window.edges
                          if e.role == "forward" and e.to_node is node), None)
        if fwd_edge is None or fwd_edge.channel_params is None:
            self.energy_harvest_value_label.setText("Harvested: --")
            self.energy_harvest_desc_label.setText("No forward edge wired")
        else:
            sender = fwd_edge.from_node
            tx_power_send = sender.params.get("tx_power_send")
            freq = fwd_edge.channel_params.get("freq")
            g_reader_dbi = fwd_edge.channel_params.get("g_reader_dbi")
            g_tag_dbi = fwd_edge.channel_params.get("g_tag_dbi")
            distance = fwd_edge.channel_params.get("distance")
            p_th = node.params.get("p_th")
            if tx_power_send is None or not freq or distance is None:
                self.energy_harvest_value_label.setText("Harvested: --")
                self.energy_harvest_desc_label.setText("Missing sender/channel settings")
            else:
                power = _harvested_power(
                    float(tx_power_send), float(distance), float(freq),
                    float(g_reader_dbi), float(g_tag_dbi))
                if p_th is not None and power < float(p_th):
                    self.energy_harvest_value_label.setText("Harvested: 0 units")
                    self.energy_harvest_desc_label.setText(
                        f"Below Harvesting Threshold at {distance:.3g}m")
                else:
                    self.energy_harvest_value_label.setText(
                        f"Harvested: {power * duration:.3g} units")
                    self.energy_harvest_desc_label.setText(
                        f"{duration:.3g}s at {distance:.3g}m")

        switch_tooltip = (
            "Real joules, from the selected FET/CMOS or BJT switch-device "
            "formula -- accurate only as far as the C_gate/Vdd/R_bias/"
            "duty_cycle values above match real hardware; defaults are "
            "order-of-magnitude or thesis-cited, not a universal constant. "
            "Reported as a separate figure, not folded into a combined total."
        )
        other_tooltip = (
            "Illustrative, uncalibrated relative units. Reported as separate "
            "figures rather than a combined total."
        )
        self.energy_switch_value_label.setToolTip(switch_tooltip)
        self.energy_switch_desc_label.setToolTip(switch_tooltip)
        for value_label, desc_label in self._energy_result_rows:
            if value_label is self.energy_switch_value_label:
                continue
            value_label.setToolTip(other_tooltip)
            desc_label.setToolTip(other_tooltip)

    def _on_id_changed(self):
        if self.current_node is None:
            return
        new_id = self.id_edit.text().strip()
        old_id = self.current_node.node_id
        if new_id == old_id or not new_id:
            self.id_edit.setText(old_id)
            return
        if new_id in self.window.nodes:
            QtWidgets.QMessageBox.warning(self.window, "Duplicate ID",
                                           f"Node id '{new_id}' is already used.")
            self.id_edit.setText(old_id)
            return
        del self.window.nodes[old_id]
        self.window.nodes[new_id] = self.current_node
        self.current_node.node_id = new_id
        self.current_node.label.setPlainText(f"{self.current_node.type_name}\n{new_id}")


class CanvasWindow(QtWidgets.QMainWindow):
    # Pixel offset applied to each paste, in BOTH x and y, so repeated
    # Ctrl+V presses cascade diagonally across the canvas instead of
    # stacking every copy exactly on top of the last one.
    PASTE_OFFSET = 40

    # How often poll_packets() drains the backend and refreshes the
    # decoder tables, in ms. Was 150 -- raised to 400 because a busy
    # decoder draining (and repainting a QTableWidget) that often is
    # exactly what was making tab-switching freeze: see poll_packets()'s
    # own docstring for the rest of the fix (batched inserts + a row cap).
    DECODER_POLL_INTERVAL_MS = 400

    # Each receiver's table keeps only its most recent rows -- an
    # unbounded table is cheap to insert into but expensive to lay out
    # and repaint, and THAT cost is what shows up as a freeze the moment
    # you switch to (or resize) a tab holding a few thousand rows.
    # Oldest rows beyond this are dropped; nothing downstream reads the
    # table back out, so trimming it is safe.
    MAX_DECODER_ROWS = 500

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Backscatter Canvas (experimental)")
        self.mw = None  # the running TopologyMiddleware, or None while stopped
        # The one open SignalViewWindow (holding every ticked port's
        # probe as a stacked row), or None while none is open -- see
        # _open_pending_signal_views() and on_stop()'s force-close-
        # before-mw.stop() ordering.
        self._signal_view_window = None
        self._node_counter = defaultdict(int)
        self._next_order_seq = 0  # see NodeItem.order_seq's own comment
        self.nodes = {}   # node_id -> NodeItem
        self.edges = []   # list of EdgeItem
        # Every QGraphicsEllipseItem currently drawn by _update_wake_
        # ranges() (the wake-up-range rings) -- tracked separately from
        # self.edges/self.nodes since these aren't backend state, just
        # this method's own on-canvas drawing, cleared and rebuilt from
        # scratch on every call.
        self.wake_range_items = []
        # Decoder settings now live on each rx_* NodeItem itself
        # (.decoder_params) -- see NodeItem's comment. No global dict
        # here any more.

        # copy_selected()/paste_clipboard()'s clipboard -- a plain dict
        # (not a NodeItem/EdgeItem reference) so pasting doesn't share
        # any live state with the node it was copied from. None until
        # the first Copy. See copy_selected()'s own docstring for what's
        # captured.
        self._clipboard = None

        # QSplitter (not a plain QHBoxLayout) so every pane boundary
        # gets a draggable handle, letting the right panel resize and
        # the canvas shrink/grow with it. QSplitter IS a QWidget, so it
        # can be the central widget directly.
        self.main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.setCentralWidget(self.main_splitter)

        palette_box = QtWidgets.QGroupBox("Node palette (drag onto canvas)")
        palette_layout = QtWidgets.QVBoxLayout(palette_box)
        self.palette_list = NodePaletteList()
        palette_layout.addWidget(self.palette_list)
        self.main_splitter.addWidget(palette_box)

        # A splitter pane has to be an actual widget, not a bare layout
        # -- wrapped in center_widget purely to give the toolbar+canvas
        # column a widget identity.
        center_widget = QtWidgets.QWidget()
        center_col = QtWidgets.QVBoxLayout(center_widget)

        toolbar = QtWidgets.QHBoxLayout()
        self.validate_button = QtWidgets.QPushButton("Validate")
        self.start_button = QtWidgets.QPushButton("Start")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.autowire_button = QtWidgets.QPushButton("Auto-wire selected")
        self.autowire_button.setToolTip(
            "Select a sender and/or a receiver PLUS the tags to wire them to (rubber-"
            "band select, or ctrl/shift-click), then click this -- wires every selected "
            "tag to the selected sender's Carrier_Out and/or the selected receiver's "
            "Antenna_In in one go, instead of dragging each wire by hand. Safe to click "
            "again: it never creates the same connection twice."
        )
        self.copy_button = QtWidgets.QPushButton("Copy")
        self.copy_button.setToolTip(
            "Select exactly one node and click this (or Ctrl+C) to copy it. For a tag "
            "that's already wired to a sender and/or receiver, Paste also recreates "
            "that same wiring on the new copy -- wire one tag, then Copy/Paste it "
            "repeatedly instead of wiring each one by hand."
        )
        self.paste_button = QtWidgets.QPushButton("Paste")
        self.paste_button.setToolTip("Paste the last copied node (or Ctrl+V), offset "
                                      "slightly so repeated pastes don't stack exactly on top of each other.")
        self.clear_button = QtWidgets.QPushButton("Clear canvas")
        self.save_button = QtWidgets.QPushButton("Save...")
        self.load_button = QtWidgets.QPushButton("Load...")
        self.save_default_button = QtWidgets.QPushButton("Save as Default")
        self.stop_button.setEnabled(False)
        for b in (self.validate_button, self.start_button, self.stop_button, self.autowire_button,
                  self.copy_button, self.paste_button,
                  self.clear_button, self.save_button, self.load_button, self.save_default_button):
            toolbar.addWidget(b)
        toolbar.addStretch(1)
        center_col.addLayout(toolbar)

        self.view = CanvasView(self)
        # Floor so the splitter handle can't shrink the canvas to
        # nothing, while still leaving plenty of room to shrink.
        self.view.setMinimumWidth(200)
        center_col.addWidget(self.view, 1)

        self.main_splitter.addWidget(center_widget)

        # Decoded-packet display: one QDockWidget per receiver (see
        # _rebuild_decoder_docks()). Built empty; populated from
        # on_start() once the run's receivers are known.
        self.decoder_docks = {}    # decoder_id -> QDockWidget
        self.decoder_tables = {}   # decoder_id -> QTableWidget (fragments)
        self.message_tables = {}  # decoder_id -> QTableWidget (reassembled
                                   # messages -- see message_framer.py)
        self.decoder_ber_stats = {}   # decoder_id -> {"errors", "bits"}
        self.decoder_ber_labels = {}  # decoder_id -> QLabel (cumulative BER)
        self.decoder_preambles = {}   # decoder_id -> synced preamble string
        # rx node_id -> [{"snr_db", "ber", "n_wrong", "n_bits", "tag_id"}, ...]
        # -- "Capture point"'s running BER-vs-SNR measurements for that
        # receiver. Deliberately NOT reset by _rebuild_decoder_docks()
        # (unlike decoder_ber_stats, which IS a per-run figure) -- the
        # whole point is that it survives Start -> Stop -> change
        # distance -> Start again, so each click adds one more point to
        # the SAME growing plot instead of losing prior runs. Cleared
        # only by on_clear() (a fresh canvas). See _capture_ber_snr_
        # point()/_show_ber_vs_snr_plot().
        self.ber_vs_snr_points = defaultdict(list)
        self.setDockNestingEnabled(True)

        # Right panel is just the one "Block" tab. Decoder settings and
        # a channel's own settings both show inline in this same
        # panel (NodeInspector.show_node()/show_edge()) rather than
        # separate tabs.
        self.inspector = NodeInspector(self)
        self.right_tabs = QtWidgets.QTabWidget()
        self.right_tabs.addTab(self.inspector, "Block")
        self.main_splitter.addWidget(self.right_tabs)

        # Stretch factors: whole-window resize grows/shrinks the canvas
        # pane (1) first; palette (0) and inspector (2) stay at whatever
        # width their handle was last dragged to. Handles stay freely
        # draggable down to each pane's own minimumWidth floor.
        # No explicit setSizes() here: before the window is shown, the
        # splitter has no real geometry and a pixel-size call is a
        # silent no-op (confirmed directly) -- the stretch factors +
        # each pane's sizeHint/minimumWidth already produce a
        # reasonable starting split once shown.
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)

        self.view.scene().selectionChanged.connect(self.on_selection_changed)

        self.validate_button.clicked.connect(self.on_validate)
        self.start_button.clicked.connect(self.on_start)
        self.stop_button.clicked.connect(self.on_stop)
        self.autowire_button.clicked.connect(self.auto_wire_selected)
        self.copy_button.clicked.connect(self.copy_selected)
        self.paste_button.clicked.connect(self.paste_clipboard)
        self.clear_button.clicked.connect(self.on_clear)
        self.save_button.clicked.connect(self.on_save_as)
        self.load_button.clicked.connect(self.on_load)
        self.save_default_button.clicked.connect(self.on_save_as_default)

        self.timer = QtCore.QTimer()
        self.timer.setInterval(self.DECODER_POLL_INTERVAL_MS)
        self.timer.timeout.connect(self.poll_packets)

        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)

        # "set a default config to start the UI so i can test fast" --
        # auto-load whatever was last saved as the default layout
        # (canvas_default_layout.json, next to this file) instead of
        # opening to an empty canvas every time. A shipped default
        # (2-tag BPSK, the modulation that's actually been run
        # end-to-end) comes with this file; "Save as Default" lets you
        # replace it with whatever you'd rather start from.
        if os.path.exists(DEFAULT_LAYOUT_PATH):
            try:
                with open(DEFAULT_LAYOUT_PATH) as f:
                    self._load_layout_dict(json.load(f))
                self.status.showMessage(f"Loaded default layout from {DEFAULT_LAYOUT_PATH}", 4000)
            except Exception as e:
                self.status.showMessage(f"Couldn't load default layout: {e}", 6000)

    # --- model mutation, called by CanvasView on drop / finished edge drag ---

    def add_node(self, type_name, scene_pos, node_id=None, params=None, payload=None,
                 decoder_params=None, payload_mode=None, message_text=None, chunk_bytes=None,
                 file_bytes=None, file_name=None, order_seq=None, packets_per_wake=None,
                 energy_per_bit=None, energy_static_rate=None,
                 energy_switch_mode=None, energy_c_gate_fF=None, energy_vdd=None,
                 energy_r_bias_ohm=None, energy_duty_cycle=None,
                 decode_snr_threshold_db=None,
                 add_to_scene=True, icon_type_name=None):
        """add_to_scene=False registers the NodeItem in self.nodes
        without scene().addItem() -- used only for a reader's hidden rx
        half, which never draws or is clickable. icon_type_name passes
        through to NodeItem (None = look up by own type_name);
        add_reader() and _load_layout_dict() pass "reader" for a
        reader's visible sender. node_id/params/.../file_name let
        _load_layout_dict() recreate a saved node exactly, left at
        NodeItem's defaults if missing (older layout). order_seq: None
        allocates fresh; _convert_node_modulation() passes the old
        item's value through so a modulation switch doesn't look like a
        new tag for tag_id purposes. packets_per_wake: the readable
        repeat count packet_bits is derived from (see NodeItem.
        packets_per_wake/full_cycle_bits()) -- params' own "packet_bits"
        key, if passed, is always overwritten by the fresh derivation
        below rather than kept as a raw hand-set number."""
        if node_id is None:
            self._node_counter[type_name] += 1
            node_id = f"{type_name}{self._node_counter[type_name]}"
            while node_id in self.nodes:
                self._node_counter[type_name] += 1
                node_id = f"{type_name}{self._node_counter[type_name]}"
        item = NodeItem(node_id, type_name, self, scene_pos, icon_type_name=icon_type_name)
        if order_seq is not None:
            item.order_seq = order_seq
            self._next_order_seq = max(self._next_order_seq, order_seq + 1)
        else:
            item.order_seq = self._next_order_seq
            self._next_order_seq += 1
        if params:
            item.params.update(params)
        if payload is not None:
            item.payload = list(payload)
        if decoder_params and item.decoder_params is not None:
            item.decoder_params.update(decoder_params)
        if payload_mode is not None and item.payload_mode is not None:
            item.payload_mode = payload_mode
        if message_text is not None and item.message_text is not None:
            item.message_text = message_text
        if chunk_bytes is not None and item.chunk_bytes is not None:
            item.chunk_bytes = chunk_bytes
        if file_bytes is not None and item.file_bytes is not None:
            item.file_bytes = file_bytes
        if file_name is not None and item.file_name is not None:
            item.file_name = file_name
        if packets_per_wake is not None and item.packets_per_wake is not None:
            # Explicit intent -- a fresh node whose caller actually
            # knows a packets_per_wake value (paste_clipboard(),
            # _convert_node_modulation(), or a layout saved AFTER this
            # feature existed). Recompute packet_bits now that payload/
            # payload_mode/message_text/chunk_bytes/file_bytes above are
            # all set on `item` -- mirrors NodeItem.__init__'s own
            # initial derivation, but against THIS node's actual loaded/
            # pasted/converted content rather than a brand-new tag's
            # defaults. No receiver is wired yet at node-creation time
            # (this runs before the edges loop in both _load_layout_
            # dict() and paste_clipboard()), so the preamble length
            # falls back to the simulator's own default PREAMBLE --
            # correct for the overwhelming common case (every receiver's
            # own preamble itself defaults to PREAMBLE; only an explicit
            # per-receiver edit would disagree), and NodeInspector.
            # _update_packets_per_wake_info() recomputes again against
            # the REAL wired preamble the first time this tag's
            # inspector panel is shown or edited.
            item.packets_per_wake = packets_per_wake
            item.params["packet_bits"] = item.packets_per_wake * item.full_cycle_bits(len(PREAMBLE))
        elif item.packets_per_wake is not None and params and "packet_bits" in params:
            # No packets_per_wake given, but a raw packet_bits WAS --
            # an old layout saved before this feature existed (or any
            # other caller handing add_node() a literal params dict).
            # That number is a deliberately-tuned burst duration (the
            # whole reason this feature exists at all -- see this
            # window's "packet_bits is like smth around 10000" ask), so
            # it must NOT be silently clobbered down to the brand-new-
            # tag default (3) just because this concept didn't exist
            # when it was saved. Leave packet_bits exactly as already
            # loaded (item.params.update(params) above already applied
            # it) and back-derive a readable repeat count FROM it
            # instead, purely so "Packets to Send" in the inspector
            # shows something consistent with what's actually configured.
            cycle_bits = item.full_cycle_bits(len(PREAMBLE)) or 1
            item.packets_per_wake = max(1, round(params["packet_bits"] / cycle_bits))
        # Else: brand new tag, nothing passed at all -- NodeItem.
        # __init__ already derived packet_bits from its own fresh-tag
        # default (3) before add_node() was ever called; nothing left
        # to do here.
        if energy_per_bit is not None and item.energy_per_bit is not None:
            item.energy_per_bit = energy_per_bit
        if energy_static_rate is not None and item.energy_static_rate is not None:
            item.energy_static_rate = energy_static_rate
        if energy_switch_mode is not None and item.energy_switch_mode is not None:
            item.energy_switch_mode = energy_switch_mode
        if energy_c_gate_fF is not None and item.energy_c_gate_fF is not None:
            item.energy_c_gate_fF = energy_c_gate_fF
        if energy_vdd is not None and item.energy_vdd is not None:
            item.energy_vdd = energy_vdd
        if energy_r_bias_ohm is not None and item.energy_r_bias_ohm is not None:
            item.energy_r_bias_ohm = energy_r_bias_ohm
        if energy_duty_cycle is not None and item.energy_duty_cycle is not None:
            item.energy_duty_cycle = energy_duty_cycle
        # type_name-gated, not "item.X is not None" like the energy_*
        # fields above -- every NodeItem already has a non-None default
        # (DECODE_RANGE_SNR_THRESHOLD_DB) regardless of type, so that
        # guard wouldn't actually restrict this to receivers. Gated on
        # rx_* (standalone receiver OR a reader's hidden rx half, both
        # of which use that prefix), not "sender" -- decode_snr_
        # threshold_db moved to being receiver-owned (see NodeItem.
        # __init__'s show_decode_range comment for why).
        if decode_snr_threshold_db is not None and type_name.startswith("rx_"):
            item.decode_snr_threshold_db = decode_snr_threshold_db
        if add_to_scene:
            self.view.scene().addItem(item)
        self.nodes[node_id] = item
        self.status.showMessage(f"Added {type_name} as '{node_id}'", 3000)
        return item

    READER_DEFAULT_RX_TYPE = "rx_bpsk"

    def add_reader(self, scene_pos, rx_type_name=None):
        """Places a combined "reader" as one visible block with one
        connectable port. On canvas: an ordinary 'sender' NodeItem (one
        real port, Carrier_Out) id "reader1"... Its reader_partner is a
        real rx_* NodeItem created with add_to_scene=False (never draws
        or clickable) -- no second box to keep in sync.

        Wiring the visible output to a tag is the real forward leg;
        _wire_reader_shadow_return() (add_edge()) auto-creates the
        matching tag->rx return edge as an invisible shadow, mirroring
        channel_params so both legs share one distance, and greys out
        the tag's output port so it can't be hand-wired a second time.

        NodeInspector shows the hidden rx's params (si_enable/
        si_isolation_db included, unlike a standalone rx) as "Receiver
        Settings (this reader)". si_enable is forced back on here since
        NODE_SEED_OVERRIDES defaults a fresh standalone rx to 0.
        icon_type_name="reader" gives the visible half its own icon."""
        rx_type_name = rx_type_name or self.READER_DEFAULT_RX_TYPE
        self._node_counter["reader"] += 1
        reader_id = f"reader{self._node_counter['reader']}"
        while reader_id in self.nodes:
            self._node_counter["reader"] += 1
            reader_id = f"reader{self._node_counter['reader']}"
        sender_item = self.add_node(
            "sender", scene_pos, node_id=reader_id, icon_type_name="reader")
        rx_item = self.add_node(
            rx_type_name, scene_pos, node_id=f"{reader_id}_rx", add_to_scene=False)
        rx_item.params["si_enable"] = 1
        rx_item.reader_partner = sender_item
        sender_item.reader_partner = rx_item
        self.status.showMessage(
            f"Added combined reader '{reader_id}'. Wire its one output straight to a tag's "
            "Carrier_In to connect both the forward and return legs at once.",
            6000,
        )
        return sender_item, rx_item

    def add_edge(self, from_node, from_port_name, to_node, to_port_name):
        if from_node is to_node:
            self.status.showMessage("Can't connect a node to itself.", 4000)
            return None
        edge = EdgeItem(from_node, from_port_name, to_node, to_port_name)
        self.view.scene().addItem(edge)
        self.edges.append(edge)
        self._update_wake_ranges()
        if edge.role == "forward" and from_node.reader_partner is not None:
            # from_node is a reader's visible sender; wire the hidden
            # return leg FIRST, before the modulation auto-match below
            # -- that check needs _find_upstream_tags(rx_item) to see
            # this tag via the shadow edge, or it'll count 0, not 1.
            # Safe even if auto-match then rebuilds the rx:
            # _convert_node_modulation() re-derives a fresh shadow for
            # whichever forward edge feeds the rebuilt rx, and `edge`
            # itself survives (neither endpoint is the rx).
            self._wire_reader_shadow_return(edge)
            self._maybe_auto_match_reader_modulation(from_node.reader_partner, to_node)
        return edge

    def _wire_reader_shadow_return(self, forward_edge):
        """Auto-creates the matching tag->rx return edge once a
        reader's visible output is wired into a tag: a normal EdgeItem
        (role="return"), appended to self.edges so scene_to_topology()
        builds its channel normally, but never scene().addItem()'d --
        only the one forward arrow the user drew appears on screen.
        Also greys out the tag's output port so it can't be re-wired
        elsewhere while claimed by a reader (the port is greyed, not
        the edge -- the visible forward edge stays normal-colored)."""
        rx = forward_edge.from_node.reader_partner
        tag = forward_edge.to_node
        if not tag.out_ports or not rx.in_ports:
            return
        out_port, in_port = tag.out_ports[0].name, rx.in_ports[0].name
        if self._edge_exists(tag, out_port, rx, in_port):
            return
        shadow = EdgeItem(tag, out_port, rx, in_port)
        shadow.is_reader_shadow = True
        self.edges.append(shadow)  # deliberately never scene().addItem()'d -- see docstring
        forward_edge.reader_shadow = shadow
        if tag.out_ports:
            out_handle = tag.port_handles.get(("out", tag.out_ports[0].name))
            if out_handle is not None:
                out_handle.set_auto_wired(True)
        self._update_wake_ranges()

    def _maybe_auto_match_reader_modulation(self, rx_item, tag_item):
        """Wiring the FIRST tag into a reader's rx auto-switches that
        rx's modulation to match. A one-time nudge, not a standing link
        -- a second, differently-modulated tag wired in later does NOT
        re-switch it. Returns True iff it actually switched."""
        if len(self._find_upstream_tags(rx_item)) != 1:
            return False
        tag_modulation = TYPE_TO_MODULATION.get(tag_item.type_name)
        rx_modulation = TYPE_TO_MODULATION.get(rx_item.type_name)
        if tag_modulation and tag_modulation != rx_modulation:
            self._convert_node_modulation(rx_item, tag_modulation)
            return True
        return False

    def _edge_exists(self, from_node, from_port_name, to_node, to_port_name):
        return any(e.from_node is from_node and e.from_port_name == from_port_name
                   and e.to_node is to_node and e.to_port_name == to_port_name
                   for e in self.edges)

    def auto_wire_selected(self):
        """Select a sender and/or receiver plus the tags to connect
        them to, click the toolbar button, and every selected tag gets
        wired to both in one pass -- same edges as dragging each by
        hand, just without the dragging."""
        selected_nodes = [i for i in self.view.scene().selectedItems() if isinstance(i, NodeItem)]
        senders = [n for n in selected_nodes if n.type_name == "sender"]
        receivers = [n for n in selected_nodes if n.type_name.startswith("rx_")]
        tags = [n for n in selected_nodes if n.type_name.startswith("tag_")]

        if len(senders) > 1 or len(receivers) > 1:
            self.status.showMessage(
                "Select at most one sender and one receiver (plus the tags) to auto-wire.", 5000)
            return
        if not tags or (not senders and not receivers):
            self.status.showMessage(
                "Select a sender and/or a receiver, plus the tag(s) to wire them to, then "
                "click Auto-wire selected.", 5000)
            return

        created = 0
        if senders and senders[0].out_ports:
            sender = senders[0]
            out_port = sender.out_ports[0].name
            for tag in tags:
                if not tag.in_ports:
                    continue
                in_port = tag.in_ports[0].name
                if self._edge_exists(sender, out_port, tag, in_port):
                    continue
                self.add_edge(sender, out_port, tag, in_port)
                created += 1
        if receivers and receivers[0].in_ports:
            rx = receivers[0]
            in_port = rx.in_ports[0].name
            for tag in tags:
                if not tag.out_ports:
                    continue
                out_port = tag.out_ports[0].name
                if self._edge_exists(tag, out_port, rx, in_port):
                    continue
                self.add_edge(tag, out_port, rx, in_port)
                created += 1

        if created:
            self.status.showMessage(f"Auto-wired {created} connection(s).", 4000)
        else:
            self.status.showMessage("Nothing to wire -- those connections already exist.", 4000)

    def update_edges_for_node(self, node_item):
        for edge in self.edges:
            if edge.from_node is node_item or edge.to_node is node_item:
                if edge.is_reader_shadow:
                    # A reader's hidden return leg never measures its
                    # own distance -- it mirrors the ONE visible channel
                    # (the forward edge the user actually wired) a few
                    # lines below instead, since "only one channel, same
                    # physical path" is the whole point of a monostatic
                    # reader. Still needs update_path() so its endpoints
                    # stay correct for scene_to_topology()'s own distance
                    # math, even though nothing ever draws it.
                    edge.update_path()
                    continue
                edge.update_path()
                if edge.role is not None:
                    edge.sync_distance_from_position()
                    self.inspector.refresh_channel_distance(edge)
                    if edge.role == "forward":
                        # Dragging EITHER endpoint (the tag or its
                        # sender) changes this edge's actual distance,
                        # which feeds the tag's own harvested-energy
                        # estimate -- see NodeInspector._update_energy_
                        # info_label(). edge.to_node is always the tag
                        # on a forward edge (see _edge_channel_role()).
                        self.inspector.refresh_energy_info(edge.to_node)
                        if edge.reader_shadow is not None:
                            edge.reader_shadow.channel_params.update(edge.channel_params)
        self._update_wake_ranges()
        # No position-lock needed here any more: a combined reader is ONE
        # visible node now (see add_reader()) -- its hidden rx half is
        # never added to the scene, so nothing can ever drag it
        # independently, and there's no second on-canvas position left
        # to keep in sync with this one.

    def _update_wake_ranges(self):
        """Redraws every visible wake-up-range AND decode-range ring
        (plus each tag's colored icon margin) from scratch -- clear
        everything, rebuild from current state, rather than tracking
        which ring a given edit staled. Called on node move, any
        formula-feeding param edit, either checkbox, and wire/unwire of
        a forward or return edge.

        Two rings per tag, same color, independently gated, different
        leg AND different owner:
        - Solid wake-up ring: one per forward edge into a tag whose
          SENDER has show_wake_ranges on, centered on the SENDER, radius
          from _wake_up_radius_m() using that edge's own channel_params.
          Sender-owned because whether the tag wakes up is a property of
          the forward (power-up) leg.
        - Dashed decode-range ring: drawn for the same tag whenever its
          return edge's RECEIVER has show_decode_range on (independent
          of the wake-up checkbox above -- see NodeInspector._add_
          decode_range_controls()'s own comment for why: decode success
          is a receiver-side question, not the sender's), centered on
          the RECEIVER, radius from _decode_range_m() using the tag's
          own gamma0/gamma1 params, the forward edge's channel_params
          (tx_power_send/freq/gains) and the return edge's channel_params
          plus that receiver's own decode_snr_threshold_db. See
          _decode_snr_db()'s docstring for exactly what this does and
          does not model.
        Still requires a forward edge to exist at all (the formula needs
        the forward leg's own tx_power_send/freq/gains), but drawing it
        no longer depends on the sender's show_wake_ranges checkbox.
        Neither ring is a function of edge distance -- each is the max
        distance its own leg would still work at; dragging the tag moves
        it relative to these fixed rings (the whole point). Non-finite/
        non-positive/zero radius is skipped rather than drawn, for
        either ring independently. tag.set_identity_highlight() fires if
        EITHER ring actually gets drawn for that tag, not just the
        wake-up one -- a tag visible only via its decode ring still
        deserves its color on the canvas."""
        scene = self.view.scene()
        for item in self.wake_range_items:
            scene.removeItem(item)
        self.wake_range_items = []
        for node in self.nodes.values():
            if node.type_name.startswith("tag_"):
                node.set_identity_highlight(None)

        for edge in self.edges:
            if edge.role != "forward" or edge.channel_params is None:
                continue
            sender, tag = edge.from_node, edge.to_node
            tx_power_send = sender.params.get("tx_power_send")
            freq = edge.channel_params.get("freq")
            g_reader_dbi = edge.channel_params.get("g_reader_dbi")
            g_tag_dbi = edge.channel_params.get("g_tag_dbi")
            color = _tag_identity_color(tag.order_seq)
            border_color = QtGui.QColor(color)
            border_color.setAlpha(220)
            ring_drawn_for_tag = False

            if sender.show_wake_ranges:
                p_th = tag.params.get("p_th")
                radius_m = (
                    _wake_up_radius_m(float(tx_power_send), float(p_th), float(freq),
                                       float(g_reader_dbi), float(g_tag_dbi))
                    if tx_power_send is not None and p_th is not None and freq
                    else float("inf"))
                if radius_m > 0 and radius_m != float("inf"):
                    radius_px = radius_m * PIXELS_PER_METER
                    center = sender.sceneBoundingRect().center()
                    ellipse = QtWidgets.QGraphicsEllipseItem(
                        center.x() - radius_px, center.y() - radius_px,
                        radius_px * 2, radius_px * 2)
                    # Filled, not just an outline -- an earlier NoBrush/
                    # thin-dashed-line version ("low opacity", per
                    # feedback) was very easy to lose against the grid
                    # background/other rings, especially with several
                    # tags' circles overlapping. Same per-tag color on
                    # both the fill (translucent, so nodes/edges drawn
                    # on top of it -- see zValue below -- and
                    # overlapping rings from OTHER tags stay readable)
                    # and the border (solid, near-opaque, so the
                    # boundary itself -- the actual point of the ring --
                    # still reads clearly even where two tags' fills
                    # overlap and blend).
                    fill_color = QtGui.QColor(color)
                    fill_color.setAlpha(60)
                    ellipse.setBrush(QtGui.QBrush(fill_color))
                    # Outline width only (NOT the fill/circle size) --
                    # per explicit feedback that with several tags'
                    # rings on screen at once, the boundary itself ("the
                    # actual point of the ring") is the thing that needs
                    # to stay readable where rings overlap.
                    ellipse.setPen(QtGui.QPen(border_color, 5))
                    ellipse.setZValue(-2)  # under edges/nodes, same convention as EdgeItem's -1
                    ellipse.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
                    scene.addItem(ellipse)
                    self.wake_range_items.append(ellipse)
                    ring_drawn_for_tag = True

                    # Small tag_id label identifying which tag this ring
                    # belongs to -- can't curve text along the ellipse's
                    # own outline in Qt's graphics view without building
                    # a custom path-text item, so per the user's own
                    # stated fallback, it's placed just above the ring's
                    # top point instead, attached to the line visually
                    # by color + proximity rather than by sitting
                    # literally on top of the stroke.
                    label = QtWidgets.QGraphicsSimpleTextItem(tag.node_id)
                    label_font = label.font()
                    label_font.setPointSize(15)
                    label_font.setBold(True)
                    label.setFont(label_font)
                    label.setBrush(QtGui.QBrush(border_color))
                    label_rect = label.boundingRect()
                    label.setPos(
                        center.x() - label_rect.width() / 2.0,
                        center.y() - radius_px - label_rect.height())
                    label.setZValue(-2)
                    label.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
                    scene.addItem(label)
                    self.wake_range_items.append(label)

            # Decode-range ring (return leg): a SECOND ring for the same
            # tag, dashed so it reads as distinct from the solid wake-up
            # ring above even when they're nested, drawn only once this
            # tag's return edge (tag -> rx) actually exists AND that
            # return edge's own receiver has show_decode_range on --
            # independent of sender.show_wake_ranges above (used to
            # share that one checkbox; moved off it per explicit
            # feedback that a receiver-side concern shouldn't ride on a
            # sender-side toggle). A tag wired to a sender but not yet
            # to any receiver, or wired to a receiver with the toggle
            # off, has nothing to draw here, same "nothing to draw yet"
            # treatment the wake-up ring gives a missing forward edge.
            # Centered on the RECEIVER, not the sender: the two are the
            # same point for a combined "reader" node (the common case --
            # EdgeItem._wire_reader_shadow_return() ties both legs'
            # distance together for exactly this setup), but a
            # standalone, separately-placed sender/rx pair genuinely has
            # two different "how far can X be" questions, and this ring
            # answers the receiver's one.
            ret_edge = next((e for e in self.edges
                              if e.role == "return" and e.from_node is tag), None)
            if ret_edge is None or ret_edge.channel_params is None:
                if ring_drawn_for_tag:
                    tag.set_identity_highlight(color)
                continue
            receiver = ret_edge.to_node
            if not receiver.show_decode_range:
                if ring_drawn_for_tag:
                    tag.set_identity_highlight(color)
                continue
            gamma0_mag = tag.params.get("gamma0_mag")
            gamma0_phase_deg = tag.params.get("gamma0_phase_deg")
            gamma1_mag = tag.params.get("gamma1_mag")
            gamma1_phase_deg = tag.params.get("gamma1_phase_deg")
            ret_freq = ret_edge.channel_params.get("freq")
            ret_g_reader_dbi = ret_edge.channel_params.get("g_reader_dbi")
            ret_g_tag_dbi = ret_edge.channel_params.get("g_tag_dbi")
            ret_noise_volt_c1 = ret_edge.channel_params.get("noise_volt_c1")
            if (tx_power_send is None or not freq or gamma0_mag is None
                    or gamma1_mag is None or not ret_freq or ret_noise_volt_c1 is None):
                if ring_drawn_for_tag:
                    tag.set_identity_highlight(color)
                continue
            # receiver.decode_snr_threshold_db -- THIS receiver's own
            # user-editable SNR figure (NodeInspector._add_decode_range_
            # controls()'s threshold_spin), not the sender's and not the
            # module default. See that spinbox's own tooltip for why
            # this is deliberately NOT a fixed constant: the right "can
            # decode" SNR genuinely differs by modulation/detector, so
            # it's a value the user calibrates against their own
            # measured BER, not a universal truth.
            snr_threshold_db = receiver.decode_snr_threshold_db
            decode_radius_m = _decode_range_m(
                float(tx_power_send),
                float(freq), float(g_reader_dbi), float(g_tag_dbi),
                float(gamma0_mag), float(gamma0_phase_deg),
                float(gamma1_mag), float(gamma1_phase_deg),
                float(ret_freq), float(ret_g_reader_dbi), float(ret_g_tag_dbi),
                float(ret_noise_volt_c1),
                snr_threshold_db=snr_threshold_db)
            if not (decode_radius_m > 0) or decode_radius_m == float("inf"):
                if ring_drawn_for_tag:
                    tag.set_identity_highlight(color)
                continue

            decode_radius_px = decode_radius_m * PIXELS_PER_METER
            decode_center = receiver.sceneBoundingRect().center()
            decode_ellipse = QtWidgets.QGraphicsEllipseItem(
                decode_center.x() - decode_radius_px, decode_center.y() - decode_radius_px,
                decode_radius_px * 2, decode_radius_px * 2)
            # No fill (NoBrush) -- this ring commonly nests inside or
            # overlaps the solid wake-up ring above (same tag, same
            # general area), and two translucent fills stacked would
            # blend into a third, misleading color. The dashed, same-
            # hue border is enough to trace it back to its tag while
            # staying visually distinct from the solid ring.
            decode_ellipse.setBrush(QtGui.QBrush(QtCore.Qt.BrushStyle.NoBrush))
            decode_pen = QtGui.QPen(border_color, 3)
            decode_pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            decode_ellipse.setPen(decode_pen)
            decode_ellipse.setZValue(-2)
            decode_ellipse.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            scene.addItem(decode_ellipse)
            self.wake_range_items.append(decode_ellipse)
            ring_drawn_for_tag = True

            decode_label = QtWidgets.QGraphicsSimpleTextItem(
                f"{tag.node_id} decode @ {snr_threshold_db:g}dB")
            decode_label_font = decode_label.font()
            decode_label_font.setPointSize(11)
            decode_label_font.setItalic(True)
            decode_label.setFont(decode_label_font)
            decode_label.setBrush(QtGui.QBrush(border_color))
            decode_label_rect = decode_label.boundingRect()
            decode_label.setPos(
                decode_center.x() - decode_label_rect.width() / 2.0,
                decode_center.y() - decode_radius_px - decode_label_rect.height())
            decode_label.setZValue(-2)
            decode_label.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            scene.addItem(decode_label)
            self.wake_range_items.append(decode_label)

            tag.set_identity_highlight(color)

    def set_channel_edge_distance(self, edge, meters):
        """Inverse of EdgeItem.sync_distance_from_position(): typing a
        distance moves this edge's TAG endpoint (never sender/receiver,
        which other tags' edges may share) so the on-canvas gap matches.
        Moving the node fires NodeItem.itemChange() -> update_edges_for_node()
        as a manual drag would, which re-syncs this edge's distance (and
        the tag's other channel edge, if any) from the new position.

        A tag has up to two channel edges (forward + return) anchored to
        the SAME tag node, so moving it shifts both ports together.
        _solve_tag_move() picks the translation that hits the NEW typed
        distance while leaving the OTHER edge's current distance
        unchanged (via circle/circle intersection), instead of a naive
        ray-slide that would silently perturb the other edge too."""
        if edge.role == "forward":
            anchor_node, anchor_key = edge.from_node, ("out", edge.from_port_name)
            mover_node, mover_key = edge.to_node, ("in", edge.to_port_name)
            other_edge = next((e for e in self.edges
                                if e.role == "return" and e.from_node is mover_node), None)
        elif edge.role == "return":
            anchor_node, anchor_key = edge.to_node, ("in", edge.to_port_name)
            mover_node, mover_key = edge.from_node, ("out", edge.from_port_name)
            other_edge = next((e for e in self.edges
                                if e.role == "forward" and e.to_node is mover_node), None)
        else:
            return

        anchor_pos = anchor_node.port_handles[anchor_key].scenePos()
        target_px = max(0.0, meters) * PIXELS_PER_METER
        delta = self._solve_tag_move(mover_node, mover_key, anchor_pos, target_px, other_edge)
        mover_node.setPos(mover_node.pos() + delta)

    def _solve_tag_move(self, mover_node, mover_key, anchor_pos, target_px, other_edge):
        """Computes the translation for mover_node (a tag) so its
        `mover_key` port ends up exactly target_px from anchor_pos,
        while leaving `other_edge` (the tag's other channel edge, if
        any) at its CURRENT distance -- a circle/circle intersection:
        circle A = positions hitting the new target distance, circle B
        = positions keeping the other edge's distance unchanged (the
        tag's current position already lies on B), so their
        intersection nearest the origin is the least-disruptive move.

        Falls back to a plain ray-slide along the anchor line when
        there's no other edge yet, or when the two distances are
        geometrically incompatible (circles don't intersect) -- the
        other edge just drifts in that fallback case."""
        old_mover_port_pos = mover_node.port_handles[mover_key].scenePos()
        direction = old_mover_port_pos - anchor_pos
        length = QtCore.QLineF(QtCore.QPointF(0, 0), direction).length()
        if length < 1e-6:
            # Coincident ports (e.g. a node dropped right on top of its
            # anchor) -- there's no direction to extend along, so pick an
            # arbitrary one rather than dividing by zero.
            unit = QtCore.QPointF(1.0, 0.0)
        else:
            unit = QtCore.QPointF(direction.x() / length, direction.y() / length)
        fallback_delta = (anchor_pos + unit * target_px) - old_mover_port_pos

        if other_edge is None or other_edge.channel_params is None:
            return fallback_delta

        if other_edge.role == "forward":
            other_anchor_pos = other_edge.from_node.port_handles[
                ("out", other_edge.from_port_name)].scenePos()
            other_mover_key = ("in", other_edge.to_port_name)
        else:
            other_anchor_pos = other_edge.to_node.port_handles[
                ("in", other_edge.to_port_name)].scenePos()
            other_mover_key = ("out", other_edge.from_port_name)
        old_other_port_pos = mover_node.port_handles[other_mover_key].scenePos()
        other_r_px = other_edge.channel_params["distance"] * PIXELS_PER_METER

        # Solve for translation D such that:
        #   |old_mover_port_pos + D - anchor_pos|       == target_px  (circle A)
        #   |old_other_port_pos + D - other_anchor_pos| == other_r_px (circle B)
        center_a = anchor_pos - old_mover_port_pos
        center_b = other_anchor_pos - old_other_port_pos
        d_vec = center_b - center_a
        d = QtCore.QLineF(QtCore.QPointF(0, 0), d_vec).length()
        r1, r2 = target_px, other_r_px

        if d < 1e-6 or d > r1 + r2 or d < abs(r1 - r2):
            # No intersection: centers coincide, or the circles are too
            # far apart / one fully inside the other with no overlap --
            # the two typed distances can't both be hit exactly from the
            # sender/receiver's current canvas placement.
            return fallback_delta

        a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
        h = max(0.0, r1 * r1 - a * a) ** 0.5
        mid = center_a + d_vec * (a / d)
        perp = QtCore.QPointF(-d_vec.y(), d_vec.x()) * (1.0 / d)
        cand1, cand2 = mid + perp * h, mid - perp * h

        # Pick whichever intersection keeps the tag closest to where it
        # already sits (D nearest (0, 0)) -- the least visually jarring
        # choice, and the natural one since D=(0, 0) already satisfies
        # circle B exactly before this move.
        len1 = QtCore.QLineF(QtCore.QPointF(0, 0), cand1).length()
        len2 = QtCore.QLineF(QtCore.QPointF(0, 0), cand2).length()
        return cand1 if len1 <= len2 else cand2

    def on_selection_changed(self):
        selected = self.view.scene().selectedItems()
        if len(selected) == 1 and isinstance(selected[0], NodeItem):
            self.inspector.show_node(selected[0])
        elif (len(selected) == 1 and isinstance(selected[0], EdgeItem)
              and selected[0].channel_params is not None):
            # Clicking the connecting arrow is "basically a block" --
            # see EdgeItem.role/channel_params and NodeInspector.show_edge().
            self.inspector.show_edge(selected[0])
        else:
            self.inspector.show_node(None)

    def delete_selected(self):
        for item in list(self.view.scene().selectedItems()):
            if isinstance(item, NodeItem):
                self._delete_node(item)
            elif isinstance(item, EdgeItem):
                self._delete_edge(item)

    def _delete_node(self, node_item, _cascade_partner=True):
        """_cascade_partner=False is used only by the recursive call
        just below (deleting a reader's OTHER half) and by
        _convert_node_modulation() (rebuilding one half of a reader in
        place must never take its partner down too) -- every other
        caller wants the normal cascading behavior. The leading guard
        makes deleting BOTH halves of one reader in the same
        delete_selected() pass safe: the first call's own cascade
        already removed the second half from self.nodes by the time
        delete_selected()'s loop reaches it directly."""
        if self.nodes.get(node_item.node_id) is not node_item:
            return
        partner = node_item.reader_partner
        for edge in [e for e in self.edges if e.from_node is node_item or e.to_node is node_item]:
            self._delete_edge(edge)
        del self.nodes[node_item.node_id]
        # A reader's hidden rx half (see add_reader()/NodeItem.
        # reader_partner) was deliberately never added to the scene --
        # scene() is None for it, same reason _delete_edge() guards its
        # own removeItem() call for a shadow return edge.
        if node_item.scene() is not None:
            self.view.scene().removeItem(node_item)
        if self.inspector.current_node is node_item:
            self.inspector.show_node(None)
        self._update_wake_ranges()
        if _cascade_partner and partner is not None and partner.node_id in self.nodes:
            self._delete_node(partner, _cascade_partner=False)

    def _delete_edge(self, edge_item):
        if edge_item not in self.edges:
            return
        self.edges.remove(edge_item)
        # A reader's hidden shadow return edge (see
        # _wire_reader_shadow_return()) was deliberately never added
        # to the scene -- scene() is None for it, and QGraphicsScene.
        # removeItem() on an item that was never added to this scene is
        # exactly the thing to avoid calling.
        if edge_item.scene() is not None:
            self.view.scene().removeItem(edge_item)
        if self.inspector.current_edge is edge_item:
            self.inspector.show_node(None)
        if edge_item.reader_shadow is not None:
            # This was a reader's visible forward edge (sender->tag) --
            # take its hidden return leg (tag->rx) down with it, and
            # give the tag's output port back since it's no longer
            # spoken for by this reader.
            shadow = edge_item.reader_shadow
            if shadow in self.edges:
                self.edges.remove(shadow)
            edge_item.reader_shadow = None
            tag = edge_item.to_node
            if tag.out_ports:
                out_handle = tag.port_handles.get(("out", tag.out_ports[0].name))
                if out_handle is not None:
                    out_handle.set_auto_wired(False)
        self._update_wake_ranges()

    def copy_selected(self):
        """Copies the one selected node's data (type, params, payload,
        decoder_params) into self._clipboard as a plain dict -- NOT a
        reference to the live NodeItem, so later edits/deletes of the
        original can't affect a later Paste.

        For a tag_* node, also remembers its wired sender (forward) and
        receiver (return) plus those edges' channel_params (noise/fading,
        NOT distance -- see paste_clipboard()), so a pasted tag arrives
        pre-wired to the same sender/receiver (the "wire one, clone it 9
        times" workflow)."""
        selected_nodes = [i for i in self.view.scene().selectedItems() if isinstance(i, NodeItem)]
        if len(selected_nodes) != 1:
            self.status.showMessage("Select exactly one node to copy.", 4000)
            return
        node = selected_nodes[0]
        if node.reader_partner is not None:
            # Not supported yet -- a combined reader (see add_reader())
            # is visibly one node, but it's still backed by two real
            # NodeItems (the visible sender plus its never-scene-added
            # hidden rx), and this clipboard only ever holds one node's
            # data. Copying just the sender alone would paste a floating
            # sender with no hidden rx behind it at all, silently
            # dropping the "combined reader" behavior entirely --
            # refusing is less confusing than that. (The hidden rx
            # itself can never reach this method in the first place,
            # since selectedItems() only ever contains items that were
            # added to the scene.)
            self.status.showMessage(
                "Copy/paste isn't supported for a combined reader yet -- drag a fresh "
                "'reader' from the palette instead.", 6000)
            return

        fwd_edge = next((e for e in self.edges if e.role == "forward" and e.to_node is node), None)
        ret_edge = next((e for e in self.edges if e.role == "return" and e.from_node is node), None)

        self._clipboard = {
            "type_name": node.type_name,
            "params": dict(node.params),
            "payload": list(node.payload) if node.payload is not None else None,
            "decoder_params": dict(node.decoder_params) if node.decoder_params is not None else None,
            "payload_mode": node.payload_mode,
            "message_text": node.message_text,
            "chunk_bytes": node.chunk_bytes,
            "file_bytes": node.file_bytes,
            "file_name": node.file_name,
            "packets_per_wake": node.packets_per_wake,
            "energy_per_bit": node.energy_per_bit,
            "energy_switch_mode": node.energy_switch_mode,
            "energy_c_gate_fF": node.energy_c_gate_fF,
            "energy_vdd": node.energy_vdd,
            "energy_r_bias_ohm": node.energy_r_bias_ohm,
            "energy_duty_cycle": node.energy_duty_cycle,
            "energy_static_rate": node.energy_static_rate,
            "decode_snr_threshold_db": (
                node.decode_snr_threshold_db if node.type_name.startswith("rx_") else None),
            "forward_from": fwd_edge.from_node if fwd_edge is not None else None,
            "forward_channel_params": dict(fwd_edge.channel_params) if fwd_edge is not None else None,
            "return_to": ret_edge.to_node if ret_edge is not None else None,
            "return_channel_params": dict(ret_edge.channel_params) if ret_edge is not None else None,
            # Where the NEXT paste should land -- bumped by PASTE_OFFSET
            # after every paste (see paste_clipboard()) so repeated
            # Ctrl+V cascades outward instead of restacking on the exact
            # same spot every time.
            "next_pos": node.sceneBoundingRect().center(),
        }
        self.status.showMessage(f"Copied {node.node_id}. Paste (or Ctrl+V) to duplicate it.", 3000)

    def paste_clipboard(self):
        clip = self._clipboard
        if clip is None:
            self.status.showMessage("Nothing copied yet -- select a node and Copy (or Ctrl+C) first.", 4000)
            return

        paste_pos = clip["next_pos"] + QtCore.QPointF(self.PASTE_OFFSET, self.PASTE_OFFSET)
        clip["next_pos"] = paste_pos  # so the NEXT paste cascades further still
        new_node = self.add_node(clip["type_name"], paste_pos, params=clip["params"],
                                  payload=clip["payload"], decoder_params=clip["decoder_params"],
                                  payload_mode=clip["payload_mode"], message_text=clip["message_text"],
                                  chunk_bytes=clip["chunk_bytes"], file_bytes=clip["file_bytes"],
                                  file_name=clip["file_name"],
                                  packets_per_wake=clip.get("packets_per_wake"),
                                  energy_per_bit=clip.get("energy_per_bit"),
                                  energy_switch_mode=clip.get("energy_switch_mode"),
                                  energy_c_gate_fF=clip.get("energy_c_gate_fF"),
                                  energy_vdd=clip.get("energy_vdd"),
                                  energy_r_bias_ohm=clip.get("energy_r_bias_ohm"),
                                  energy_duty_cycle=clip.get("energy_duty_cycle"),
                                  energy_static_rate=clip.get("energy_static_rate"),
                                  decode_snr_threshold_db=clip.get("decode_snr_threshold_db"))

        # Recreate the same wiring the original tag had, to the SAME
        # sender/receiver (not a copy of them) -- only if that node is
        # still actually on the canvas (checked by identity, not just
        # node_id, in case it was deleted and a new unrelated node
        # happens to reuse the id).
        wired = 0
        sender = clip["forward_from"]
        if sender is not None and sender in self.nodes.values() and sender.out_ports and new_node.in_ports:
            edge = self.add_edge(sender, sender.out_ports[0].name, new_node, new_node.in_ports[0].name)
            if edge is not None and clip["forward_channel_params"] is not None:
                # Carry over the tuned noise/fading/etc, but NOT the
                # copied distance -- this new edge's distance was just
                # computed fresh from the PASTED node's own position
                # (EdgeItem.__init__ -> sync_distance_from_position()),
                # which is the whole point: paste it somewhere, that
                # position IS its distance, same as any other edge.
                other = {k: v for k, v in clip["forward_channel_params"].items() if k != "distance"}
                edge.channel_params.update(other)
            wired += 1
        rx = clip["return_to"]
        if rx is not None and rx in self.nodes.values() and rx.in_ports and new_node.out_ports:
            edge = self.add_edge(new_node, new_node.out_ports[0].name, rx, rx.in_ports[0].name)
            if edge is not None and clip["return_channel_params"] is not None:
                other = {k: v for k, v in clip["return_channel_params"].items() if k != "distance"}
                edge.channel_params.update(other)
            wired += 1

        msg = f"Pasted {new_node.node_id}"
        msg += f" with {wired} wire(s) matching the original." if wired else "."
        self.status.showMessage(msg, 4000)
        self.view.scene().clearSelection()
        new_node.setSelected(True)
        # add_edge() above already refreshed the wake-up-range rings, but
        # using the freshly-SEEDED channel_params -- the carried-over
        # freq/g_reader_dbi/g_tag_dbi (see the channel_params.update()
        # calls above) land AFTER that, so one more refresh here is what
        # actually picks those up.
        self._update_wake_ranges()

    def keyPressEvent(self, event):
        ctrl = event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
        if event.key() in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            self.delete_selected()
        elif ctrl and event.key() == QtCore.Qt.Key.Key_C:
            self.copy_selected()
        elif ctrl and event.key() == QtCore.Qt.Key.Key_V:
            self.paste_clipboard()
        else:
            super().keyPressEvent(event)

    def _convert_node_modulation(self, node_item, new_modulation):
        """Swaps ONE tag_*/rx_* node to a different modulation's
        underlying registry type (e.g. tag_ook -> tag_bpsk). See
        MODULATION_CHOICES' module comment for why this is deliberately
        scoped to exactly this node and never cascades to anything it's
        wired to.

        Implemented as delete-and-recreate (reusing add_node/_delete_node/
        add_edge rather than mutating a live NodeItem's port set in
        place) because the two types can have a different port count/
        layout and param list -- rebuilding is simpler and far less
        error-prone than surgery on an existing item's PortHandles.
        """
        is_tag = node_item.type_name.startswith("tag_")
        is_rx = node_item.type_name.startswith("rx_")
        if not (is_tag or is_rx):
            return
        key = "tag_type" if is_tag else "rx_type"
        new_type_name = registry.MODULATION_MAP[new_modulation][key]
        if new_type_name == node_item.type_name:
            return

        old_nt = registry.NODE_TYPES[node_item.type_name]
        new_nt = registry.NODE_TYPES[new_type_name]

        # Carry over a shared-name param only if its current value is a
        # deliberate edit, not just the OLD type's default/seed. Several
        # params (gamma0/1_mag/phase_deg, as_real/as_imag, si_isolation_db)
        # have genuinely different correct defaults per modulation -- the
        # gamma pair literally IS the modulation (OOK: big magnitude
        # contrast/same phase; BPSK: near-equal magnitude/opposite phase).
        # Blindly carrying these over left a switched node physically
        # configured for the OLD modulation while labeled as the new one.
        # Fix: compare each shared param's current value to a FRESH node
        # of the OLD type's default/seed -- if they match it was never
        # edited, so let the NEW type's fresh default apply instead; if
        # they differ, the user tuned it deliberately and it survives
        # the switch (slot_id, a hand-tuned bit_rate, etc.).
        old_seed = NODE_SEED_OVERRIDES.get(node_item.type_name, {})
        old_fresh = {p.name: old_seed.get(p.name, p.default) for p in old_nt.params}
        new_seed = NODE_SEED_OVERRIDES.get(new_type_name, {})
        shared = {p.name for p in old_nt.params} & {p.name for p in new_nt.params}
        new_params = {p.name: p.default for p in new_nt.params}
        new_params.update(new_seed)
        for name in shared:
            if name not in node_item.params:
                continue
            old_value = node_item.params[name]
            if old_value != old_fresh.get(name):
                new_params[name] = old_value

        # Positional port-rename map, per direction -- e.g. tag_ook's
        # single "out" <-> tag_fm0's single "Reflection_Out", both being
        # the 1st (and only) "out" port on each type. A port with no
        # same-direction counterpart at that position on the new type
        # maps to None (dropped, reported below).
        def _ports_by_dir(nt):
            return {
                "in": [p.name for p in nt.ports if p.direction == "in"],
                "out": [p.name for p in nt.ports if p.direction == "out"],
            }
        old_pd, new_pd = _ports_by_dir(old_nt), _ports_by_dir(new_nt)
        rename = {}
        for direction in ("in", "out"):
            for i, old_name in enumerate(old_pd[direction]):
                rename[old_name] = new_pd[direction][i] if i < len(new_pd[direction]) else None

        # Snapshot everything needed to rebuild, before the old node (and
        # every edge touching it) gets deleted.
        node_id = node_item.node_id
        # If the id still looks exactly like the auto-generated
        # "<old type><number>" (e.g. "tag_bpsk1"), update it to match the
        # new type (e.g. "tag_ook1") so the label on the canvas doesn't
        # keep showing the old modulation's name after switching --
        # purely cosmetic, never touches a custom id the user actually
        # typed in themselves. Skipped if that name's already taken
        # (another node of the new type already has it).
        m = re.fullmatch(re.escape(node_item.type_name) + r"(\d+)", node_id)
        if m:
            candidate = f"{new_type_name}{m.group(1)}"
            if candidate not in self.nodes or candidate == node_id:
                node_id = candidate
        payload = list(node_item.payload) if node_item.payload is not None else None
        # payload_mode/message_text/chunk_bytes: carried over exactly as-
        # is, same reasoning as payload/decoder_params right below -- a
        # modulation switch changes HOW bits get on the air, not WHAT
        # message a tag is sending. Missing this (add_node()'s defaults
        # are "raw"/""/4 for any brand-new tag) used to silently revert
        # a text-mode tag back to raw/empty the instant its modulation
        # was switched -- "switch to OOK and nothing shows anymore" is
        # exactly that: both tags got reset to raw mode, so neither one
        # was transmitting a framed message any more at all. None for a
        # non-tag node (rx_*), matching NodeItem.__init__'s own default.
        payload_mode = node_item.payload_mode
        message_text = node_item.message_text
        chunk_bytes = node_item.chunk_bytes
        file_bytes = node_item.file_bytes
        file_name = node_item.file_name
        # packets_per_wake: same reasoning -- a modulation switch
        # shouldn't silently reset how many wake-burst repeats this tag
        # is configured for back to the fresh-tag default. add_node()
        # recomputes packet_bits itself from this against the rebuilt
        # item's own (possibly carried-over) payload/payload_mode/etc,
        # so no separate packet_bits carryover is needed here at all --
        # whatever numeric value new_params ends up with for it (via
        # the shared-param carryover logic above) is always overwritten
        # by that fresh derivation.
        packets_per_wake = node_item.packets_per_wake
        # order_seq carries over too -- see NodeItem.order_seq's own
        # comment and add_node()'s order_seq param: without this, the
        # rebuilt item would get a BRAND NEW (later) order_seq, which
        # is exactly the "tag_id flips when you switch just one tag's
        # modulation" bug this was added to fix in the first place --
        # re-deleting/re-adding is an implementation detail of a
        # modulation switch, not a reason for this tag to act like it
        # was just placed on the canvas for the first time.
        order_seq = node_item.order_seq
        # decoder_params carries over as-is -- packet_hex_decoder's own
        # param set doesn't depend on which rx_* type feeds it (every
        # rx type decodes to the same byte stream shape), so a modulation
        # switch between rx types has no reason to reset it, unlike
        # `params` above which genuinely needs type-aware remapping.
        decoder_params = dict(node_item.decoder_params) if node_item.decoder_params is not None else None
        rect = node_item.rect()
        center = node_item.pos() + QtCore.QPointF(rect.width() / 2, rect.height() / 2)
        was_selected = node_item.isSelected()

        pending_edges = []  # (role, this_node's_old_port, other_node, other_port)
        for e in self.edges:
            if e.is_reader_shadow:
                # Deliberately NOT queued for the generic recreate-via-add_edge()
                # loop below: that loop's add_edge() always scene-adds, which
                # would make this shadow pop into VISIBLE existence as a
                # duplicate arrow. Both real cases are already handled: converting
                # the shadow's TAG re-queues the real forward edge, whose
                # recreation re-fires add_edge()'s reader hook and re-derives a
                # fresh hidden shadow; converting the reader's RX doesn't touch
                # the forward edge at all, and is handled explicitly below.
                continue
            if e.from_node is node_item:
                pending_edges.append(("out", e.from_port_name, e.to_node, e.to_port_name))
            elif e.to_node is node_item:
                pending_edges.append(("in", e.to_port_name, e.from_node, e.from_port_name))

        # reader_partner: None for a tag (tags are never a reader's own
        # sender/rx half, see add_reader()) -- captured before the
        # delete below so a combined reader's rx can switch modulation
        # WITHOUT its sender half getting cascade-deleted along with it
        # (_cascade_partner=False), and so the link can be restored onto
        # the freshly rebuilt replacement node right after. Only a
        # reader's hidden RX ever actually reaches this method with
        # reader_partner set (TYPE_TO_MODULATION has no "sender" entry,
        # so a sender itself never goes through a modulation switch) --
        # which is also why add_to_scene below is keyed off it: the
        # rebuilt replacement has to stay just as invisible as the node
        # it's replacing, or a modulation switch would silently turn a
        # reader back into V1's two-visible-boxes bug.
        reader_partner = node_item.reader_partner
        self._delete_node(node_item, _cascade_partner=False)
        new_item = self.add_node(new_type_name, center, node_id=node_id,
                                  params=new_params, payload=payload, decoder_params=decoder_params,
                                  payload_mode=payload_mode, message_text=message_text,
                                  chunk_bytes=chunk_bytes, file_bytes=file_bytes,
                                  file_name=file_name, order_seq=order_seq,
                                  packets_per_wake=packets_per_wake,
                                  add_to_scene=reader_partner is None)
        if reader_partner is not None:
            new_item.reader_partner = reader_partner
            reader_partner.reader_partner = new_item
            # node_item here is always a reader's hidden rx (see this
            # method's own reader_partner comment above) -- reader_
            # partner is its sender, whose forward edge to a tag (if
            # any) was never touched by the delete above, since neither
            # of ITS endpoints is the rx being rebuilt. That forward
            # edge's own OLD shadow WAS torn down though (every edge
            # touching the old rx got deleted, see _delete_node()), and
            # was deliberately skipped from pending_edges above rather
            # than generically recreated -- re-derive it properly here
            # instead, now that new_item (the rebuilt rx) actually
            # exists to point it at.
            fwd = next((e for e in self.edges
                        if e.role == "forward" and e.from_node is reader_partner), None)
            if fwd is not None:
                self._wire_reader_shadow_return(fwd)
        if was_selected:
            new_item.setSelected(True)

        dropped = []
        for role, old_port, other_node, other_port in pending_edges:
            new_port = rename.get(old_port)
            if new_port is None:
                dropped.append(f"{old_port} -> {other_node.node_id}.{other_port}")
                continue
            if role == "out":
                self.add_edge(new_item, new_port, other_node, other_port)
            else:
                self.add_edge(other_node, other_port, new_item, new_port)

        msg = f"'{node_id}' switched to {new_modulation} ({new_type_name})."
        if dropped:
            msg += " Dropped edge(s) with no matching port: " + "; ".join(dropped)
        self.status.showMessage(msg, 7000)
        self.inspector.show_node(new_item)

    # --- canvas -> topology spec, same shape build_fixed_topology() produces ---

    def _find_upstream_tags(self, rx_item):
        """Walks backward from rx_item through the channel node(s)
        feeding it and returns the tag_* NodeItems found, in a STABLE
        order -- this receiver's decoder settings derive from exactly
        these tags (per-receiver, not global), and the returned order
        is what scene_to_topology() uses to assign tag_id, so stability
        here is what keeps tag_id stable across rebuilds.

        GOTCHA: sorted by order_seq, NOT edge-list insertion order
        (unstable -- _convert_node_modulation() re-adds a tag's edges at
        the end of self.edges on every switch, which used to flip which
        tag got tag_id 0 vs 1) and NOT node_id (also unstable -- a
        modulation switch can rename the node, e.g. tag_bpsk1->tag_ook1).
        order_seq is the one field deliberately preserved unchanged
        through a modulation switch's delete+recreate for this reason."""
        seen = set()
        frontier = [rx_item]
        tags = []
        while frontier:
            node = frontier.pop()
            if node.node_id in seen:
                continue
            seen.add(node.node_id)
            for e in self.edges:
                if e.to_node is node:
                    src = e.from_node
                    if src.type_name.startswith("tag_"):
                        tags.append(src)
                    elif src.type_name == "channel":
                        frontier.append(src)
        tags.sort(key=lambda t: t.order_seq)
        return tags

    def _find_downstream_receiver(self, tag_item):
        """The mirror of _find_upstream_tags(): walks FORWARD from a
        tag through however many channel node(s) it feeds into, and
        returns the first rx_* NodeItem found (None if the tag isn't
        wired to any receiver yet). Used only for display -- the
        decoder's "preamble" field, deliberately a receiver-side-only
        setting (see NodeInspector.show_node()'s preamble_info_label
        and plan_topology()'s own docstring), so a tag's own panel can
        still answer "what preamble am I actually going to transmit,
        and where do I change it" without needing its own editable
        copy of the same value."""
        seen = set()
        frontier = [tag_item]
        while frontier:
            node = frontier.pop()
            if node.node_id in seen:
                continue
            seen.add(node.node_id)
            for e in self.edges:
                if e.from_node is node:
                    dst = e.to_node
                    if dst.type_name.startswith("rx_"):
                        return dst
                    if dst.type_name == "channel":
                        frontier.append(dst)
        return None

    def _sync_decoder_params(self, rx_item, upstream_tags, notify=True):
        """Keeps a receiver's decoder config -- and, for FM0/Miller, its own
        bit_rate/payload_len/preamble -- derived from its upstream tag(s)
        instead of letting two editable copies of the same value drift apart.
        notify=False: silent recompute before display. notify=True: also
        posts a status-bar message when something had to be auto-corrected."""
        changed = False

        # preamble: FM0/Miller carry it as their own param now: just
        # mirror it into decoder_params as the single source of truth,
        # rather than deriving the two independently. Doesn't depend on
        # any tag data, so it's unconditional -- not gated on whether
        # the payload-length lookup below even resolves.
        if ("preamble" in rx_item.params
                and rx_item.decoder_params.get("preamble") != rx_item.params["preamble"]):
            rx_item.decoder_params["preamble"] = rx_item.params["preamble"]
            changed = True

        # effective_fragment_bits(), not len(t.payload) directly -- a
        # text-mode tag's real per-fragment length is header+chunk_bytes
        # (see NodeItem.effective_fragment_bits()/message_framer.py),
        # not the length of its (irrelevant, in that mode) self.payload.
        payload_lens = {t.effective_fragment_bits() for t in upstream_tags
                         if t.effective_fragment_bits()}
        if len(payload_lens) == 1:
            payload_len = payload_lens.pop()
            if "payload_len" in rx_item.params:
                # FM0/Miller: the receiver's own payload_len is the one
                # editable copy shown on the UI -- auto-correct IT, then
                # mirror the result into decoder_params, instead of
                # deriving the two independently (which is what let the
                # exact same number be shown, and separately settable,
                # in two different places).
                if rx_item.params["payload_len"] != payload_len:
                    rx_item.params["payload_len"] = payload_len
                    changed = True
                    if notify:
                        self.status.showMessage(
                            f"Auto-set {rx_item.node_id}'s payload length to "
                            f"{payload_len} bits to match its tag(s).", 5000)
                if rx_item.decoder_params.get("payload_len_bits") != rx_item.params["payload_len"]:
                    rx_item.decoder_params["payload_len_bits"] = rx_item.params["payload_len"]
                    changed = True
            else:
                # OOK/BPSK: no rx-level payload_len of their own --
                # derive the decoder's payload_len_bits directly, same
                # as before.
                if rx_item.decoder_params.get("payload_len_bits") != payload_len:
                    rx_item.decoder_params["payload_len_bits"] = payload_len
                    changed = True
                    if notify:
                        self.status.showMessage(
                            f"Auto-set {rx_item.node_id}'s decoder Payload Length to "
                            f"{payload_len} bits to match its tag(s).", 5000)
        # Else: either no payload data yet (len==0, nothing to say), or
        # this receiver's own tags disagree on length (len>1) -- same
        # case build_fixed_topology() would assert on. Previously this
        # was a silent no-op either way, which is exactly what made a
        # genuine disagreement look like "auto-sync is just broken" --
        # reported first on BPSK, but the guard isn't BPSK-specific at
        # all (see payload_lens above: it's keyed off upstream_tags,
        # same set/same check for every modulation). Surface it instead:
        # a disagreement is almost always two tags feeding the same
        # receiver with different chunk_bytes (or one text-mode, one
        # raw-mode with a different payload length), which also means
        # whichever tag's length didn't get picked silently fails to
        # decode at all until its length matches too.
        elif len(payload_lens) > 1 and notify:
            self.status.showMessage(
                f"'{rx_item.node_id}': upstream tags disagree on fragment length "
                f"{sorted(payload_lens)} bits -- NOT auto-syncing payload length. "
                "Make every upstream tag's payload/chunk size match (only the "
                "matching one(s) will decode until then).", 9000)

        rates = {t.params.get("bit_rate") for t in upstream_tags
                  if t.params.get("bit_rate") is not None}
        bit_rate = None
        if rates:
            bit_rate = next(iter(rates))
            if len(rates) > 1 and notify:
                self.status.showMessage(
                    f"'{rx_item.node_id}': upstream tags have different bit rates "
                    f"{sorted(rates)} -- using {bit_rate} for its decoder.", 7000)

        if bit_rate is not None:
            if (rx_item.type_name in RX_ALREADY_AT_BIT_RATE
                    and rx_item.params.get("bit_rate") != bit_rate):
                rx_item.params["bit_rate"] = bit_rate
                changed = True
                if notify:
                    self.status.showMessage(
                        f"Auto-set {rx_item.node_id}'s own bit rate to {bit_rate} to "
                        "match its tag(s) -- it had drifted from a stale/default value.",
                        7000)

            decoder_bit_rate = bit_rate
            decoder_samp_rate = (bit_rate if rx_item.type_name in RX_ALREADY_AT_BIT_RATE
                                  else SAMP_RATE)
            if (rx_item.decoder_params.get("bit_rate") != decoder_bit_rate
                    or rx_item.decoder_params.get("samp_rate") != decoder_samp_rate):
                rx_item.decoder_params["bit_rate"] = decoder_bit_rate
                rx_item.decoder_params["samp_rate"] = decoder_samp_rate
                changed = True

        if notify and changed and self.inspector.current_node is rx_item:
            self.inspector.show_node(rx_item)

    def scene_to_topology(self):
        nodes = []
        node_dicts = {}  # node_id -> the dict just appended to `nodes`,
                          # so the receivers loop below can attach
                          # "payload_fragments" to a text-mode tag's
                          # ALREADY-built dict instead of a second pass
                          # over self.nodes.
        for node_id, item in self.nodes.items():
            n = {"id": node_id, "type": item.type_name, "params": dict(item.params)}
            if item.payload is not None:
                n["payload"] = list(item.payload)
            nodes.append(n)
            node_dicts[node_id] = n

        # Every sender->tag_* or tag_*->rx_* edge IS a channel now (see
        # EdgeItem.role/channel_params): auto-create and wire that edge's
        # own "channel" node here, same auto-wiring precedent as the
        # Tx_Leakage/decoder wiring below. An OLD saved layout's plain
        # edges (role/channel_params None) just pass through unchanged
        # in the else branch. Interference_In is left unconnected --
        # plan_topology() zero-sources it on its own. channel_counter
        # guarantees a unique id per EDGE even on fan-in into one port.
        edges = []
        channel_counter = 0
        for e in self.edges:
            if e.role is None:
                edges.append((e.from_node.node_id, e.from_port_name,
                              e.to_node.node_id, e.to_port_name))
                continue
            channel_counter += 1
            prefix = "fwd_channel" if e.role == "forward" else "ret_channel"
            channel_id = f"{prefix}_{e.to_node.node_id if e.role == 'forward' else e.from_node.node_id}_{channel_counter}"
            # Remembered on the edge itself (see EdgeItem.live_channel_id's
            # own comment) so "View signal" on THIS edge's channel can
            # find it in a running TopologyMiddleware.nodes later --
            # this id is only ever meaningful for the run this call is
            # currently building, which is exactly when on_start() calls
            # this method.
            e.live_channel_id = channel_id
            nodes.append({"id": channel_id, "type": "channel", "params": dict(e.channel_params)})
            edges.append((e.from_node.node_id, e.from_port_name, channel_id, "Signal_In"))
            edges.append((channel_id, "out", e.to_node.node_id, e.to_port_name))

        # Auto-wire every sender's Tx_Leakage_Out to every receiver's
        # Tx_Leakage_In -- see AUTO_WIRED_PORTS. BackscatterMiddleware/
        # build_fixed_topology connect this unconditionally themselves.
        #
        # Readers (add_reader()) are re-scoped: a reader's sender is a
        # dedicated co-located antenna for its own rx only -- wires ONLY
        # to that paired rx, never to any other receiver, and its rx
        # never sees leakage from any other sender (two readers are two
        # physically distinct devices). Standalone sender/receivers
        # (reader_partner is None) keep the old all-pairs behavior.
        senders = [n for n in self.nodes.values() if n.type_name == "sender"]
        receivers = [n for n in self.nodes.values() if n.type_name.startswith("rx_")]
        standalone_receivers = [n for n in receivers if n.reader_partner is None]
        for sender in senders:
            if sender.reader_partner is not None:
                edges.append((sender.node_id, "Tx_Leakage_Out",
                              sender.reader_partner.node_id, "Tx_Leakage_In"))
                continue
            for rx in standalone_receivers:
                edges.append((sender.node_id, "Tx_Leakage_Out", rx.node_id, "Tx_Leakage_In"))

        # Auto-create and wire one packet_hex_decoder per receiver,
        # using THAT receiver's own decoder_params (see NodeItem's
        # comment: not a draggable block, not a global/shared setting --
        # see PLACEABLE_NODE_TYPES' comment for the former). Same
        # auto-wiring precedent as Tx_Leakage. TopologyMiddleware now
        # gives each packet_hex_decoder instance it builds its OWN
        # queue, keyed by this exact decoder_id, and tags every packet
        # get_packets() returns with the decoder_id it came from -- see
        # that class's own comment -- which is what lets poll_packets()
        # below route a decoded packet to the right receiver's own tab
        # instead of one merged table no one could attribute.
        for rx in receivers:
            upstream_tags = self._find_upstream_tags(rx)
            # Derives/corrects payload_len_bits, bit_rate and samp_rate
            # (and, for FM0/Miller, the receiver's own bit_rate param)
            # from these tags -- see _sync_decoder_params' docstring.
            # Snapshotting decoder_params AFTER this call (not before)
            # is what makes sure what actually gets built here is never
            # a stale value the inspector just hadn't refreshed yet.
            self._sync_decoder_params(rx, upstream_tags)

            decoder_id = f"decoder_{rx.node_id}"
            params = dict(rx.decoder_params)
            nodes.append({"id": decoder_id, "type": "packet_hex_decoder", "params": params})
            edges.append((rx.node_id, "Data_Out", decoder_id, "Data_In"))

            # Text/file-mode tags (NodeItem.payload_mode/message_framer.py):
            # build each one's multi-fragment bitstream now that upstream_tags
            # for THIS receiver is known. tag_id is just this tag's index
            # among the receiver's own upstream tags (0..N-1) -- unique only
            # within this receiver's reassembly, which is all
            # FragmentReassembler needs. Overwrites the dict node_dicts
            # already built for this tag. Raw-mode tags are untouched. File
            # mode uses the already-read file_bytes, never re-reads disk.
            for tag_id, tag in enumerate(upstream_tags):
                if tag.payload_mode == "text":
                    fragments = message_framer.build_fragments(
                        tag.message_text, tag_id, tag.chunk_bytes)
                    node_dicts[tag.node_id]["payload_fragments"] = fragments
                elif tag.payload_mode == "file":
                    fragments = message_framer.build_fragments_from_bytes(
                        tag.file_bytes, tag_id, tag.chunk_bytes)
                    node_dicts[tag.node_id]["payload_fragments"] = fragments

        return nodes, edges

    # --- save/load: a full canvas layout (nodes, positions, params,
    # payloads, edges) as JSON -- the thing that was "bothersome to drag
    # and drop every time" before this. Tx_Leakage/Data_In edges are
    # never saved since they're auto-wired fresh every time regardless
    # (see scene_to_topology()) -- the edges list here is only what the
    # user actually drew. ---

    def _serialize_layout(self):
        nodes = []
        for node_id, item in self.nodes.items():
            center = item.pos() + QtCore.QPointF(item.rect().width() / 2, item.rect().height() / 2)
            n = {
                "id": node_id, "type": item.type_name,
                "x": center.x(), "y": center.y(),
                "params": dict(item.params),
                "order_seq": item.order_seq,
            }
            if item.payload is not None:
                n["payload"] = list(item.payload)
            if item.decoder_params is not None:
                n["decoder_params"] = dict(item.decoder_params)
            if item.payload_mode is not None:
                n["payload_mode"] = item.payload_mode
                n["message_text"] = item.message_text
                n["chunk_bytes"] = item.chunk_bytes
                # Saved as hex (JSON has no byte-string type) so a
                # "File" mode tag's chosen file round-trips through
                # Save/Load same as message_text does for "Text
                # message" -- without this, reloading a saved layout
                # would silently drop back to an empty file (chosen
                # again by hand) every time, same bug class as the
                # payload_mode-carryover fix message_text/chunk_bytes
                # already got.
                n["file_name"] = item.file_name
                n["file_bytes_hex"] = (item.file_bytes or b"").hex()
                n["packets_per_wake"] = item.packets_per_wake
            if item.energy_per_bit is not None:
                n["energy_per_bit"] = item.energy_per_bit
                n["energy_switch_mode"] = item.energy_switch_mode
                n["energy_c_gate_fF"] = item.energy_c_gate_fF
                n["energy_vdd"] = item.energy_vdd
                n["energy_r_bias_ohm"] = item.energy_r_bias_ohm
                n["energy_duty_cycle"] = item.energy_duty_cycle
                n["energy_static_rate"] = item.energy_static_rate
            if item.type_name.startswith("rx_"):
                # decode_snr_threshold_db is receiver-owned (standalone
                # rx_* node, or a reader's hidden rx half -- which gets
                # its own node_id/entry in this same loop, e.g.
                # "reader1_rx" -- so no special-casing needed here for
                # the reader case). This IS saved, unlike show_decode_
                # range: it's a calibration value the user sets
                # deliberately against their own measured BER, not a
                # view toggle -- losing it on every Save/Load would
                # throw away real experiment setup.
                n["decode_snr_threshold_db"] = item.decode_snr_threshold_db
            if item.reader_partner is not None:
                # See add_reader()/NodeItem.reader_partner -- just the
                # OTHER node's id. The hidden rx half's position is
                # saved the same way as any other node's (pos() works
                # fine on an item that was never added to the scene), but
                # it's cosmetic bookkeeping only now -- nothing on screen
                # ever reads it, since the rx half never draws and can't
                # be dragged (see add_node()'s add_to_scene param).
                n["reader_partner"] = item.reader_partner.node_id
            nodes.append(n)
        # Each edge saves as a plain [from, from_port, to, to_port] list
        # UNLESS it carries a channel (EdgeItem.role/channel_params), in
        # which case it's the dict form below -- so an edited distance/
        # fading/noise value round-trips through Save/Load.
        # _load_layout_dict accepts both shapes, so an OLD saved layout
        # (every edge a plain list) still loads unchanged.
        #
        # A reader's hidden shadow return edge is deliberately skipped
        # here -- it's always reconstructible from reader_partner plus
        # the tag's forward edge, and re-saving it would recreate it a
        # second time on Load, this time as a visible arrow (a plain
        # add_edge() on reload has no way to know it should stay hidden).
        edges = []
        for e in self.edges:
            if e.is_reader_shadow:
                continue
            if e.channel_params is not None:
                edges.append({
                    "from": e.from_node.node_id, "from_port": e.from_port_name,
                    "to": e.to_node.node_id, "to_port": e.to_port_name,
                    "channel_params": dict(e.channel_params),
                })
            else:
                edges.append([e.from_node.node_id, e.from_port_name,
                              e.to_node.node_id, e.to_port_name])
        return {"nodes": nodes, "edges": edges}

    def _load_layout_dict(self, data):
        self.view.scene().clear()
        self.nodes = {}
        self.edges = []
        self.wake_range_items = []  # scene.clear() already deleted these Qt objects
        self._node_counter = defaultdict(int)
        self.ber_vs_snr_points = defaultdict(list)
        self.inspector.show_node(None)

        for n in data.get("nodes", []):
            # A reader's visible sender half uses its own "reader" icon
            # (see ICON_DIR's comment/add_reader()) -- known straight
            # from THIS node's own saved dict (its "reader_partner" key,
            # written by _serialize_layout()), no need to wait for the
            # link-restoration pass below. A plain sender has no
            # "reader_partner" key at all and keeps icon_type_name=None,
            # same as every other ordinary node.
            icon_type_name = (
                "reader" if n["type"] == "sender" and n.get("reader_partner") else None)
            self.add_node(
                n["type"], QtCore.QPointF(n["x"], n["y"]), node_id=n["id"],
                params=n.get("params"), payload=n.get("payload"),
                decoder_params=n.get("decoder_params"),
                payload_mode=n.get("payload_mode"), message_text=n.get("message_text"),
                chunk_bytes=n.get("chunk_bytes"), file_name=n.get("file_name"),
                file_bytes=(bytes.fromhex(n["file_bytes_hex"])
                            if n.get("file_bytes_hex") else None),
                # Missing on any layout saved before this existed -- None
                # falls back to add_node()'s normal fresh-allocation path,
                # which just means tag_id assignment for an old layout is
                # keyed off node LOAD order instead of original PLACEMENT
                # order (the two already coincide for anything that's
                # never been through a modulation switch, which is every
                # pre-existing saved layout).
                order_seq=n.get("order_seq"),
                # Missing on any layout saved before this existed --
                # None falls back to add_node()'s own fresh-tag default
                # (3), same reasoning as order_seq just above.
                packets_per_wake=n.get("packets_per_wake"),
                energy_per_bit=n.get("energy_per_bit"),
                energy_switch_mode=n.get("energy_switch_mode"),
                energy_c_gate_fF=n.get("energy_c_gate_fF"),
                energy_vdd=n.get("energy_vdd"),
                energy_r_bias_ohm=n.get("energy_r_bias_ohm"),
                energy_duty_cycle=n.get("energy_duty_cycle"),
                energy_static_rate=n.get("energy_static_rate"),
                decode_snr_threshold_db=n.get("decode_snr_threshold_db"),
                icon_type_name=icon_type_name,
            )

        # Restore combined-reader links (see add_reader()/NodeItem.
        # reader_partner) now that every node above has its real, final
        # position -- BEFORE the edges loop below, since add_edge()'s
        # own reader handling (auto-creating a tag's hidden shadow
        # return edge) only fires when a forward edge's from_node already
        # has reader_partner set. Missing entirely on any layout saved
        # before readers existed, same None-falls-through-harmlessly
        # pattern as order_seq above.
        for n in data.get("nodes", []):
            partner_id = n.get("reader_partner")
            if partner_id and n["id"] in self.nodes and partner_id in self.nodes:
                a, b = self.nodes[n["id"]], self.nodes[partner_id]
                a.reader_partner = b
                b.reader_partner = a
                # The loop above added every node via add_node()'s
                # default add_to_scene=True, so the hidden rx half just
                # got drawn same as any other node. Pull it back out now
                # that we know which of the pair it is -- a reader's
                # hidden rx is always type_name.startswith("rx_") (its
                # visible sender partner never is), matching the
                # add_to_scene=False add_reader() itself uses when
                # creating one fresh.
                for item in (a, b):
                    if item.type_name.startswith("rx_") and item.scene() is not None:
                        self.view.scene().removeItem(item)

        for entry in data.get("edges", []):
            if isinstance(entry, dict):
                from_id, from_port = entry["from"], entry["from_port"]
                to_id, to_port = entry["to"], entry["to_port"]
                channel_params = entry.get("channel_params")
            else:
                from_id, from_port, to_id, to_port = entry
                channel_params = None
            if from_id in self.nodes and to_id in self.nodes:
                edge = self.add_edge(self.nodes[from_id], from_port, self.nodes[to_id], to_port)
                if edge is not None and channel_params and edge.channel_params is not None:
                    edge.channel_params.update(channel_params)

    def _confirm_discard_if_nonempty(self):
        if not self.nodes:
            return True
        reply = QtWidgets.QMessageBox.question(
            self, "Replace current canvas?",
            "This will clear everything currently on the canvas. Continue?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        return reply == QtWidgets.QMessageBox.StandardButton.Yes

    def on_save_as(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save layout", "", "Layout files (*.json)")
        if not path:
            return
        with open(path, "w") as f:
            json.dump(self._serialize_layout(), f, indent=2)
        self.status.showMessage(f"Saved layout to {path}", 4000)

    def on_load(self):
        if self.mw is not None:
            self.status.showMessage("Stop the running topology before loading a new layout.", 4000)
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load layout", "", "Layout files (*.json)")
        if not path:
            return
        if not self._confirm_discard_if_nonempty():
            return
        try:
            with open(path) as f:
                self._load_layout_dict(json.load(f))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Couldn't load layout", str(e))
            return
        self.status.showMessage(f"Loaded layout from {path}", 4000)

    def on_save_as_default(self):
        with open(DEFAULT_LAYOUT_PATH, "w") as f:
            json.dump(self._serialize_layout(), f, indent=2)
        self.status.showMessage(f"Saved as default layout ({DEFAULT_LAYOUT_PATH})", 4000)

    # --- toolbar actions ---

    def on_validate(self):
        nodes, edges = self.scene_to_topology()
        try:
            plan = plan_topology(nodes, edges)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Topology problem", str(e))
            return

        problems = _check_known_requirements(nodes, edges)
        if problems:
            QtWidgets.QMessageBox.warning(self, "Topology problem", "\n\n".join(problems))
            return

        QtWidgets.QMessageBox.information(
            self, "Topology OK",
            f"{len(nodes)} node(s), {len(edges)} connection(s), "
            f"{len(plan['fanin_groups'])} fan-in group(s) -- wiring resolves cleanly.\n\n"
            "Note: this checks that every named port exists and resolves to a real "
            "position, plus the one specific known trap in _check_known_requirements() "
            "(every rx_*'s Tx_Leakage_In). It does NOT check that every OTHER required "
            "input has something feeding it, or that connected ports agree on data "
            "type (complex/byte/float) -- those still only surface on Start, from "
            "gnuradio's own flowgraph validation."
        )

    def on_start(self):
        nodes, edges = self.scene_to_topology()
        problems = _check_known_requirements(nodes, edges)
        if problems:
            QtWidgets.QMessageBox.warning(self, "Can't start", "\n\n".join(problems))
            return
        try:
            self.mw = TopologyMiddleware(nodes, edges)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Can't start", str(e))
            self.mw = None
            return
        self._rebuild_decoder_docks()
        # Connects every ticked "View signal" tap BEFORE mw.start() --
        # see _open_pending_signal_views()'s own docstring for why that
        # ordering is what actually fixes the freeze/stutter opening one
        # mid-run used to cause.
        self._open_pending_signal_views()
        self.mw.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.validate_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.timer.start()
        # Picks up both the newly-live mw (buttons that were disabled can
        # now be clicked) and, for a channel edge, the live_channel_id
        # scene_to_topology() just assigned above -- see
        # NodeInspector.refresh_after_run_state_change()'s own docstring.
        self.inspector.refresh_after_run_state_change()

    def on_stop(self):
        self.timer.stop()
        # Force-close the signal-view window BEFORE mw.stop() -- its
        # closeEvent tears down every probe it holds via SignalProbe.
        # teardown(), which does its own tb.lock()/disconnect()/
        # tb.unlock() against the STILL-RUNNING flowgraph. Doing this
        # after mw.stop() would mean teardown() locking/disconnecting a
        # top_block that's already torn down, which is both pointless
        # and not a sequence GNU Radio's reconfiguration API is meant to
        # be used in.
        if self._signal_view_window is not None:
            self._signal_view_window.close()
        if self.mw is not None:
            self.mw.stop()
            self.mw = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.validate_button.setEnabled(True)
        self.clear_button.setEnabled(True)
        self.inspector.refresh_after_run_state_change()

    def _open_pending_signal_views(self):
        """Opens ONE SignalViewWindow holding every output port ticked in
        the inspector (NodeItem/EdgeItem.signal_probe_ports) as a stacked
        row apiece -- one window, not a popup per tick. Called from
        on_start() right after self.mw is built but BEFORE self.mw.start().

        GOTCHA: that ordering matters -- tapping on-demand while already
        running calls SignalProbe's tb.lock()/connect()/tb.unlock(), and
        locking a RUNNING top_block pauses its scheduler, which showed up
        as the sim stuttering on open. Tapping here, before start(), means
        lock()/unlock() only ever hit a not-yet-scheduling flowgraph.
        self.mw.nodes is already populated at this point, so connecting
        here is both possible and safe. No-op if nothing is ticked."""
        pending = []  # (node_id, type_name, port_name, title)
        for node_item in self.nodes.values():
            for port_name in sorted(node_item.signal_probe_ports):
                pending.append((node_item.node_id, node_item.type_name, port_name,
                                 f"{node_item.node_id} / {port_name}"))
        for edge_item in self.edges:
            if edge_item.live_channel_id and "out" in edge_item.signal_probe_ports:
                title = (f"channel {edge_item.from_node.node_id}"
                         f"→{edge_item.to_node.node_id} / out")
                pending.append((edge_item.live_channel_id, "channel", "out", title))
        if not pending:
            return

        win = SignalViewWindow(on_closed=self._on_signal_view_closed)
        opened_any = False
        for node_id, type_name, port_name, title in pending:
            if self.mw is None or node_id not in self.mw.nodes:
                continue  # defensive -- self.mw is always set by the time this runs
            try:
                probe = SignalProbe(self.mw, node_id, type_name, port_name)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Can't view signal", f"{title}: {e}")
                continue
            win.add_probe(title, probe)
            opened_any = True
        if opened_any:
            self._signal_view_window = win
            win.show()

    def _on_signal_view_closed(self, win):
        """SignalViewWindow's on_closed callback -- clears the tracked
        reference regardless of which side closed it first (the user
        closing the window, or on_stop() force-closing it)."""
        if self._signal_view_window is win:
            self._signal_view_window = None

    def on_clear(self):
        if self.mw is not None:
            return
        self.view.scene().clear()
        self.nodes = {}
        self.edges = []
        self.wake_range_items = []  # scene.clear() already deleted these Qt objects
        self._node_counter = defaultdict(int)
        self.ber_vs_snr_points = defaultdict(list)
        self.inspector.show_node(None)

    def _rebuild_decoder_docks(self):
        """(Re)builds one QDockWidget per receiver on the canvas, each
        holding that receiver's decoded-packet table, replacing whatever
        the previous run left behind. Called from on_start() only -- not
        kept in sync incrementally, since the decoder_id naming
        (f"decoder_{rx.node_id}") is only meaningful for an actual run.

        Docks rather than a plain QTabWidget: Qt tabifies docks in the
        same area into an ordinary-looking tab bar (tabifyDockWidget()
        below), but each tab can also be dragged off into its own
        floating window and back -- a free "pop one out" feature with no
        custom detachable-tab code needed.

        Each dock's widget holds TWO tables: the per-fragment log, and a
        "Reassembled messages" table fed by pkt["message"] in
        poll_packets() -- stays empty for a raw-bits receiver with no
        text-mode tag feeding it."""
        for dock in self.decoder_docks.values():
            self.removeDockWidget(dock)
            dock.deleteLater()
        self.decoder_docks = {}
        self.decoder_tables = {}
        self.message_tables = {}
        # Cumulative bit-error-rate bookkeeping, per decoder_id --
        # {"errors": int, "bits": int} -- reset here (a fresh Start),
        # accumulated across polls in poll_packets()/_compute_packet_
        # ber(). A running total across many packets is what's actually
        # useful for a BER figure (a single packet's ratio is too noisy
        # to mean much on its own), same reasoning as the energy
        # estimate wanting a stable number rather than an instant one.
        self.decoder_ber_stats = {}
        self.decoder_ber_labels = {}
        self.decoder_preambles = {}

        receivers = [n for n in self.nodes.values() if n.type_name.startswith("rx_")]
        prev_dock = None
        for rx in receivers:
            decoder_id = f"decoder_{rx.node_id}"
            self.decoder_ber_stats[decoder_id] = {"errors": 0, "bits": 0}

            table = QtWidgets.QTableWidget(0, 7)
            table.setHorizontalHeaderLabels(
                ["Timestamp", "Bits", "Preamble", "Sim Info", "Hex", "ASCII", "BER"])
            table.horizontalHeader().setStretchLastSection(True)
            table.horizontalHeaderItem(2).setToolTip(
                "The preamble this row's packet synced on. Not independently "
                "decoded per row -- the decoder only starts collecting a packet "
                "on an EXACT match against this receiver's configured preamble "
                "(see the SEARCHING-state check in packet_hex_decoder_epy_"
                "block_0.py), so every row here is guaranteed, by construction, "
                "to have matched exactly this value -- there'd be no row at all "
                "otherwise. Change it on this receiver's Decoder settings."
            )
            table.horizontalHeaderItem(6).setToolTip(
                "This packet's bit errors vs. the exact bits this tag actually "
                "transmitted (TopologyMiddleware.tag_frame_bits) -- '--' when the "
                "source tag can't be identified (e.g. several raw-bits tags "
                "feeding one receiver with no per-packet tag id to disambiguate). "
                "See the cumulative figure above the table for a stable number -- "
                "any single packet's ratio is noisy on its own."
            )
            # "Sim Info" is the simulator's own tag/fragment bookkeeping
            # (message_framer.py's HEADER_BYTES), decoded and labeled
            # here instead of sitting as unexplained raw bytes inside
            # Hex/ASCII -- those two columns now show ONLY the chunk
            # (what the user actually typed/chose), via pkt["payload_
            # hex"]/["payload_ascii"] (see TopologyMiddleware.
            # get_packets()). Blank for a raw/non-framed tag's packets,
            # which have no header at all.
            table.horizontalHeaderItem(2).setToolTip(
                "Added by the simulation to track which tag/fragment this is -- "
                "not part of your message. Blank for a raw-bits tag (no framing)."
            )

            message_table = QtWidgets.QTableWidget(0, 3)
            message_table.setHorizontalHeaderLabels(["Tag", "Bytes", "Message (text)"])
            message_table.horizontalHeader().setStretchLastSection(True)
            # "Save message as file..." right-click -- see poll_packets()'s
            # UserRole data on column 0 and _show_message_table_context_
            # menu()/_save_message_bytes() below. rx.node_id (not the
            # decoder_id) is what's threaded through, since that's what
            # _find_upstream_tags() takes.
            message_table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            message_table.customContextMenuRequested.connect(
                lambda pos, t=message_table, rid=rx.node_id:
                    self._show_message_table_context_menu(t, rid, pos))
            # No setMaximumHeight() any more -- that's what made this
            # table permanently tiny and un-resizable (reported: "the
            # reassembled msg component is too small in height, only
            # fragment part is resizable"). A QSplitter between the two
            # panes instead gives an actual drag handle, same as any
            # other split view, with no hard ceiling on either side.

            # The RF-sync preamble itself never shows up anywhere in the
            # table below -- confirmed straight from the compiled
            # blocks: packet_hex_decoder's own state machine only starts
            # collecting bits (and only ever dispatches what it collects)
            # AFTER its shift register matches self.preamble, and that
            # match is consumed as a trigger, never forwarded into
            # packet_data. FM0/Miller's own sync block re-emits the
            # preamble into ITS output for exactly the same reason
            # (packet_hex_decoder's SEARCHING state re-detects it there
            # too) -- same story, preamble in, never preamble out. So
            # there's no PER-ROW decode happening (the Preamble table
            # column below isn't read off each packet individually) --
            # what IS true, and shown there, is the value this receiver
            # is configured to hunt for, which every row is implicit,
            # guaranteed proof of: an exact-match requirement means a
            # row with a DIFFERENT preamble could never have existed.
            synced_preamble = rx.decoder_params.get("preamble") or PREAMBLE
            self.decoder_preambles[decoder_id] = synced_preamble
            preamble_sync_label = QtWidgets.QLabel(f"Synced on preamble: {synced_preamble}")
            preamble_sync_label.setStyleSheet("color: #666666; font-style: italic;")
            preamble_sync_label.setToolTip(
                "This receiver's decoder only starts collecting a packet once its "
                "incoming bits match this exact pattern -- the match itself is "
                "consumed as a sync trigger and never forwarded into the Hex/ASCII "
                "below, so there's no per-row preamble to show. Every row in this "
                "table exists only because this pattern was detected first. "
                "Change it on this receiver's Decoder settings in the Inspector."
            )

            ber_label = QtWidgets.QLabel("Cumulative BER: no packets decoded yet")
            ber_label.setStyleSheet("font-weight: 600;")
            ber_label.setToolTip(
                "Running total across every packet decoded this run whose source "
                "tag could be identified -- bit errors / bits compared, vs. the "
                "exact bits that tag actually transmitted. More stable than any "
                "single row's own BER column."
            )
            self.decoder_ber_labels[decoder_id] = ber_label

            # "Export CSV..." -- Geethika's exact request: the only
            # configurable option is the row range, nothing else (no
            # column picker, no format choice). Row numbers are
            # 1-indexed against this table's CURRENTLY DISPLAYED rows,
            # which is the only range that exists -- MAX_DECODER_ROWS
            # evicts the oldest row once the table passes 500, so a
            # "line 1" from an hour ago may no longer be here to
            # export; _export_table_csv()'s dialog caps the range to
            # whatever's actually still in the table right now.
            export_fragments_button = QtWidgets.QPushButton("Export CSV...")
            export_fragments_button.setToolTip(
                "Export a row range from this table (as currently shown) to a "
                "CSV file, same columns as above. Rows older than the "
                f"{self.MAX_DECODER_ROWS}-row cap have already been dropped "
                "and can't be recovered."
            )
            export_fragments_button.clicked.connect(
                lambda _checked=False, t=table, stem=f"{rx.node_id}_fragments":
                    self._export_table_csv(t, stem))

            # "Capture point" / "Show BER vs SNR Plot" -- the in-app
            # replacement for the earlier CSV+JSON-config workflow
            # ("nobody has time to prepare jsons"). Capture grabs THIS
            # receiver's current cumulative BER (self.decoder_ber_stats,
            # already being accumulated every poll) plus the real SNR
            # for its upstream tag's CURRENT link settings (same formula
            # _update_wake_ranges()'s decode-range ring already draws),
            # and appends one point to self.ber_vs_snr_points[rx.node_id]
            # -- a list that survives Start/Stop/Start (see that dict's
            # own comment in __init__), so stop, change distance, Start
            # again, Capture again builds up a real multi-run curve.
            capture_point_button = QtWidgets.QPushButton("Capture point")
            capture_point_button.setToolTip(
                "Records this receiver's current cumulative BER, and the real "
                "SNR for its tag's current settings, as one point on the BER "
                "vs SNR plot below. Only works with exactly one tag feeding "
                "this receiver (ambiguous otherwise -- same restriction the "
                "BER column itself already has for raw-mode fan-in)."
            )
            capture_point_button.clicked.connect(
                lambda _checked=False, rid=rx.node_id: self._capture_ber_snr_point(rid))

            show_plot_button = QtWidgets.QPushButton("Show BER vs SNR Plot")
            show_plot_button.setToolTip(
                "Opens the theoretical BER-vs-SNR curves plus every point "
                "captured so far for this receiver (across any number of "
                "Start/Stop/Start cycles). Re-open after capturing a new "
                "point to see it added."
            )
            show_plot_button.clicked.connect(
                lambda _checked=False, rid=rx.node_id: self._show_ber_vs_snr_plot(rid))

            export_row = QtWidgets.QHBoxLayout()
            export_row.addStretch(1)
            export_row.addWidget(capture_point_button)
            export_row.addWidget(show_plot_button)
            export_row.addWidget(export_fragments_button)

            fragments_pane = QtWidgets.QWidget()
            fragments_layout = QtWidgets.QVBoxLayout(fragments_pane)
            fragments_layout.setContentsMargins(0, 0, 0, 0)
            fragments_layout.addWidget(QtWidgets.QLabel("Fragments"))
            fragments_layout.addWidget(preamble_sync_label)
            fragments_layout.addWidget(ber_label)
            fragments_layout.addWidget(table)
            fragments_layout.addLayout(export_row)

            messages_pane = QtWidgets.QWidget()
            messages_layout = QtWidgets.QVBoxLayout(messages_pane)
            messages_layout.setContentsMargins(0, 0, 0, 0)
            messages_layout.addWidget(QtWidgets.QLabel("Reassembled messages"))
            messages_layout.addWidget(message_table)

            splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
            splitter.addWidget(fragments_pane)
            splitter.addWidget(messages_pane)
            # Fragments pane starts bigger (it's normally the busier of
            # the two), but both are freely draggable from here on --
            # stretch factors just set the INITIAL split, not a limit.
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 1)

            container = QtWidgets.QWidget()
            container_layout = QtWidgets.QVBoxLayout(container)
            container_layout.setContentsMargins(2, 2, 2, 2)
            container_layout.addWidget(splitter)

            dock = QtWidgets.QDockWidget(rx.node_id, self)
            dock.setObjectName(f"decoder_dock_{decoder_id}")
            dock.setWidget(container)
            dock.setFeatures(
                QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable
            )
            self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, dock)
            if prev_dock is not None:
                self.tabifyDockWidget(prev_dock, dock)
            prev_dock = dock

            self.decoder_docks[decoder_id] = dock
            self.decoder_tables[decoder_id] = table
            self.message_tables[decoder_id] = message_table

        if receivers:
            # Tabified docks default to showing the LAST one added as the
            # active tab -- raise() the first receiver's instead, so the
            # active tab on a fresh Start is predictable rather than
            # whatever order dict iteration happened to produce.
            self.decoder_docks[f"decoder_{receivers[0].node_id}"].raise_()

    def _compute_packet_ber(self, decoder_id, pkt):
        """Bit-error count for one decoded packet, against the EXACT
        bits that tag actually transmitted this run (TopologyMiddleware.
        tag_frame_bits -- see plan_topology()'s own docstring: the same
        bits used to build that tag's vector_source_b in the first
        place, not a fresh re-derivation that could drift from it).

        Returns (n_wrong, n_bits), or None when there isn't enough
        information to compare -- no running sim, no dock for this
        decoder, or (raw/non-framed packets only) more than one
        upstream tag feeding this receiver with nothing in the packet
        itself to say which one sent it. A framed packet always
        carries its own sim_header['tag_id'], so fan-in is never
        ambiguous there.

        No Tier-1 block involved or changed: packet_hex_decoder already
        emits everything needed (hex + the pre-padding bit count) to
        reconstruct the exact decoded bit sequence -- see
        _hex_str_to_bits()."""
        if self.mw is None:
            return None
        rx_node_id = decoder_id[len("decoder_"):] if decoder_id.startswith("decoder_") else None
        rx_item = self.nodes.get(rx_node_id) if rx_node_id else None
        if rx_item is None:
            return None

        sim_header = pkt.get("sim_header")
        tag_items = self._find_upstream_tags(rx_item)
        if sim_header is not None:
            idx = sim_header["tag_id"]
            if idx < 0 or idx >= len(tag_items):
                return None
            tag_node_id = tag_items[idx].node_id
        else:
            if len(tag_items) != 1:
                return None
            tag_node_id = tag_items[0].node_id

        frame_bits = self.mw.tag_frame_bits.get(tag_node_id)
        if not frame_bits:
            return None
        preamble_len = len(rx_item.decoder_params.get("preamble") or PREAMBLE)
        n_bits = pkt["bits"]

        if sim_header is not None:
            # Fixed-size fragments (every fragment for a given tag is
            # the same length -- see message_framer.fragment_len_bits()
            # and the "Hello"/"Geethika" regression check in
            # test_energy_estimate.py that depends on this same fact),
            # so pkt['bits'] IS that fixed per-fragment length and can
            # be used directly as the block size without importing
            # message_framer here at all.
            block_size = preamble_len + n_bits
            start = sim_header["frag_index"] * block_size + preamble_len
        else:
            start = preamble_len
        expected = frame_bits[start:start + n_bits]
        if len(expected) != n_bits or n_bits <= 0:
            return None

        decoded_bits = _hex_str_to_bits(pkt["hex"], n_bits)
        if len(decoded_bits) != n_bits:
            return None
        n_wrong = sum(1 for a, b in zip(expected, decoded_bits) if a != b)
        return n_wrong, n_bits

    def poll_packets(self):
        """Drains the backend (every DECODER_POLL_INTERVAL_MS, not per
        packet) and refreshes each receiver's table.

        GOTCHA: packets are grouped by destination table first, each
        batch inserted with repainting suspended (setUpdatesEnabled(False))
        and ONE scrollToBottom() at the end, with MAX_DECODER_ROWS capping
        row count -- a naive per-packet insertRow()/repaint caused
        hundreds of relayouts per poll on a busy decoder.

        A completed reassembled message (pkt["message"] set only on the
        fragment that completes it) goes into that receiver's separate
        "Reassembled messages" table, batched/capped the same way."""
        if self.mw is None:
            return
        by_table = defaultdict(list)
        by_message_table = defaultdict(list)
        for pkt in self.mw.get_packets():
            decoder_id = pkt.get("decoder_id")
            table = self.decoder_tables.get(decoder_id)
            if table is None:
                # No dock for this decoder_id -- shouldn't happen (docks
                # are rebuilt from the same receiver set on every Start),
                # but drop the packet rather than crash if it ever does.
                continue
            by_table[table].append(pkt)
            if pkt.get("message") is not None:
                message_table = self.message_tables.get(decoder_id)
                if message_table is not None:
                    by_message_table[message_table].append(pkt["message"])

        for table, pkts in by_table.items():
            table.setUpdatesEnabled(False)
            try:
                for pkt in pkts:
                    row = table.rowCount()
                    table.insertRow(row)
                    decoder_id = pkt.get("decoder_id")
                    table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"{pkt['timestamp']:.3f}"))
                    table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(pkt['bits'])))

                    # See this column's header tooltip (_rebuild_decoder_
                    # docks()): not decoded independently per row -- every
                    # row's packet is guaranteed, by the decoder's own
                    # exact-match requirement, to have synced on exactly
                    # this receiver's configured preamble.
                    preamble_str = self.decoder_preambles.get(decoder_id, "")
                    preamble_item = QtWidgets.QTableWidgetItem(preamble_str)
                    preamble_item.setForeground(QtGui.QColor("#888888"))
                    pfont = preamble_item.font()
                    pfont.setItalic(True)
                    preamble_item.setFont(pfont)
                    table.setItem(row, 2, preamble_item)

                    sim_header = pkt.get("sim_header")
                    sim_item = QtWidgets.QTableWidgetItem(_format_sim_header(sim_header))
                    # Styled distinctly (gray/italic) from the two payload
                    # columns next to it -- a glance should tell "this
                    # column isn't yours" without reading the tooltip.
                    sim_item.setForeground(QtGui.QColor("#888888"))
                    font = sim_item.font()
                    font.setItalic(True)
                    sim_item.setFont(font)
                    if sim_header is not None and not sim_header["crc_ok"]:
                        sim_item.setToolTip("This fragment failed its checksum -- dropped, not reassembled.")
                    table.setItem(row, 3, sim_item)
                    table.setItem(row, 4, QtWidgets.QTableWidgetItem(pkt.get("payload_hex", pkt["hex"])))
                    table.setItem(row, 5, QtWidgets.QTableWidgetItem(pkt.get("payload_ascii", pkt["ascii"])))

                    ber_result = self._compute_packet_ber(decoder_id, pkt) if decoder_id else None
                    if ber_result is None:
                        ber_item = QtWidgets.QTableWidgetItem("--")
                        ber_item.setForeground(QtGui.QColor("#888888"))
                    else:
                        n_wrong, n_cmp = ber_result
                        pct = 100.0 * n_wrong / n_cmp if n_cmp else 0.0
                        ber_item = QtWidgets.QTableWidgetItem(f"{n_wrong}/{n_cmp} ({pct:.2f}%)")
                        if n_wrong > 0:
                            ber_item.setForeground(QtGui.QColor("#c0392b"))
                        stats = self.decoder_ber_stats.setdefault(decoder_id, {"errors": 0, "bits": 0})
                        stats["errors"] += n_wrong
                        stats["bits"] += n_cmp
                        label = self.decoder_ber_labels.get(decoder_id)
                        if label is not None and stats["bits"] > 0:
                            cum_pct = 100.0 * stats["errors"] / stats["bits"]
                            label.setText(
                                f"Cumulative BER: {stats['errors']}/{stats['bits']} bits "
                                f"({cum_pct:.3f}%)")
                    table.setItem(row, 6, ber_item)
                overflow = table.rowCount() - self.MAX_DECODER_ROWS
                if overflow > 0:
                    for _ in range(overflow):
                        table.removeRow(0)
            finally:
                table.setUpdatesEnabled(True)
            table.scrollToBottom()

        for message_table, messages in by_message_table.items():
            message_table.setUpdatesEnabled(False)
            try:
                for msg in messages:
                    row = message_table.rowCount()
                    message_table.insertRow(row)
                    tag_item = QtWidgets.QTableWidgetItem(str(msg["tag_id"]))
                    # Raw bytes ride along as UserRole data on this
                    # item -- never shown, just carried so a later
                    # right-click ("Save message as file...", see
                    # _show_message_table_context_menu()) can write out
                    # the EXACT reassembled bytes, not whatever the
                    # "Message (text)" column's best-effort UTF-8
                    # decode produced. Works the same whether this
                    # message actually came from a text-mode or
                    # file-mode tag -- the data here is mode-agnostic,
                    # same as everything downstream of message_framer.
                    tag_item.setData(QtCore.Qt.ItemDataRole.UserRole, msg["bytes"])
                    message_table.setItem(row, 0, tag_item)
                    message_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(len(msg["bytes"]))))
                    # A file's bytes almost never decode as clean UTF-8
                    # -- showing the resulting replacement-character
                    # soup ("Message (text)" full of U+FFFD) reads as
                    # broken/garbled rather than "this is binary data,
                    # right-click to save it", so swap in a plain
                    # indicator whenever the decode wasn't clean,
                    # regardless of which payload mode produced it.
                    text = msg["text"]
                    display = text if "�" not in text else (
                        f"<binary data, {len(msg['bytes'])} byte(s) -- right-click to save>")
                    message_table.setItem(row, 2, QtWidgets.QTableWidgetItem(display))
                overflow = message_table.rowCount() - self.MAX_DECODER_ROWS
                if overflow > 0:
                    for _ in range(overflow):
                        message_table.removeRow(0)
            finally:
                message_table.setUpdatesEnabled(True)
            message_table.scrollToBottom()

    def _export_table_csv(self, table, default_stem):
        """Exports a user-chosen contiguous row range (1-indexed,
        inclusive, against this table's CURRENTLY DISPLAYED rows -- see
        the "Export CSV..." button's tooltip in _rebuild_decoder_docks()
        re: MAX_DECODER_ROWS eviction) to a CSV file. Row range is the
        ONLY configurable option, per Geethika's exact request -- no
        column picker, no delimiter choice.

        Reads cell TEXT only (.text()), not font/color -- the Preamble/
        Sim Info columns' gray-italic styling and the BER column's
        red-on-error styling are visual hints with no CSV equivalent
        anyway, so losing them isn't a loss of data.

        Written generically over any QTableWidget-with-headers (table,
        default_stem) rather than hardcoded to the Fragments table, so
        it's a one-line wire-up if the Reassembled-messages table (or
        any future table) ever wants the same button -- currently only
        wired to Fragments, since that's the one she asked for."""
        row_count = table.rowCount()
        if row_count == 0:
            QtWidgets.QMessageBox.information(
                self, "Export CSV", "This table has no rows to export yet.")
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Export CSV")
        form = QtWidgets.QFormLayout(dialog)
        from_spin = QtWidgets.QSpinBox()
        from_spin.setRange(1, row_count)
        from_spin.setValue(1)
        to_spin = QtWidgets.QSpinBox()
        to_spin.setRange(1, row_count)
        to_spin.setValue(row_count)
        form.addRow("From row:", from_spin)
        form.addRow("To row:", to_spin)
        note = QtWidgets.QLabel(f"{row_count} row(s) currently shown (rows 1-{row_count}).")
        note.setStyleSheet("color: #666666; font-style: italic;")
        form.addRow(note)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        start, end = from_spin.value(), to_spin.value()
        if start > end:
            start, end = end, start  # tolerate either order rather than erroring

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export CSV", f"{default_stem}.csv", "CSV files (*.csv)")
        if not path:
            return

        headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for row in range(start - 1, end):
                    row_texts = []
                    for col in range(table.columnCount()):
                        item = table.item(row, col)
                        row_texts.append(item.text() if item is not None else "")
                    writer.writerow(row_texts)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Export CSV", f"Couldn't write {path}: {e}")
            return
        self.status.showMessage(f"Exported rows {start}-{end} ({end - start + 1} row(s)) to {path}", 5000)

    def _resolve_decode_snr_db_for_tag(self, tag, rx):
        """Resolves every _decode_snr_db() param for tag's CURRENT link
        to rx, the exact same lookup _update_wake_ranges() already does
        to draw the decode-range ring (same forward/return edge search,
        same tag gamma0/1 params, same channel_params keys) -- so the
        number this returns is always the same SNR that ring would be
        drawn at for this tag/receiver pair, right now, nothing
        re-derived differently. Returns None if the tag isn't fully
        wired for the calculation yet (missing forward or return edge,
        that return edge not actually ending at `rx`, or any edge/tag
        param absent) -- same missing-data guard _update_wake_ranges()
        already uses before drawing."""
        fwd_edge = next((e for e in self.edges if e.role == "forward" and e.to_node is tag), None)
        if fwd_edge is None or fwd_edge.channel_params is None:
            return None
        sender = fwd_edge.from_node
        tx_power_send = sender.params.get("tx_power_send")
        fwd_freq = fwd_edge.channel_params.get("freq")
        fwd_g_reader_dbi = fwd_edge.channel_params.get("g_reader_dbi")
        fwd_g_tag_dbi = fwd_edge.channel_params.get("g_tag_dbi")
        fwd_distance_m = fwd_edge.channel_params.get("distance")

        ret_edge = next((e for e in self.edges if e.role == "return" and e.from_node is tag), None)
        if ret_edge is None or ret_edge.channel_params is None or ret_edge.to_node is not rx:
            return None
        ret_freq = ret_edge.channel_params.get("freq")
        ret_g_reader_dbi = ret_edge.channel_params.get("g_reader_dbi")
        ret_g_tag_dbi = ret_edge.channel_params.get("g_tag_dbi")
        ret_noise_volt_c1 = ret_edge.channel_params.get("noise_volt_c1")
        ret_distance_m = ret_edge.channel_params.get("distance")

        gamma0_mag = tag.params.get("gamma0_mag")
        gamma0_phase_deg = tag.params.get("gamma0_phase_deg")
        gamma1_mag = tag.params.get("gamma1_mag")
        gamma1_phase_deg = tag.params.get("gamma1_phase_deg")

        if (tx_power_send is None or not fwd_freq or fwd_distance_m is None
                or gamma0_mag is None or gamma1_mag is None
                or not ret_freq or ret_noise_volt_c1 is None or ret_distance_m is None):
            return None

        return _decode_snr_db(
            float(ret_distance_m), float(tx_power_send),
            float(fwd_freq), float(fwd_g_reader_dbi), float(fwd_g_tag_dbi),
            float(gamma0_mag), float(gamma0_phase_deg),
            float(gamma1_mag), float(gamma1_phase_deg),
            float(ret_freq), float(ret_g_reader_dbi), float(ret_g_tag_dbi),
            float(ret_noise_volt_c1),
            fwd_distance_m=float(fwd_distance_m))

    def _capture_ber_snr_point(self, rx_node_id):
        """"Capture point" button, Fragments pane -- the in-app
        replacement for exporting a CSV and hand-writing a JSON config
        just to get one measured (SNR, BER) point. Grabs this
        receiver's current cumulative BER (self.decoder_ber_stats,
        already accumulated by poll_packets()/_compute_packet_ber() --
        no CSV round-trip needed, it's already in memory) and the real
        SNR for its upstream tag's current settings (_resolve_decode_
        snr_db_for_tag(), same math the decode-range ring already
        draws), then appends one point to self.ber_vs_snr_points[rx_
        node_id] -- a list that survives Start/Stop/Start (see that
        dict's own comment in __init__), so Stop -> change distance ->
        Start -> Capture again builds a real curve across several runs
        rather than one run's single noisy point.

        Only supported for a receiver fed by EXACTLY ONE tag right now
        -- same ambiguity _compute_packet_ber() already refuses to
        guess through for raw-mode fan-in (the cumulative BER figure
        doesn't say which tag each error belongs to once more than one
        feeds the same receiver)."""
        rx = self.nodes.get(rx_node_id)
        if rx is None:
            return
        decoder_id = f"decoder_{rx_node_id}"
        stats = self.decoder_ber_stats.get(decoder_id)
        if not stats or stats["bits"] == 0:
            QtWidgets.QMessageBox.information(
                self, "Capture BER/SNR point",
                "No attributed packets decoded yet on this receiver -- run "
                "the sim (or let it run a bit longer) before capturing a point.")
            return
        tag_items = self._find_upstream_tags(rx)
        if len(tag_items) != 1:
            QtWidgets.QMessageBox.information(
                self, "Capture BER/SNR point",
                f"This receiver has {len(tag_items)} upstream tag(s) -- SNR "
                f"capture only works for exactly one. With more than one, the "
                f"cumulative BER figure mixes their errors together with no "
                f"way to say which tag's link the SNR number would even be "
                f"for.")
            return
        tag = tag_items[0]
        snr_db = self._resolve_decode_snr_db_for_tag(tag, rx)
        if snr_db is None:
            QtWidgets.QMessageBox.information(
                self, "Capture BER/SNR point",
                "This tag isn't fully wired for an SNR calculation yet -- "
                "needs a forward edge (from a sender) AND a return edge (to "
                "this receiver), both with their channel settings filled in.")
            return
        ber = stats["errors"] / stats["bits"]
        self.ber_vs_snr_points[rx_node_id].append({
            "snr_db": snr_db, "ber": ber,
            "n_wrong": stats["errors"], "n_bits": stats["bits"],
            "tag_id": tag.node_id,
        })
        self.status.showMessage(
            f"Captured point: SNR={snr_db:.2f} dB, BER={ber:.4g} "
            f"({stats['errors']}/{stats['bits']}) -- "
            f"{len(self.ber_vs_snr_points[rx_node_id])} point(s) so far for {rx_node_id}",
            6000)

    def _show_ber_vs_snr_plot(self, rx_node_id):
        """"Show BER vs SNR Plot" button, Fragments pane -- opens a
        plain matplotlib window (Geethika's own pick over an embedded
        panel: simpler, free pan/zoom/save) with the three theoretical
        curves (core.ber_theory.THEORETICAL_CURVES) plus every point
        captured so far for this receiver via _capture_ber_snr_point().
        A separate window, not tied to this dock -- capturing another
        point afterward doesn't live-update an already-open plot;
        re-click this button to see the new point on a fresh one."""
        try:
            import matplotlib
            matplotlib.use("QtAgg")
            import matplotlib.pyplot as plt
        except ImportError as e:
            QtWidgets.QMessageBox.warning(
                self, "Show BER vs SNR Plot",
                f"matplotlib isn't installed in this environment: {e}")
            return
        from core.ber_theory import THEORETICAL_CURVES

        points = self.ber_vs_snr_points.get(rx_node_id, [])
        snr_lo = min([-10.0] + [p["snr_db"] - 2 for p in points])
        snr_hi = max([20.0] + [p["snr_db"] + 2 for p in points])
        snr_range = [snr_lo + i * (snr_hi - snr_lo) / 399.0 for i in range(400)]

        fig, ax = plt.subplots(figsize=(7, 5.5))
        for _key, (label, fn) in THEORETICAL_CURVES.items():
            ax.semilogy(snr_range, [max(fn(s), 1e-300) for s in snr_range],
                        label=label, linewidth=1.3)
        if points:
            xs = [p["snr_db"] for p in points]
            ys = [max(p["ber"], 1e-300) for p in points]
            ax.semilogy(xs, ys, "o", markersize=8, markeredgecolor="black",
                        markeredgewidth=0.6,
                        label=f"Measured ({rx_node_id}, {len(points)} point(s))")
        else:
            ax.text(0.5, 0.5, "No points captured yet -- click \"Capture point\" after a run",
                    transform=ax.transAxes, ha="center", va="center", fontsize=9, color="gray")
        ax.set_xlabel("SNR (dB)")
        ax.set_ylabel("Bit Error Rate")
        ax.set_title(f"BER vs SNR -- {rx_node_id}")
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=8, loc="lower left")
        fig.tight_layout()
        plt.show(block=False)

    def _show_message_table_context_menu(self, message_table, rx_node_id, pos):
        """Right-click on a completed row in a receiver's "Reassembled
        messages" table -- offers to write that row's EXACT raw bytes
        (UserRole data on column 0, see poll_packets()) to a file, the
        natural receive-side counterpart to a tag's own "Choose File..."
        on the send side. Works for ANY completed row, not just ones
        that came from a file-mode tag -- a text message is just as
        saveable, it just defaults to a .txt-ish name instead of the
        original filename."""
        item = message_table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        tag_item = message_table.item(row, 0)
        data = tag_item.data(QtCore.Qt.ItemDataRole.UserRole)
        if data is None:
            return
        try:
            tag_id = int(tag_item.text())
        except ValueError:
            tag_id = None
        menu = QtWidgets.QMenu(message_table)
        action = menu.addAction("Save message as file...")
        if menu.exec(message_table.viewport().mapToGlobal(pos)) is action:
            self._save_message_bytes(data, rx_node_id, tag_id)

    def _save_message_bytes(self, data, rx_node_id, tag_id):
        """Writes `data` (a completed message's raw reassembled bytes)
        to a file the user picks. Defaults the save-dialog's filename
        to the ORIGINATING tag's own file_name when that tag is still
        on the canvas, still in file mode, and still has one chosen
        (resolved via _find_upstream_tags() the same way scene_to_
        topology() originally assigned this message's tag_id, so the
        lookup lines up with what actually sent it) -- falls back to a
        generic name otherwise (the tag was in text mode, has since
        been deleted/reconfigured, or tag_id couldn't be read back out
        of the table row)."""
        default_name = f"tag{tag_id}_message.bin" if tag_id is not None else "message.bin"
        rx = self.nodes.get(rx_node_id)
        if rx is not None and tag_id is not None:
            upstream_tags = self._find_upstream_tags(rx)
            if 0 <= tag_id < len(upstream_tags):
                origin = upstream_tags[tag_id]
                if origin.payload_mode == "file" and origin.file_name:
                    default_name = origin.file_name
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save message as file", default_name)
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Couldn't save file", str(e))
            return
        self.status.showMessage(f"Saved {len(data)} byte(s) to {path}", 4000)

    def closeEvent(self, event):
        self.on_stop()
        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = CanvasWindow()
    window.resize(1250, 780)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
