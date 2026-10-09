"""
Step 8 -- the first real slice of the canvas UI: a QGraphicsView-based
drag-and-drop node editor, wired to the SAME backend ui/main_window.py
already uses (registry.NODE_TYPES/Port/Param, plan_topology(),
TopologyMiddleware -- all from core.modulation_middleware /
core.registry, completely unchanged by this file). This is a new,
separate widget paradigm, not an evolution of main_window.py's
QFormLayout form -- that question got settled earlier: a form can't grow
into a free node graph, but the backend was kept UI-agnostic specifically
so a second, very different front end could be bolted onto it without
touching core/*.py at all. Nothing in core/ changed for this file.

WHAT WORKS in this slice:
  - A left-side palette (registry.NODE_TYPES, minus tag_analog/rx_analog
    -- see PLACEABLE_NODE_TYPES below) you drag onto the canvas; each
    drop creates a real node (auto-id'd "<type><n>", e.g. "tag_ook1").
  - Nodes are drawn as plain rectangles -- no icons/images, just the
    type name + id as text, colored by rough category (tag/rx/channel/
    sender/decoder) purely so same-category nodes are easy to tell apart
    at a glance. Input ports are small circles down the left edge,
    output ports down the right edge, each labeled with its real
    registry port name (e.g. "Carrier_In", "Tx_Leakage_Out") and a
    tooltip showing direction + dtype.
  - Wiring: click an OUTPUT port (orange), drag, release over an INPUT
    port (blue) to connect them. A dashed line follows the cursor while
    dragging; releasing anywhere else cancels. You don't have to land
    exactly on the dot -- both the press and the release snap to the
    nearest matching port within a small pixel radius (constant in
    screen space, so it feels the same at any zoom level; see
    CanvasView.SNAP_PIXELS/_port_near()), on top of each PortHandle's own
    enlarged (invisible) hit area. Multiple edges into the same input
    port are allowed (that's exactly the fan-in case TopologyMiddleware's
    adder logic already handles -- see its docstring) -- nothing here
    restricts it.
  - Selecting a node shows its registry Params in a right-side inspector
    as plain editable fields (int/float/tuple/string, dispatched by the
    Python type of that param's default), built from
    registry.NODE_TYPES[type_name].params the same way, structurally,
    that build_fixed_topology()'s *_params dicts are organized -- one
    param set per node, nothing flattened. A tag_* node also gets a
    "Payload" field (comma-separated 0/1 bits) since that's per-run DATA,
    not a registry Param -- same distinction build_fixed_topology()'s
    nodes already draw (payload lives next to "params", not inside it).
    The node's id itself is editable too (rename-checked for collisions).
  - Validate / Start / Stop, same shape as main_window.py: Validate runs
    plan_topology() over whatever's on the canvas right now and reports
    either "wiring resolves" or the exact KeyError (bad/renamed port)
    plan_topology raises. Start builds a real TopologyMiddleware from the
    canvas and runs it; Stop tears it down. The packet table underneath
    polls the same mw.get_packets() every 150ms, same as main_window.py.
  - Delete key removes selected nodes (and every edge touching them) or
    selected edges. Clear canvas wipes everything (blocked while
    running, same as Validate/Start are disabled while running).
  - Plain scroll pans the canvas (vertical wheel/scroll moves it up/
    down, horizontal wheel/scroll -- a trackpad's sideways swipe, or a
    mouse's horizontal scroll wheel -- moves it left/right), same
    convention as any other scrollable Qt view; Ctrl+scroll zooms
    in/out instead. Plus a light grid background, since that costs
    nothing and makes a free-form canvas easier to navigate.

FIXES FROM REAL USE (first hands-on pass caught these; all addressed):
  - Edge wiring didn't connect at all -- my own earlier edit had silently
    stranded PortHandle's label-creation code as dead code after a
    misplaced method split. Fixed; see commit history for the exact bug.
  - Edge wiring was ALSO just hard to land, even once that bug was fixed:
    ports were a small, pixel-exact target. Each PortHandle's hit area is
    now bigger than its visible dot (shape() override), press/release
    both snap to the nearest matching port within a constant screen-pixel
    radius if the exact pixel misses, and the drag now gives live visual
    feedback -- the source port highlights for the whole drag, and
    whatever "in" port is currently the snap target highlights too, so
    it's visible BEFORE releasing which connection is about to form.
  - Port spacing went from 18px to 24px, and the snap radius came down
    from 16px to 12px, specifically because 18-vs-16 was close enough
    that snapping to the WRONG adjacent port (e.g. grabbing Carrier_Out
    when aiming for the sender's other output, Tx_Leakage_Out, 18px
    away) was a real, silent risk -- a very plausible cause of one
    real crash below.
  - A real run crashed on Start with a gnuradio-level
    "multiply_const_cc(N): insufficient connected input ports (1 needed,
    0 connected)" -- traced by reading backscatter_rx_ook.py (and the
    other four rx_* blocks, same pattern): every rx_*'s self-interference
    cancellation multiply is wired to that hier-block's Tx_Leakage_In
    port UNCONDITIONALLY, not gated by si_enable, so an unconnected
    Tx_Leakage_In reliably produces exactly this crash. Validate() and
    Start() now both check this specific requirement up front (see
    _check_known_requirements()) and report it in plain language instead
    of letting gnuradio fail with an opaque internal error (it was also
    hard-crashing the whole process -- "Aborted (core dumped)" -- not
    just raising a catchable Python exception).
  - Every tag_* node's Data_In port looked like a normal, danglable
    connectable port, but TopologyMiddleware always wires it itself from
    that node's Payload field (see AUTO_WIRED_PORTS) -- it now renders
    greyed-out, with a tooltip explaining why, and is excluded from
    press/release/snap handling entirely so it can't be dragged from or
    to by mistake.
  - packet_hex_decoder's "Preamble" param looked editable but silently
    did nothing useful: TopologyMiddleware prepends a fixed, hardcoded
    preamble to every tag's transmitted frame regardless of this field,
    which only controls what the decoder looks for -- editing it would
    just break the match. Disabled with a tooltip explaining the real
    fix (threading one project-level preamble value through
    TopologyMiddleware instead of its current module constant) rather
    than silently letting it mislead.

FIXES FROM ROUND 2 (second hands-on pass, BPSK ran end-to-end but every
default needed hand-correcting first; all of this addressed):
  - Every freshly-dropped node started at plain, mostly-untested registry
    defaults -- NOT the validated overrides build_fixed_topology() has
    always applied for the fixed-shape path. This is exactly why sender's
    enable_gated (0 vs the needed 1), every tag's packet_bits/p_th
    (registry's 16-bit burst vs the validated 10000/0.5 -- a burst too
    short to ever be caught, same root cause documented in
    modulation_middleware's own module docstring), and each rx's
    si_isolation_db (flat 25.0, silently wrong for OOK specifically) all
    needed manual fixing on every single node, every time. NODE_SEED_OVERRIDES
    now seeds every new node with these SAME already-validated values
    instead of raw registry defaults -- registry.py itself is untouched,
    this is purely how NodeItem initializes.
  - "channel" is used for two different validated roles in the fixed
    topology (forward: distance=1; return: registry's own distance=2) --
    a dropped channel node has no role yet, so it's now seeded at the
    forward channel's distance=1, which also happens to be the easier-
    to-observe distance asked for directly.
  - Tx_Leakage had to be wired by hand, which both isn't how
    BackscatterMiddleware/build_fixed_topology ever worked (they connect
    it unconditionally themselves) and was flagged as unintended -- "the
    middleware should do it for me". scene_to_topology() now auto-wires
    every sender's Tx_Leakage_Out to every receiver's Tx_Leakage_In, and
    both ports are hidden from canvas interaction the same way tag_*'s
    Data_In already was (see AUTO_WIRED_PORTS). si_enable (already a
    real registry Param) is the actual toggle for this -- which brings
    up the next point.
  - "Toggle stuff... needn't be [a plain int] on the UI": enable_gated,
    si_enable, and pie_decoder's enable_mac are conceptually on/off even
    though the registry stores them as int 0/1 like every other param.
    BOOLEAN_INT_PARAMS now renders exactly these three as checkboxes in
    the inspector (writing back int 0/1, not Python bool, to match the
    stored type exactly) instead of int spinboxes.
  - A tag's Payload field and packet_hex_decoder's Payload Length param
    were two fully independent values with no mechanism keeping them
    equal (unlike build_fixed_topology(), which always COMPUTES
    payload_len_bits from the tag_specs themselves) -- _auto_sync_decoder()
    now does the same computation here, correcting the decoder's stored
    value (and the inspector, if showing it) right before Validate/Start.
  - "Bothersome to drag and drop every time I start": the canvas now
    has Save.../Load... (arbitrary named layouts, as JSON) and Save as
    Default, and auto-loads canvas_default_layout.json (shipped next to
    this file, a working 2-tag BPSK setup) on startup instead of opening
    empty.

STILL OPEN, raised directly by this pass, not yet addressed:
  - The free-form canvas still can't do "pick a modulation once,
    everything compatible follows" the way build_fixed_topology() can --
    NODE_SEED_OVERRIDES fixes the STARTING values for a newly-dropped
    node, but two tag_bpsk nodes you then tweak independently, or a
    tag/rx pair whose bit_rate/m/samp_rate you edit unevenly, have
    nothing syncing them the way build_fixed_topology()'s tag->rx
    bit_rate/m/samp_rate sync does on the fixed path. A project-level
    settings panel, and/or extending _auto_sync_decoder()'s approach to
    these shared names, is the natural next increment -- not done here,
    partly because pairing which tag(s) sync to which rx is genuinely
    ambiguous on a canvas that could hold multiple modulations at once
    (unlike the fixed path's single-modulation-per-run assumption).
  - packet_hex_decoder's "Preamble" field is still just disabled (see
    FIXES FROM REAL USE above) rather than actually threaded through --
    the real fix (one project-level preamble value flowing into
    TopologyMiddleware instead of its hardcoded module constant) belongs
    with the settings-panel work above, not fixed in isolation here.

WHAT'S DELIBERATELY NOT HERE YET -- flagged, not forgotten:
  - No port-dtype checking on connect (complex/byte/float). Validate
    only checks that named ports exist and resolve -- it does NOT catch
    "you wired a byte port into a complex port" or "you left a required
    input dangling". Both of those still only surface when you hit
    Start, as whatever error gnuradio's own tb.connect()/flowgraph
    validation raises, shown the same way the AssertionError/ValueError
    paths already are. Tightening Validate to catch these client-side,
    before Start, is a reasonable next step but not done here.
  - No live param editing while running (unlike main_window.py's one
    live distance field) -- stop, edit, restart. get_node(node_id) on
    the running TopologyMiddleware would make a "live" panel possible
    later; not wired up yet.
  - tag_analog/rx_analog aren't placeable (see PLACEABLE_NODE_TYPES) --
    same gap as the modulation dropdown leaving "Analog" out, for the
    same reason (tag_analog's Signal_In/float shape isn't a bitstream,
    and TopologyMiddleware's own node loop raises NotImplementedError
    for it today).
  - No automated test exercises the actual drag/click/drop mouse
    interaction -- that needs a real Qt event loop and eyes on screen,
    neither of which exist in the sandbox this was built in. What WAS
    checked without any GUI: the file imports and compiles cleanly, and
    the exact node/edge structure scene_to_topology() produces for a
    hand-built graph (mirroring build_fixed_topology()'s own 2-tag and
    3-tag OOK shape) was fed through the real plan_topology() and
    confirmed to resolve to the same connects/fan-in shape
    build_fixed_topology() itself produces for the same tag count -- see
    the test run noted in the commit. The genuinely untested part is the
    interactive feel (does a drag actually land on the right port, does
    a node drag actually drag) -- that's what running it for real will
    tell us, not something a mock can fake convincingly.

Run from the project root, venv active:
    python3 ui/canvas_window.py
"""
import sys
import re
from collections import defaultdict

sys.path.insert(0, "blocks")
sys.path.insert(0, ".")

from PyQt6 import QtWidgets, QtCore, QtGui

import json
import os

import core.registry as registry
import core.message_framer as message_framer
from core.modulation_middleware import (
    plan_topology, TopologyMiddleware, DEFAULT_TAG_OVERRIDES, SI_ISOLATION_DEFAULTS,
    SAMP_RATE, RX_ALREADY_AT_BIT_RATE, PREAMBLE_BITS,
)

# tag_analog needs a different payload shape (Signal_In/float, not
# Data_In/byte + a 0/1 bit list) that TopologyMiddleware's own node loop
# explicitly rejects (raises NotImplementedError) -- same gap
# BackscatterMiddleware already has, see modulation_middleware's module
# docstring. rx_analog only pairs with tag_analog, so there's nothing it
# usefully connects to yet either. Both left out of the palette rather
# than offered and made to fail on Start -- same precedent as
# ui/main_window.py leaving "Analog" out of its modulation dropdown.
#
# "pie_decoder" is ALSO left out, for a different reason: it isn't a
# separate usable node at all. Every tag_* hier-block already builds its
# own tag_pie_decoder internally -- confirmed by reading
# backscatter_tag_ook.py's Blocks section: `self.tag_pie_decoder_1 =
# tag_pie_decoder(enable_mac=enable_gated, ...)`. Its enable_mac is wired
# straight to the tag's OWN enable_gated param, not exposed as a separate
# knob -- `set_enable_gated()` calls `self.tag_pie_decoder_1.set_enable_mac(...)`
# directly. TopologyMiddleware never places a standalone pie_decoder node
# in any real topology either, so dropping one on the canvas had no
# wiring path that could ever matter -- it was dead weight in the palette.
#
# "packet_hex_decoder" is left out too, for a related reason: it's a
# single-input sink with nothing to connect it TO except "whichever rx
# you happen to have" -- there was never a real choice being made by
# wiring it yourself. It's now a fixed part of the pipeline instead: its
# settings live as NodeItem.decoder_params on the rx_* node that owns
# it (shown in that rx's own Block tab -- see NodeInspector.show_node()),
# and scene_to_topology() is where it actually gets auto-created and
# auto-wired to that rx's Data_Out, one decoder per receiver, same
# pattern as the Tx_Leakage auto-wire.
# "channel" joins pie_decoder/packet_hex_decoder's reasoning above: it's
# no longer a block you drag and wire yourself (two wires -- sender->
# channel, channel->tag -- for EVERY tag, times two for the return leg --
# was exactly the "bothersome" redundancy packet_hex_decoder's removal
# already set the precedent for). Every sender->tag_* edge and every
# tag_*->rx_* edge now implicitly IS a channel -- see EdgeItem's own
# channel_params/role -- so connecting a tag straight to a sender or an
# rx is all the wiring there is; the channel in between is created and
# wired automatically by scene_to_topology(), one per edge (per-tag
# distance/fading/noise, not one shared forward/return channel for every
# tag the way build_fixed_topology() still works). Click the connecting
# arrow itself to edit that one tag's channel, same inspector panel a
# block gets (see NodeInspector.show_edge()). "channel" stays a real
# registry.NODE_TYPES entry and still renders/works if an OLD saved
# layout has explicit channel boxes on it (see _edge_channel_role()) --
# only NEW placements lose it from the palette.
PLACEABLE_NODE_TYPES = [t for t in registry.NODE_TYPES
                         if t not in ("tag_analog", "rx_analog", "pie_decoder",
                                      "packet_hex_decoder", "channel")]

# Ports that are ALWAYS wired automatically -- either by TopologyMiddleware
# itself (tag_*'s Data_In, from that node's "payload" entry -- see its
# pass-1 loop/docstring) or by scene_to_topology() below (every sender's
# Tx_Leakage_Out to every rx_*'s Tx_Leakage_In, added to a fresh "overall
# settings"-style convenience: you were having to manually draw that wire
# every time, which isn't how BackscatterMiddleware/build_fixed_topology
# ever worked -- they connect it unconditionally themselves
# (`self.tb.connect((sender, 1), (rx, 1))`), the same way this now does.
# Both kinds render greyed-out and excluded from CanvasView's press/
# release/snap handling entirely -- not hidden outright, so it's still
# visible where that connection conceptually is, just not draggable.
AUTO_WIRED_PORTS = {
    "tag_ook": {"Data_In"}, "tag_bpsk": {"Data_In"},
    "tag_fm0": {"Data_In"}, "tag_miller": {"Data_In"},
    "sender": {"Tx_Leakage_Out"},
    "rx_ook": {"Tx_Leakage_In"}, "rx_bpsk": {"Tx_Leakage_In"},
    "rx_fm0": {"Tx_Leakage_In"}, "rx_miller": {"Tx_Leakage_In"},
}

# Known-good starting values for a freshly-dropped node, layered on top of
# plain registry defaults in NodeItem.__init__ -- the SAME validated
# overrides build_fixed_topology() already bakes in for the fixed-shape
# path (DEFAULT_TAG_OVERRIDES, its sender_params/fwd_channel_params
# defaults, SI_ISOLATION_DEFAULTS), now ALSO applied here, because the
# canvas was seeding every new node at raw, mostly-untested registry
# defaults -- which is exactly why every single node had to be hand-
# corrected before anything decoded: sender's enable_gated (registry
# default 0 -- raw, never-PIE-gated carrier -- vs the validated 1),
# every tag's packet_bits/p_th (registry default packet_bits=16 for
# everything but tag_ook -- a burst too short to ever be caught, see
# modulation_middleware's own module docstring -- vs DEFAULT_TAG_OVERRIDES'
# validated 10000/0.5), and each rx's si_isolation_db (registry's flat
# 25.0 default, which is fine for BPSK/FM0/Miller but silently breaks OOK
# -- see SI_ISOLATION_DEFAULTS). "channel" is the one type used for two
# DIFFERENT validated roles in the fixed topology (forward: distance=1,
# noise_volt_c1=0.02; return: registry's own distance=2, noise_volt_c1=
# 0.01) -- a freshly dropped channel node has no role yet, so this seeds
# the forward channel's values, which also happen to be the lower,
# easier-to-observe distance asked for here.
NODE_SEED_OVERRIDES = {
    "sender": {"enable_gated": 1},
    "channel": {"distance": 1, "noise_volt_c1": 0.02},
    "tag_ook": dict(DEFAULT_TAG_OVERRIDES),
    "tag_bpsk": dict(DEFAULT_TAG_OVERRIDES),
    "tag_fm0": dict(DEFAULT_TAG_OVERRIDES),
    "tag_miller": dict(DEFAULT_TAG_OVERRIDES),
    "rx_ook": {"si_isolation_db": SI_ISOLATION_DEFAULTS["OOK"]},
    "rx_bpsk": {"si_isolation_db": SI_ISOLATION_DEFAULTS["BPSK"]},
    "rx_fm0": {"si_isolation_db": SI_ISOLATION_DEFAULTS["FM0"]},
    "rx_miller": {"si_isolation_db": SI_ISOLATION_DEFAULTS["Miller"]},
}

# Arbitrary canvas-to-physical scale, used only to keep a per-edge
# channel's "Distance (m)" param in visual step with how far apart its
# two endpoint nodes are actually drawn -- see
# EdgeItem.sync_distance_from_position() (an edge's distance always
# reflects wherever its two nodes actually are: computed the moment the
# edge is created -- manually, or via CanvasWindow.auto_wire_selected()
# -- and recomputed on every drag afterward) and
# CanvasWindow.set_channel_edge_distance() (the inverse: type a distance
# -> the node moves to match). This is NOT a physical constant, just
# "how many screen pixels represent one simulated meter" -- one number
# to change if the canvas ever needs to feel more/less zoomed-in
# relative to distance.
#
# An earlier version of this only synced on a later drag, leaving a
# freshly wired edge stuck at its seeded placeholder (NODE_SEED_
# OVERRIDES["channel"] above / the registry's own return-channel
# default) until something moved it -- changed per feedback, since
# "shake the node to refresh the real distance" read as a bug, not a
# deliberate safety net. So: wherever you drop/arrange nodes BEFORE
# wiring them now directly sets their distance, same as dragging them
# apart afterward always did -- more canvas distance always means more
# Friis attenuation in the real channel block, there's no longer a
# "it's still at the validated default until touched" grace period.
#
# 500, not the original 40: 1 meter at 40px/m read as "too small" on
# screen (per feedback) -- 500px/m is the ~12x bump that was asked for
# (10x-15x), still one number to retune either way.
PIXELS_PER_METER = 500

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

# Enum-like int params that have GRC-documented word labels for each
# value -- taken from the source block's .block.yml "label:" field, not
# guessed. Rendered as a QComboBox; writes back the plain int index, the
# same type the registry default and the real block constructor expect.
#
# "fading_type_c1": backscatter_channel_1.block.yml documents this
# exactly as "Fading Mode (0=Clear, 1=Rician, 2=Rayleigh)". Cross-checked
# against backscatter_channel_1.py's own wiring: `blocks.selector(...,
# fading_type_c1, 0)` picks between three already-connected branches --
# index 0 bypasses both fading_model instances entirely (the plain
# Friis-attenuated signal: a clear/no-fading channel), index 1 selects
# `channels.fading_model(..., True, ...)` (LOS present -- Rician), index
# 2 selects `channels.fading_model(..., False, ...)` (no LOS --
# Rayleigh).
ENUM_PARAMS = {
    "fading_type_c1": ["Clear (no fading)", "Rician (LOS present)", "Rayleigh (no LOS)"],
}

# Per-node modulation switcher, shown in NodeInspector for any tag_*/rx_*
# node. The 4 real, placeable modulations -- "Analog" is left out for the
# same reason PLACEABLE_NODE_TYPES leaves tag_analog/rx_analog out
# entirely (see that constant's comment above).
#
# Deliberately scoped to ONE node at a time, never a project-wide switch:
# changing it only ever swaps the node you're looking at (e.g. tag_ook ->
# tag_bpsk on this one tag), carrying over any param shared by name
# between the old and new type, re-seeding anything new to the target
# type the same way a freshly-dropped node would be, and re-pointing
# that node's edges by matching old/new ports POSITIONALLY within each
# direction (handles tag_fm0/tag_miller's "Reflection_Out" vs
# tag_ook/tag_bpsk's "out" -- same slot, different name). It never
# touches any OTHER node -- not a connected rx, not another tag -- on
# purpose: a cascading/global switch would silently rewrite whatever
# heterogeneous multi-modulation network you'd built, which is exactly
# the freedom multi-tag/multi-rx testing needs to keep. See
# _convert_node_modulation() below for the actual swap.
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
    """A short, specific checklist of wiring requirements that
    plan_topology() deliberately does NOT check (it only resolves port
    names to positions -- see its own docstring) but that gnuradio's real
    flowgraph validation WILL reject at Start, with an opaque low-level
    error instead of a readable one. Currently just the one rule that's
    actually bitten someone: every placed rx_* node needs its
    Tx_Leakage_In fed -- confirmed by reading backscatter_rx_ook.py (and
    the other four rx_* blocks -- same pattern in all of them): the
    self-interference-cancellation multiply_const_cc inside every rx_*
    hier-block is wired straight to that hier-block's own port 1
    (Tx_Leakage_In) UNCONDITIONALLY in its __init__ -- not gated by
    si_enable -- so an unfed Tx_Leakage_In means that internal block gets
    built with 0 connected inputs, which is exactly the
    "multiply_const_cc(N): insufficient connected input ports (1 needed,
    0 connected)" crash. scene_to_topology() now auto-wires every
    sender's Tx_Leakage_Out to every rx's Tx_Leakage_In (see
    AUTO_WIRED_PORTS), so by the time `edges` reaches this function that
    wire already exists AS LONG AS a sender node is actually on the
    canvas -- this check is really "is there a sender", just phrased in
    terms of the edge its absence would leave missing. NOT a stand-in for
    general port-dtype/completeness validation (still not implemented,
    see module docstring) -- just this one specific, now-confirmed trap.
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


# Optional per-type icon images. Drop a transparent-background .png
# named exactly after a type (sender.png, tag_ook.png, tag_bpsk.png,
# tag_fm0.png, tag_miller.png, rx_ook.png, rx_bpsk.png, rx_fm0.png,
# rx_miller.png) into this folder and NodeItem picks it up automatically
# -- no code change needed per icon. A type with no matching file just
# keeps today's plain colored rectangle (_node_color() above), so
# dropping in icons for some types but not others is fine, nothing
# breaks either way. Square-ish source images work best (NodeItem scales
# to fit, keeping aspect ratio) -- a few hundred px per side is plenty;
# the shape (circle, rounded square, whatever) should already be baked
# into the png via its own transparency, since this code only ever
# draws the pixmap as-is, it doesn't mask or crop it into a shape itself.
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


class PortHandle(QtWidgets.QGraphicsEllipseItem):
    """One connectable port on a node -- a small circle, blue for an
    input, orange for an output, labeled with the port's real registry
    name. `port` is the registry.Port this handle represents; `node_item`
    is the NodeItem it belongs to (back-reference used when finishing a
    drag-to-connect, and by EdgeItem to find this handle's scene
    position)."""

    RADIUS = 6
    # Extra invisible click tolerance beyond the drawn circle -- a plain
    # 12px-wide dot is a hard target to land a release on exactly, which
    # was the actual cause of "drag works, release near an input doesn't
    # connect": shape() (what itemAt()/collision tests use for hit-testing)
    # defaulted to the SAME small circle paint() draws, so a release even
    # a couple pixels off the dot's center hit the node's big rect
    # instead and got rejected. shape() below is overridden to a larger
    # invisible circle while paint() (inherited, unchanged) still only
    # draws the small visible dot.
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

        # show_label=False (an icon-bearing NodeItem -- see its own
        # comment) drops the on-canvas port-name text entirely, per
        # feedback once a real icon was tried: with the node now sized
        # to hug the image, a text label next to every dot read as
        # clutter rather than useful, and the port's name/direction/
        # dtype is still right there in the tooltip on hover. position_
        # label() below no-ops when there's no label to move, so callers
        # don't need their own branch for this.
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

    # Icon-bearing nodes only (see __init__'s icon_pixmap branch) -- a
    # smaller, fixed layout that hugs the image instead of reusing the
    # 170px-wide plain-rectangle box above. Separate constants rather
    # than reusing WIDTH/HEADER_H because the two layouts' proportions
    # aren't related -- this is "how big should the picture be" (display
    # size, independent of whatever resolution the source .png actually
    # is), not a scaled-down version of the rectangle layout.
    # Display size on screen, independent of the source .png's own
    # resolution (a 300x300 file still gets scaled to fit this box).
    # Was 64 -- bumped to 96 ("a bit bigger", not "extremely larger")
    # once a real 300x300 image made 64 look too small.
    ICON_SIZE = 96
    # Gap between the icon's own left/right edge and the node's -- this
    # IS the node's left/right edge too, so it's also exactly how close
    # a port dot sits to the image (feedback: "make the circle blue hook
    # closer to the png image"). Small on purpose.
    ICON_PORT_MARGIN = 14
    ICON_TOP_MARGIN = 6
    ICON_LABEL_H = 28  # room for the type/id text below the image

    def __init__(self, node_id, type_name, canvas, pos):
        self.node_id = node_id
        self.type_name = type_name
        self.canvas = canvas  # CanvasWindow, used to keep edges in sync on move

        # order_seq: a monotonically increasing id, assigned once by
        # add_node() (see its own comment) and otherwise untouched for
        # this NodeItem's whole life -- including a modulation switch,
        # which rebuilds the item from scratch under possibly a
        # DIFFERENT node_id (tag_bpsk1 -> tag_ook1) but explicitly
        # carries this value over. See _find_upstream_tags()' use of it:
        # this is what makes "which tag is tag_id 0 vs 1" depend on
        # placement order alone, immune to a later rename OR to
        # self.edges' insertion order (the two things that made it
        # silently flip before). None here is a placeholder overwritten
        # by add_node() before this item is ever used for anything.
        self.order_seq = None

        nt = registry.NODE_TYPES[type_name]
        # Auto-wired ports (Data_In, Tx_Leakage_Out/In -- see
        # AUTO_WIRED_PORTS) were still drawn as a greyed-out dot even
        # though nothing can ever be manually connected to/from one --
        # "two extra entry/exit points that do nothing" per feedback.
        # Excluded from in_ports/out_ports entirely now: no dot, no
        # label, no port_rows slot for it. Nothing elsewhere in this file
        # ever looks one up by name (EdgeItem only ever deals in
        # manually-wired ports, since CanvasView._port_near() already
        # refuses to start/end a drag on an auto-wired one), so there's
        # nothing left depending on these being present.
        auto_wired_names = AUTO_WIRED_PORTS.get(type_name, set())
        self.in_ports = [p for p in nt.ports if p.direction == "in" and p.name not in auto_wired_names]
        self.out_ports = [p for p in nt.ports if p.direction == "out" and p.name not in auto_wired_names]
        port_rows = max(len(self.in_ports), len(self.out_ports), 1)

        # Icon, if ICON_DIR/<type_name>.png exists (see its own comment) --
        # otherwise fall back to exactly today's plain colored rectangle.
        # Either way this RectItem (self) stays the real node body: the
        # thing that's movable/selectable/has the ports as children --
        # an icon is just a picture drawn on top of it, not a swap to a
        # different item type, so nothing elsewhere that expects a
        # NodeItem to BE the QGraphicsRectItem breaks. Which layout
        # (size, where the label goes, whether ports get text) is picked
        # ONCE here, before super().__init__(), since the icon layout is
        # a genuinely different shape -- narrower, ports hugging the
        # image, name below instead of a top-left header -- not just a
        # resized version of the plain-rectangle one.
        icon_pixmap = _load_icon_pixmap(type_name)

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
            # NoPen, not just a faint one: self IS still a rectangle
            # underneath (this class is a QGraphicsRectItem; an icon is a
            # picture drawn on top of it, not a swap to a round item --
            # see the class comment above), so drawing ANY visible pen
            # here would outline that full rectangle around a circular
            # (or whatever-shaped) icon, defeating the point of a
            # non-rectangular icon in the first place. With no pen and
            # no brush, nothing of the rectangle itself is drawn at all
            # while idle -- only the icon picture (and the ports/label)
            # show. Selecting the node still shows Qt's own built-in
            # dashed selection box around this same rectangle (that part
            # isn't this pen -- it's QGraphicsRectItem's default paint()
            # always drawing it on top when selected, regardless of pen/
            # brush), which is the one case a rectangular hint still
            # flashes up around a round icon -- that's expected, it's
            # just "this is the thing that's currently selected".
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

        # Long-message tags (see message_framer.py): "raw" is today's
        # mode unchanged -- self.payload above IS the one frame that
        # gets sent, repeated forever. "text" instead frames
        # self.message_text into several same-length fragments at
        # scene_to_topology() build time (chunk_bytes bytes of message
        # data per fragment) and loops the WHOLE framed message instead
        # of one hand-typed frame -- self.payload is irrelevant in this
        # mode (effective_fragment_bits() below is what everything that
        # used to read len(self.payload) now reads instead). None/empty
        # for anything that isn't a tag_* node, same as self.payload.
        self.payload_mode = "raw" if type_name.startswith("tag_") else None
        self.message_text = "" if type_name.startswith("tag_") else None
        self.chunk_bytes = 4 if type_name.startswith("tag_") else None
        # "file" mode: a third payload_mode alongside raw/text (see
        # above) -- frames an arbitrary file's raw bytes the same way
        # "text" frames message_text, via message_framer.
        # build_fragments_from_bytes() instead of build_fragments() (no
        # UTF-8 round-trip, since a file's bytes have no reason to be
        # valid text). file_name is display/bookkeeping only (shown in
        # the inspector, offered back as the default save name on
        # reassembly) -- never transmitted or used by message_framer.
        self.file_bytes = b"" if type_name.startswith("tag_") else None
        self.file_name = "" if type_name.startswith("tag_") else None

        # decoder_params: packet_hex_decoder's OWN param dict, carried on
        # the receiver it decodes for -- not a separate canvas node (see
        # PLACEABLE_NODE_TYPES' comment) and not a global/shared setting
        # either (see the "per-receiver decoder" discussion this
        # replaced): two different receivers can need two different
        # payload lengths or preambles, same as any other per-rx setting.
        # bit_rate/samp_rate are seeded here but immediately overwritten
        # by scene_to_topology()'s per-receiver derivation every
        # Validate/Start regardless of what's set -- see its comment.
        self.decoder_params = (
            {p.name: p.default for p in registry.NODE_TYPES["packet_hex_decoder"].params}
            if type_name.startswith("rx_") else None
        )

        # Every port reaching this point already had any auto-wired name
        # filtered out above, so none of these are ever auto_wired=True
        # anymore -- that PortHandle constructor arg (and its grey/
        # disabled rendering) is kept only for any future port that
        # genuinely needs to show up but not be connectable by hand.
        #
        # show_label=False for an icon node: no on-canvas port-name text
        # (still in the tooltip) -- see PortHandle's own comment on this.
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

    def effective_fragment_bits(self):
        """Length, in bits, of ONE transmitted frame -- what the
        receiver this tag feeds needs as its payload_len/decoder
        payload_len_bits (see CanvasWindow._sync_decoder_params(), the
        only caller). Raw mode: len(self.payload), unchanged meaning
        from before payload_mode existed. Text/file mode: whatever
        message_framer.fragment_len_bits() always produces for this
        chunk_bytes regardless of message_text/file_bytes' actual
        content/length (header + chunk, see that module) -- NOT
        len(self.payload), which is irrelevant in either mode. Same
        formula for both since a fragment's size on the wire never
        depended on where the content came from, only on chunk_bytes.
        0 for a non-tag node, or a raw-mode tag with no payload at all
        yet."""
        if self.payload_mode in ("text", "file"):
            return message_framer.fragment_len_bits(self.chunk_bytes)
        return len(self.payload) if self.payload else 0

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if self.canvas is not None:
                self.canvas.update_edges_for_node(self)
        return result


def _edge_channel_role(from_node, to_node):
    """Which implicit channel this edge represents, if any -- "forward"
    (sender->tag, matches build_fixed_topology()'s fwd_channel_params
    role) or "return" (tag->rx, matches its ret_channel_params role), or
    None for anything else (not currently drawable by hand at all, since
    every other port is in AUTO_WIRED_PORTS -- except an OLD saved
    layout's edges touching an explicit "channel" NodeItem box, e.g.
    sender->channel1 or channel1->tag0, which also land here as None
    since neither endpoint is a bare sender/tag_*/rx_* pair -- see
    PLACEABLE_NODE_TYPES' comment: those keep working exactly as they
    always did, scene_to_topology() just passes them through unchanged).
    A narrow type-name check, not "every edge has a role", specifically
    so that backward compatibility falls out for free instead of needing
    its own special-cased branch."""
    if from_node.type_name == "sender" and to_node.type_name.startswith("tag_"):
        return "forward"
    if from_node.type_name.startswith("tag_") and to_node.type_name.startswith("rx_"):
        return "return"
    return None


class EdgeItem(QtWidgets.QGraphicsPathItem):
    """One connection between an output port and an input port, drawn as
    a straight line (see update_path()'s own comment on why not a
    curve). from_node/to_node are NodeItems; from_port_name/
    to_port_name are the registry port names on each side -- exactly the
    4-tuple shape scene_to_topology() turns into an
    (from_id, from_port, to_id, to_port) edge for plan_topology().

    role/channel_params: see _edge_channel_role() and
    PLACEABLE_NODE_TYPES' module comment -- a sender->tag_* or tag_*->
    rx_* edge IS a channel now (one per edge, not one shared forward/
    return channel for every tag), seeded here the same way NodeItem
    seeds a dropped node's params (registry default + the role's known-
    good NODE_SEED_OVERRIDES value), then live-edited in place by
    NodeInspector.show_edge() exactly like a node's own params --
    scene_to_topology() reads channel_params straight off this edge when
    it auto-builds that edge's real "channel" node. None for any edge
    _edge_channel_role() doesn't recognize (old explicit-channel-layout
    edges; see that function's docstring)."""

    # Line thickness in scene pixels. Was 2 -- feedback was "a bit
    # thicker", not a different style, so a modest bump rather than a
    # big jump. Same width for every edge, channel-bearing or not.
    PEN_WIDTH = 3

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
            # "return": the registry's own plain defaults (distance=2,
            # noise_volt_c1=0.01) already ARE the validated return-channel
            # values -- see NODE_SEED_OVERRIDES' comment -- nothing to
            # layer on top.
            #
            # registry.Param("distance", 2, ...) stores that default as a
            # plain int, which would make make_param_widget() build an
            # int QSpinBox for it -- fine on its own, but
            # sync_distance_from_position() always writes back a rounded
            # float (meters are fractional in general), and QSpinBox.
            # setValue() raises on a float. Cast once here so the widget
            # this edge ever gets is always the float QDoubleSpinBox. In
            # practice this value is immediately replaced a few lines
            # below anyway (self.sync_distance_from_position(), called
            # once this edge's ports actually exist) -- distance now
            # reflects the real on-canvas gap from the moment an edge is
            # created, not this seed -- but the cast stays as a type
            # guarantee regardless of whether that call path ever changes.
            self.channel_params["distance"] = float(self.channel_params["distance"])
        else:
            self.channel_params = None
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
        # Compute the REAL distance from wherever the two nodes already
        # are the moment this edge is created -- manually drawn or via
        # CanvasWindow.auto_wire_selected() -- instead of leaving the
        # seeded placeholder (1.0 / 2.0) sitting there until the node
        # happens to get dragged afterward. Per feedback: defaulting to
        # the seed until something "shakes" the node to refresh it was
        # confusing, not a safety feature worth keeping -- see
        # sync_distance_from_position()'s own updated comment.
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
        """Recomputes this edge's channel distance from how far apart its
        two endpoints are actually drawn right now, using PIXELS_PER_METER
        as the scale. Called from __init__ (so a freshly wired edge's
        distance reflects reality immediately, not the seeded placeholder
        -- see __init__'s own comment) AND from
        CanvasWindow.update_edges_for_node() whenever a node genuinely
        moves afterward (a drag, or the inverse
        set_channel_edge_distance()'s own setPos() call) -- same
        computation either way, just triggered at two different moments."""
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

    # Snap tolerance for landing a port click/release, in VIEWPORT pixels
    # (not scene units) -- converted to a scene-space search rect fresh
    # each time via mapToScene, so it stays the same number of screen
    # pixels regardless of how far you've zoomed in/out. This is on top
    # of PortHandle.shape()'s own enlarged hit area, as a second net:
    # if itemAt() at the exact pixel doesn't land on a port (it missed,
    # or something else -- a node's own rect, another port's shape --
    # is on top at that point), search a small ring around the click/
    # release point for the nearest matching port instead of giving up.
    # Reduced from an earlier 16px once PORT_SPACING went up to 24px (see
    # NodeItem) -- still generous for normal mouse precision, but no
    # longer close enough to the inter-port spacing to make snapping to
    # the WRONG adjacent port a real risk.
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
        if event.mimeData().hasText() and event.mimeData().text() in registry.NODE_TYPES:
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text() in registry.NODE_TYPES:
            event.acceptProposedAction()

    def dropEvent(self, event):
        type_name = event.mimeData().text()
        if type_name not in registry.NODE_TYPES:
            return
        scene_pos = self.mapToScene(event.position().toPoint())
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
        # No modifier: hand this straight to QGraphicsView's own
        # QAbstractScrollArea wheel handling, which already pans the
        # vertical scrollbar from angleDelta().y() and the horizontal
        # one from angleDelta().x() -- exactly the "plain scroll = pan,
        # horizontal scroll = pan sideways" behavior asked for. The
        # previous override replaced this default with an always-zoom
        # behavior, and that override had its own bug on top: it only
        # ever read angleDelta().y(), so a horizontal-only scroll
        # (y always 0) fell through its "else" branch and zoomed OUT
        # every single time, regardless of which way you scrolled --
        # that's the exact symptom reported ("horizontal only zooms out").
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
        pen = QtGui.QPen(QtGui.QColor("#eeeeee"))
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
    """Mouse-wheel-over-an-unfocused-field is the thing a form embedded
    in a scrollable sidebar should NEVER react to: Qt's default spin
    box / combo box behavior is to change the VALUE on wheel scroll
    just from hovering, with no click/focus required, which means
    scrolling down the inspector panel silently edits whatever field
    happens to be under the cursor along the way -- exactly the
    "I scroll the sidebar and it changes values" complaint. Click/tab
    into a field first (giving it focus) and the wheel still works
    normally to bump the value; scroll while merely hovering and the
    event is ignored here, so Qt's normal event propagation hands it
    up to the enclosing scroll area instead, which is what the person
    actually wanted to scroll.

    A first attempt here just checked self.hasFocus() inside
    wheelEvent() and nothing else -- that passed a synthetic
    app.sendEvent() test but did NOT fix the real bug, because
    QAbstractSpinBox/QComboBox ship with focusPolicy() ==
    Qt.FocusPolicy.WheelFocus by default, and Qt's *own* event
    dispatch grants the widget focus as a side effect of delivering
    the wheel event -- BEFORE this overridden wheelEvent() method
    ever runs. So by the time hasFocus() was checked, hovering alone
    had already made it True; the widget was, in effect, focusing
    itself via the very scroll it was supposed to ignore. The
    synthetic test didn't catch this because QApplication.sendEvent()
    delivers straight to the widget and skips Qt's real mouse-hover
    dispatch path (and the focus-granting side effect that path has).

    The actual fix: explicitly force focusPolicy() to StrongFocus
    (click or Tab only -- explicitly excludes WheelFocus) so Qt never
    hands this widget focus just because the cursor is sitting over
    it and the wheel turned. Scrolling while unfocused then has
    nothing to grab onto; hasFocus() stays reliably False until an
    actual click/tab, and the wheelEvent() override above does the
    rest."""

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
    elif isinstance(current_value, tuple):
        w = QtWidgets.QLineEdit(",".join(str(v) for v in current_value))
        w.editingFinished.connect(lambda n=name, box=w: on_change(
            n, tuple(int(v.strip()) for v in box.text().split(",") if v.strip() != "")))
    else:
        w = QtWidgets.QLineEdit(str(current_value))
        w.editingFinished.connect(lambda n=name, box=w: on_change(n, box.text()))
    return w


def _worst_bit_run(fragments, preamble_bits):
    """Longest run of identical bits anywhere in this tag's CONTINUOUSLY
    LOOPED transmission -- preamble + fragment, for every fragment,
    back to back, wrapped around once to also catch the loop's own
    seam (last fragment's tail butting up against the next loop's
    preamble) -- since that's genuinely where a run can appear that a
    single pass over one fragment alone would miss.

    This is the number the DC-blocker droop problem (see
    whitening-fix-findings.md) actually depends on: the header used to
    be the worst offender on its own (tag_id=0/frag_index=0 both being
    all-zero bytes, every loop) -- fixed by whitening the header. The
    chunk/payload was deliberately left untouched (per an explicit
    "don't touch the payload" requirement), so ANY message/file whose
    bytes happen to contain a longer run than the header's worst case
    can still trip the same mechanism -- confirmed in practice with a
    pasted paragraph needing a ~16k-sample dc_len. Computing this
    directly (instead of leaving it to "crank dc_len until it works")
    is what _update_message_info_label() surfaces to the user."""
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


class NodeInspector(QtWidgets.QWidget):
    """Right-side property panel for whatever single node is currently
    selected. Rebuilds its param form from registry.NODE_TYPES[...].params
    every time the selection changes -- one widget per Param, dispatched
    by the Python type of that param's current value (bool -> checkbox,
    int -> spinbox, float -> double spinbox, tuple -> comma-separated
    text, anything else -> plain text). This is a first pass, not
    per-param-tuned formatting -- large values like samp_rate=2e6 display
    as a plain decimal, not scientific notation, but round-trip
    correctly either way."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.current_node = None
        self.current_edge = None  # set instead of current_node when an
                                   # EdgeItem (a per-tag channel -- see
                                   # show_edge()) is what's selected
        self.param_widgets = {}
        self.decoder_param_widgets = {}

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

        # Modulation switcher -- only shown for tag_*/rx_* nodes (see
        # MODULATION_CHOICES' module comment for exactly what changing
        # this does and doesn't touch). Deliberately separate from the
        # scrollable params_form below: it's a structural choice (which
        # registry type this node actually is), not a tunable value.
        self.modulation_label = QtWidgets.QLabel("Modulation")
        self.modulation_combo = NoScrollComboBox()
        self.modulation_combo.addItems(MODULATION_CHOICES)
        self._updating_modulation_combo = False
        self.modulation_combo.currentTextChanged.connect(self._on_modulation_changed)
        outer.addWidget(self.modulation_label)
        outer.addWidget(self.modulation_combo)

        # Payload mode -- "Raw bits" is the original toddler-level mode
        # (one short hand-typed frame, repeated forever, below); "Text
        # message" instead frames message_text into several same-length
        # fragments at build time and loops the WHOLE framed message
        # (see message_framer.py and NodeItem.payload_mode). Only shown
        # for tag_* nodes, same gating as payload_label/payload_edit.
        self.payload_mode_label = QtWidgets.QLabel("Payload source")
        self.payload_mode_combo = NoScrollComboBox()
        self.payload_mode_combo.addItems(["Raw bits", "Text message", "File"])
        self._updating_payload_mode_combo = False
        self.payload_mode_combo.currentTextChanged.connect(self._on_payload_mode_changed)
        outer.addWidget(self.payload_mode_label)
        outer.addWidget(self.payload_mode_combo)

        self.payload_label = QtWidgets.QLabel("Payload (bits, comma-separated)")
        self.payload_edit = QtWidgets.QLineEdit()
        self.payload_edit.editingFinished.connect(self._on_payload_changed)
        outer.addWidget(self.payload_label)
        outer.addWidget(self.payload_edit)

        # "Text message" mode's own three controls -- message content,
        # how many bytes go into each fragment (the actual tunable
        # that, together with message length, determines how many
        # fragments the message needs), and a live read-only summary of
        # what that resolves to so Start/Validate aren't required just
        # to find out.
        self.message_text_label = QtWidgets.QLabel("Message")
        self.message_text_edit = QtWidgets.QLineEdit()
        self.message_text_edit.editingFinished.connect(self._on_message_text_changed)
        outer.addWidget(self.message_text_label)
        outer.addWidget(self.message_text_edit)

        # "File" mode's own control -- a file picker instead of a typed
        # message. The chosen file's raw bytes get framed exactly the
        # same way message_text's UTF-8 bytes do (see NodeItem.
        # file_bytes/file_name and message_framer.build_fragments_from_
        # bytes()); chunk_bytes/message_info_label right below are
        # shared with text mode (same meaning either way), so they're
        # shown for file mode too rather than duplicated.
        self.file_picker_button = QtWidgets.QPushButton("Choose File...")
        self.file_picker_button.clicked.connect(self._on_choose_file_clicked)
        self.file_info_label = QtWidgets.QLabel("")
        self.file_info_label.setWordWrap(True)
        self.file_info_label.setStyleSheet("color: #666666;")
        outer.addWidget(self.file_picker_button)
        outer.addWidget(self.file_info_label)

        self.chunk_bytes_label = QtWidgets.QLabel("Bytes per fragment")
        self.chunk_bytes_spin = NoScrollSpinBox()
        self.chunk_bytes_spin.setRange(1, message_framer.MAX_CHUNK_BYTES)
        self.chunk_bytes_spin.valueChanged.connect(self._on_chunk_bytes_changed)
        outer.addWidget(self.chunk_bytes_label)
        outer.addWidget(self.chunk_bytes_spin)

        self.message_info_label = QtWidgets.QLabel("")
        self.message_info_label.setWordWrap(True)
        self.message_info_label.setStyleSheet("color: #666666;")
        outer.addWidget(self.message_info_label)

        # Preamble is deliberately a RECEIVER-side-only setting (see
        # plan_topology()'s docstring and _sync_decoder_params' preamble
        # note) -- a tag has no preamble of its own to edit, it just
        # transmits whatever its own governing receiver's decoder is
        # configured to look for, same as a real reader decides what
        # preamble to expect and a tag just reflects bits. That's easy
        # to read as "why is this only editable on the rx, is that a
        # bug" from the tag's side, so this shows this tag's ACTUAL
        # resolved preamble and exactly where to change it, without
        # adding a second editable copy that could drift out of sync
        # with the receiver's -- see show_node()'s is_tag branch.
        self.preamble_info_label = QtWidgets.QLabel("")
        self.preamble_info_label.setWordWrap(True)
        self.preamble_info_label.setStyleSheet("color: #666666;")
        outer.addWidget(self.preamble_info_label)

        self.params_form_widget = QtWidgets.QWidget()
        self.params_form = QtWidgets.QFormLayout(self.params_form_widget)
        # Stack each row's label ABOVE its field instead of beside it.
        # With long labels ("Channel - Distance (m)", etc.) and a fixed
        # panel width, the default side-by-side layout pushed the actual
        # edit boxes out of view -- you had to scroll the panel sideways
        # to reach them. WrapAllRows removes the side-scrolling entirely;
        # the panel only ever scrolls vertically now.
        self.params_form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapAllRows)
        self.params_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.params_form_widget)
        outer.addWidget(scroll, 1)

        self.ports_label = QtWidgets.QLabel("")
        self.ports_label.setWordWrap(True)
        outer.addWidget(self.ports_label)

        self.setEnabled(False)

    def show_node(self, node_item):
        self.current_node = node_item
        self.current_edge = None
        self._clear_params_form()

        if node_item is None:
            self.setEnabled(False)
            self.title_label.setText("No node selected")
            self.id_edit.setText("")
            self.id_edit.setEnabled(True)
            self.modulation_label.setVisible(False)
            self.modulation_combo.setVisible(False)
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
            self.ports_label.setText("")
            return

        self.setEnabled(True)
        self.id_edit.setEnabled(True)
        self.title_label.setText(node_item.type_name)
        self.id_edit.setText(node_item.node_id)

        modulation = TYPE_TO_MODULATION.get(node_item.type_name)
        show_modulation = modulation is not None
        self.modulation_label.setVisible(show_modulation)
        self.modulation_combo.setVisible(show_modulation)
        if show_modulation:
            self._updating_modulation_combo = True
            idx = self.modulation_combo.findText(modulation)
            if idx >= 0:
                self.modulation_combo.setCurrentIndex(idx)
            self._updating_modulation_combo = False

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

        # Recompute this receiver's decoder config (and, for FM0/Miller,
        # its own bit_rate param -- see _sync_decoder_params' docstring)
        # BEFORE building either params form below, so what's displayed
        # is never the stale placement-time default: this is what makes
        # opening/switching a receiver immediately show the truth, no
        # Validate/Start required first. notify=False -- this call IS
        # the refresh, so it must not also trigger show_node again.
        if node_item.decoder_params is not None:
            self.window._sync_decoder_params(
                node_item, self.window._find_upstream_tags(node_item), notify=False)

        nt = registry.NODE_TYPES[node_item.type_name]
        for p in nt.params:
            widget = self._make_param_widget(p.name, node_item.params.get(p.name, p.default))
            self.params_form.addRow(p.label or p.name, widget)
            self.param_widgets[p.name] = widget

        # Decoder settings, for rx_* nodes only -- same form, same tab,
        # right below this receiver's own params (see decoder_params'
        # comment on NodeItem: no longer a separate node or a global
        # tab, it's this ONE receiver's own decode config now, the same
        # way "two receivers might need two payload lengths" actually
        # works). bit_rate/samp_rate stay disabled: scene_to_topology()
        # derives those two per receiver regardless of what's set here
        # (see its comment -- a wrong value there doesn't error, it
        # silently decodes nothing, the already-diagnosed FM0/Miller
        # failure mode), so showing them as editable here would be a lie.
        if node_item.decoder_params is not None:
            self.params_form.addRow(QtWidgets.QLabel("<b>Decoder (this receiver)</b>"))
            dnt = registry.NODE_TYPES["packet_hex_decoder"]
            for p in dnt.params:
                # FM0/Miller now carry their OWN payload_len/preamble
                # params (see registry.py's comment on rx_fm0/rx_miller --
                # the embedded decoder needs to know packet length to
                # re-sync per packet). Showing payload_len_bits/preamble
                # AGAIN here, right above, with the exact same value,
                # would just be the same field twice -- skip them here
                # for those two types and let the one up in this
                # receiver's own params section be the single editable
                # copy (mirrored down into decoder_params by
                # _sync_decoder_params, not independently re-derived).
                # OOK/BPSK have no such params of their own, so nothing
                # is redundant for them -- keep showing both here.
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
                self.params_form.addRow(p.label or p.name, widget)
                self.decoder_param_widgets[p.name] = widget

        port_descriptions = ", ".join(f"{p.name} ({p.direction}/{p.dtype})" for p in nt.ports)
        self.ports_label.setText(f"Ports: {port_descriptions}")

    def show_edge(self, edge_item):
        """The per-tag channel living on a sender->tag_* or tag_*->rx_*
        connecting arrow (see EdgeItem.role/channel_params and
        PLACEABLE_NODE_TYPES' module comment) -- shown in this SAME
        Block tab/panel, not a separate one, same precedent as a
        receiver's decoder settings showing inline in show_node() rather
        than their own tab. Clicking the arrow is "basically a block"
        the same way clicking a node is; there's just no node ID, no
        modulation, and no payload to show instead of a channel's own
        registry params."""
        self.current_node = None
        self.current_edge = edge_item
        self._clear_params_form()

        self.setEnabled(True)
        role_text = ("Forward (sender -> tag)" if edge_item.role == "forward"
                     else "Return (tag -> receiver)")
        self.title_label.setText(f"channel -- {role_text}")
        self.id_edit.setText(f"{edge_item.from_node.node_id}  →  {edge_item.to_node.node_id}")
        self.id_edit.setEnabled(False)  # identified by its endpoints, not renamed
        self.modulation_label.setVisible(False)
        self.modulation_combo.setVisible(False)
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

        ch_nt = registry.NODE_TYPES["channel"]
        for p in ch_nt.params:
            widget = make_param_widget(
                p.name, edge_item.channel_params.get(p.name, p.default),
                self._on_channel_param_changed)
            self.params_form.addRow(p.label or p.name, widget)
            self.param_widgets[p.name] = widget

        # Interference_In is deliberately not surfaced at all -- nothing
        # wires it on a per-edge channel (TopologyMiddleware zero-sources
        # any channel's unconnected Interference_In automatically, see
        # plan_topology()'s own docstring), so there's nothing here to
        # click or connect for it.
        self.ports_label.setText(
            "This channel's Interference_In is left unconnected (silently "
            "zero-sourced) -- nothing to wire for it here."
        )

    def _clear_params_form(self):
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        self.param_widgets = {}
        self.decoder_param_widgets = {}

    def _make_param_widget(self, name, current_value):
        return make_param_widget(name, current_value, self._on_param_changed)

    def _on_decoder_param_changed(self, name, value):
        if self.current_node is not None and self.current_node.decoder_params is not None:
            self.current_node.decoder_params[name] = value

    def _on_param_changed(self, name, value):
        if self.current_node is not None:
            self.current_node.params[name] = value

    def _on_channel_param_changed(self, name, value):
        if self.current_edge is None or self.current_edge.channel_params is None:
            return
        self.current_edge.channel_params[name] = value
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
        self.window._convert_node_modulation(self.current_node, text)

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

    def _on_chunk_bytes_changed(self, value):
        if self.current_node is None:
            return
        self.current_node.chunk_bytes = value
        self._update_message_info_label()

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
        """Live summary of what the current message/file + chunk-size
        settings actually resolve to (fragment count, bits per
        fragment) -- without this you'd only find out by hitting
        Validate/Start, and a message/file that needs more than
        message_framer.MAX_FRAGMENTS fragments would only surface as a
        ValueError buried in that Start attempt instead of right here
        while still typing/picking. Text and file mode share this one
        label -- see build_fragments_from_bytes()'s docstring for why
        frag_count/used_bytes mean exactly the same thing for either.

        For tag_ook/tag_bpsk specifically, also appends the longest run
        of identical bits THIS content actually puts on the wire (see
        _worst_bit_run_samples()) -- the DC-blocker droop problem (see
        whitening-fix-findings.md) depends on that number, not on
        message length, and it's different for every message/file, so
        there's no single dc_len that's "correct" once and for all.
        This turns "crank dc_len until it works" into a number you can
        read off before even hitting Start. Skipped for tag_fm0/
        tag_miller -- their self-clocking line coding never produces a
        run long enough for this to matter (see registry.py's dc_len
        comment on those two types)."""
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
        frag_bits = message_framer.fragment_len_bits(node.chunk_bytes)
        # Kept to ONE short line on purpose -- this used to spell out
        # byte/fragment/bit counts in a full sentence, which ate too
        # much vertical space in the sidebar (especially with the
        # decoder section open below it). The "why" (auto-sets payload
        # length) now lives in this label's tooltip instead.
        text = f"{len(fragments)} fragment(s), {frag_bits} bits each"
        tooltip = "Sets this tag's receiver's payload length automatically."

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
        """Answers "what preamble does THIS tag actually transmit, and
        why can't I edit it here" right on the tag's own panel, instead
        of leaving that only discoverable by clicking over to its
        receiver. See preamble_info_label's own comment in __init__ for
        why there's deliberately no second editable copy here."""
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
        self._node_counter = defaultdict(int)
        self._next_order_seq = 0  # see NodeItem.order_seq's own comment
        self.nodes = {}   # node_id -> NodeItem
        self.edges = []   # list of EdgeItem
        # Decoder settings now live on each rx_* NodeItem itself
        # (.decoder_params) -- see NodeItem's comment. No global dict
        # here any more.

        # copy_selected()/paste_clipboard()'s clipboard -- a plain dict
        # (not a NodeItem/EdgeItem reference) so pasting doesn't share
        # any live state with the node it was copied from. None until
        # the first Copy. See copy_selected()'s own docstring for what's
        # captured.
        self._clipboard = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QHBoxLayout(central)

        palette_box = QtWidgets.QGroupBox("Node palette (drag onto canvas)")
        palette_layout = QtWidgets.QVBoxLayout(palette_box)
        self.palette_list = NodePaletteList()
        palette_layout.addWidget(self.palette_list)
        outer.addWidget(palette_box, 0)

        center_col = QtWidgets.QVBoxLayout()

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
        center_col.addWidget(self.view, 1)

        outer.addLayout(center_col, 1)

        # Decoded-packet display: one QDockWidget per receiver (NOT a
        # plain QTableWidget any more, and deliberately not a plain
        # QTabWidget either) -- see _rebuild_decoder_docks()'s own
        # docstring for why docks specifically. Built empty here;
        # populated in _rebuild_decoder_docks(), called from on_start()
        # once the actual set of receivers (and their decoder_id's,
        # f"decoder_{rx.node_id}") for this run is known.
        self.decoder_docks = {}    # decoder_id -> QDockWidget
        self.decoder_tables = {}   # decoder_id -> QTableWidget (fragments)
        self.message_tables = {}  # decoder_id -> QTableWidget (reassembled
                                   # messages -- see message_framer.py)
        self.setDockNestingEnabled(True)

        # Right panel is just the one "Block" tab -- the per-node
        # inspector. Neither "Decoder" nor "Channels" ever became a
        # separate tab: a receiver's decoder settings show inline in its
        # own Block tab content (see NodeInspector.show_node()), and now
        # a tag's own channel does too, just on an EdgeItem instead of a
        # NodeItem -- see NodeInspector.show_edge() and
        # PLACEABLE_NODE_TYPES' module comment for the arrow-as-channel
        # redesign itself (decided, and built, as of this pass: no
        # separate "Channels" tab, clicking the connecting arrow reuses
        # this exact panel). "Overall settings" (a project-level panel
        # for things like the shared preamble -- see the module
        # docstring's "STILL OPEN" section) stays unbuilt; nothing here
        # depends on it.
        self.inspector = NodeInspector(self)
        self.right_tabs = QtWidgets.QTabWidget()
        self.right_tabs.addTab(self.inspector, "Block")
        outer.addWidget(self.right_tabs, 0)

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
                 file_bytes=None, file_name=None, order_seq=None):
        """node_id/params/payload/decoder_params/payload_mode/message_text/
        chunk_bytes/file_bytes/file_name let _load_layout_dict() recreate
        a saved node exactly (same id, same edited values) instead of
        getting a fresh auto-id and NODE_SEED_OVERRIDES-only params --
        the normal drag-from-palette path (CanvasView.dropEvent) only
        ever passes type_name/scene_pos, leaving these at their
        defaults. These are None for anything saved before the
        corresponding mode existed (an older layout, or a non-tag node)
        -- left at NodeItem's own defaults ("raw"/""/4/b""/"") rather
        than erroring on a missing key.

        order_seq: see NodeItem.order_seq's own comment -- None (the
        normal case: a genuinely new node from the palette, paste, or
        an old saved layout with no order_seq of its own) allocates a
        fresh one here. _convert_node_modulation() is the one caller
        that passes the OLD item's order_seq through explicitly, so a
        modulation switch's delete+recreate doesn't read as "a brand
        new tag added at the end" for tag_id assignment purposes."""
        if node_id is None:
            self._node_counter[type_name] += 1
            node_id = f"{type_name}{self._node_counter[type_name]}"
            while node_id in self.nodes:
                self._node_counter[type_name] += 1
                node_id = f"{type_name}{self._node_counter[type_name]}"
        item = NodeItem(node_id, type_name, self, scene_pos)
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
        self.view.scene().addItem(item)
        self.nodes[node_id] = item
        self.status.showMessage(f"Added {type_name} as '{node_id}'", 3000)
        return item

    def add_edge(self, from_node, from_port_name, to_node, to_port_name):
        if from_node is to_node:
            self.status.showMessage("Can't connect a node to itself.", 4000)
            return None
        edge = EdgeItem(from_node, from_port_name, to_node, to_port_name)
        self.view.scene().addItem(edge)
        self.edges.append(edge)
        return edge

    def _edge_exists(self, from_node, from_port_name, to_node, to_port_name):
        return any(e.from_node is from_node and e.from_port_name == from_port_name
                   and e.to_node is to_node and e.to_port_name == to_port_name
                   for e in self.edges)

    def auto_wire_selected(self):
        """"20 drags for 10 tags" is a real problem once a layout has more
        than a couple of tags on it (see the discussion that led here) --
        this is the fix that doesn't require inventing implicit/by-
        distance connectivity the way Cooja has it (a GNU Radio
        flowgraph has no such thing; every signal path genuinely needs a
        real wire -- see that discussion). Instead: select a sender
        and/or a receiver PLUS the tags to connect them to (rubber-band,
        or ctrl/shift-click to build up the set), click the toolbar
        button, and every selected tag gets wired to BOTH in one pass --
        sender's single real out port (there's only ever one once
        Tx_Leakage_Out is filtered out, see NodeItem.__init__) to each
        tag's single in port, and each tag's single out port to the
        receiver's single in port. Exactly the same edges you'd get
        dragging them by hand one at a time (same EdgeItem, same
        per-edge channel seeding, same role detection via
        _edge_channel_role()) -- this only saves the dragging, it
        doesn't change what gets built."""
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
                edge.update_path()
                if edge.role is not None:
                    edge.sync_distance_from_position()
                    self.inspector.refresh_channel_distance(edge)

    def set_channel_edge_distance(self, edge, meters):
        """Inverse of EdgeItem.sync_distance_from_position(): typing a
        distance into the inspector moves this edge's TAG endpoint (never
        the sender/receiver -- those are commonly shared by OTHER tags'
        own edges too, see _edge_channel_role(), so only the tag end is
        ever safe to reposition for a per-tag channel) so the on-canvas
        pixel gap matches the typed value. Moving that node fires
        NodeItem.itemChange() -> update_edges_for_node() exactly as a
        manual drag would, which is what re-syncs this edge's distance
        (and this same tag's OTHER channel edge, if it has one) back
        from the new position right afterward -- the same
        single-source-of-truth model a drag uses, just driven by a typed
        number instead of the mouse.

        A tag has at most two channel edges (forward: sender->tag,
        return: tag->rx) both anchored to this SAME tag node, so moving
        it is a rigid-body translation that shifts BOTH edges' tag-side
        ports together. Originally this just slid the tag along the ray
        from the anchor being edited, with no regard for the other
        edge -- which silently changed the OTHER channel's distance too,
        so editing forward then return then forward again never settled
        ("have to edit both multiple times till they get to an
        equilibrium"). _solve_tag_move() below picks the translation
        that hits the NEW typed distance exactly while leaving the other
        edge's CURRENT distance untouched, by solving where two circles
        (one per edge's distance constraint) intersect -- one typed
        number now moves the tag to the right spot in one shot."""
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
        """Computes the translation to apply to mover_node (a tag) so its
        `mover_key` port ends up exactly target_px from anchor_pos, while
        leaving `other_edge` (the tag's OTHER channel edge, if it has
        one) at its CURRENT distance -- see set_channel_edge_distance()'s
        own comment for why this is a circle/circle intersection problem
        rather than a single ray-projection.

        Circle A: centered so that translation D=(0,0) plus D landing on
        it means the edited port is exactly target_px from anchor_pos.
        Circle B: centered so D=(0,0) plus D landing on it means the
        OTHER edge's port is exactly its existing (unchanged) distance
        from ITS anchor -- note D=(0,0) is already exactly on circle B,
        since that's the tag's actual current position, so the two
        circles intersecting near the origin is the "least disruptive"
        solution, not an arbitrary one.

        Falls back to the old single-constraint ray move (slide along
        the line from anchor_pos) when there's no other edge yet, or
        when the two requested distances are geometrically incompatible
        from here (the circles don't intersect at all) -- same as
        today's behavior in that case, the other edge just drifts."""
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

    def _delete_node(self, node_item):
        for edge in [e for e in self.edges if e.from_node is node_item or e.to_node is node_item]:
            self._delete_edge(edge)
        del self.nodes[node_item.node_id]
        self.view.scene().removeItem(node_item)
        if self.inspector.current_node is node_item:
            self.inspector.show_node(None)

    def _delete_edge(self, edge_item):
        self.edges.remove(edge_item)
        self.view.scene().removeItem(edge_item)
        if self.inspector.current_edge is edge_item:
            self.inspector.show_node(None)

    def copy_selected(self):
        """Copies the one selected node's own data (type, params, payload,
        decoder_params) into self._clipboard as a plain dict -- deliberately
        NOT a reference to the live NodeItem, so later edits to the
        original (or deleting it) can't retroactively change what a
        later Paste produces.

        For a tag_* node specifically, also remembers which sender it's
        wired FROM (role=="forward") and which receiver it's wired TO
        (role=="return"), plus each of those edges' own channel_params
        (noise/fading -- NOT distance, see paste_clipboard()) -- this is
        the actual point of copy-paste for the "wire one tag, clone it
        9 times" workflow discussed alongside auto_wire_selected(): a
        pasted tag arrives already wired to the same sender/receiver,
        not floating unconnected."""
        selected_nodes = [i for i in self.view.scene().selectedItems() if isinstance(i, NodeItem)]
        if len(selected_nodes) != 1:
            self.status.showMessage("Select exactly one node to copy.", 4000)
            return
        node = selected_nodes[0]

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
                                  file_name=clip["file_name"])

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

        # Carry over any param that exists (by name) on both types -- but
        # only when its CURRENT value is actually a deliberate edit, not
        # just whatever the OLD type happened to default/seed it to.
        # Plenty of params are "fresh" on both types (never touched since
        # the node was placed/last switched) yet have genuinely different
        # correct values per modulation: gamma0_mag/gamma0_phase_deg/
        # gamma1_mag/gamma1_phase_deg (literally the tag's two reflection
        # states -- OOK wants a big magnitude contrast, same phase; BPSK
        # wants near-equal magnitude, opposite phase -- these ARE the
        # modulation, not incidental settings) and as_real/as_imag (the
        # tag's static/antenna-mismatch term) both differ by registry
        # default across tag_ook/bpsk/fm0/miller. si_isolation_db is the
        # same story on the rx side (25dB vs 100dB). Blindly carrying any
        # of these over as "current value" silently left a switched node
        # physically configured for the OLD modulation while labeled and
        # wired as the new one -- e.g. BPSK's near-zero-amplitude-contrast
        # gamma pair surviving a switch to OOK, whose envelope detector
        # needs exactly that contrast to see anything. So: compare each
        # shared param's current value to what a FRESH node of the OLD
        # type would have had (its own default/seed) -- if they match,
        # it was never actually edited, so let the NEW type's own fresh
        # default/seed apply instead of carrying the old one over. If
        # they differ, the user deliberately tuned it, and that choice
        # survives the switch (slot_id, a hand-tuned bit_rate, a
        # purposely-customized gamma for an experiment, etc.).
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
            if e.from_node is node_item:
                pending_edges.append(("out", e.from_port_name, e.to_node, e.to_port_name))
            elif e.to_node is node_item:
                pending_edges.append(("in", e.to_port_name, e.from_node, e.from_port_name))

        self._delete_node(node_item)
        new_item = self.add_node(new_type_name, center, node_id=node_id,
                                  params=new_params, payload=payload, decoder_params=decoder_params,
                                  payload_mode=payload_mode, message_text=message_text,
                                  chunk_bytes=chunk_bytes, file_bytes=file_bytes,
                                  file_name=file_name, order_seq=order_seq)
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
        """Walks backward from rx_item through however many channel
        node(s) feed it and returns the tag_* NodeItems found, in a
        STABLE order (see _NODE_ID_SORT_KEY below) -- this receiver's
        decoder settings (bit_rate, payload length) are derived from
        exactly these tags, not every tag on the canvas -- see
        NodeItem.decoder_params' comment for why that's per-receiver
        now rather than one global value. The returned order is also
        exactly what scene_to_topology() enumerate()s to assign each
        tag its tag_id (see its own comment there), so this being
        stable is what makes tag_id itself stable across rebuilds.

        It did NOT used to be: this was a bare DFS over self.edges in
        whatever order edges happen to sit in that list, which is
        INSERTION order -- fine as long as nothing ever re-inserts an
        edge, but _convert_node_modulation() (switching a tag's OOK/
        BPSK/FM0/Miller dropdown) deletes that tag's edge(s) and
        re-adds them at the END of self.edges every time. With two
        tags feeding one receiver, switching modulation on just ONE of
        them silently moved that one tag's edge past the other's in
        list order -- which tag got tag_id 0 vs 1 could flip on a
        switch that has nothing to do with tag_id at all. Reported as
        "message ends up filed under the wrong tag, and swapping/
        switching things doesn't fix it predictably".

        Sorted by order_seq, NOT node_id -- node_id isn't stable either,
        since a modulation switch can rename it (tag_bpsk1 -> tag_ook1,
        see _convert_node_modulation()'s id-rename comment), which would
        just trade one source of flip-on-switch instability for another
        (a BPSK->OOK rename changes this tag's ALPHABETICAL position
        relative to any tag that stayed BPSK). order_seq is the one
        thing that's deliberately carried over, unchanged, through a
        modulation switch's delete+recreate -- see its own comment on
        NodeItem and add_node()'s order_seq param."""
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
        """Keeps a receiver's decoder config -- and, for FM0/Miller, the
        receiver's OWN bit_rate/payload_len/preamble params -- derived
        from whatever its upstream tag(s) actually transmit at, instead
        of trusting separately-editable fields that can silently drift
        apart. Several things get kept in lock-step here, all previously
        able to desync from each other (the "half changed between tag
        and RX" failure mode first reported turned out to have more than
        one cause):

        - payload_len_bits/preamble, for FM0/Miller: these now live on
          the receiver's OWN params (registry.py's comment on
          rx_fm0/rx_miller -- its embedded decoder needs to know packet
          length to re-sync once per packet) as well as on the
          auto-wired decoder node's decoder_params. Showing/editing the
          SAME number in two places was the redundancy being fixed here:
          rx_item.params is now the one canonical, user-editable copy
          (auto-corrected from the tag below same as before), and
          decoder_params just MIRRORS it -- never derived independently
          -- so the two literally cannot show two different values.
          OOK/BPSK have no such params of their own, so for them
          decoder_params["payload_len_bits"] is still derived directly
          from the tag, same as always.
        - the canonical bit_rate: taken from the upstream tag(s), never
          from the receiver's own bit_rate field -- the tag is what
          actually decides the physical transmission rate.
        - rx_item.params["bit_rate"] (FM0/Miller only): these two types
          carry their OWN bit_rate param, consumed by their OWN embedded
          decimator -- a field completely separate from decoder_params,
          and nothing previously kept it synced to the tag's rate. A
          modulation switch only ever reset it to the new type's
          registry default (10000), which happens to match the tag's
          own default too -- so this stayed hidden until bit_rate was
          edited on one side without the other, or the two were synced
          by coincidence rather than by anything enforcing it. If it
          drifted, the receiver's own internal decimation is wrong
          regardless of what the decoder gets -- fixing the decoder
          alone can't fix that.
        - decoder_params["bit_rate"]/["samp_rate"]: mirrors
          build_fixed_topology()'s RX_ALREADY_AT_BIT_RATE-aware
          derivation (FM0/Miller already decimate internally -> sps=1;
          OOK/BPSK don't -> sps=SAMP_RATE/bit_rate), computed here
          instead of only at scene_to_topology()'s build time, so the
          inspector's (disabled) display is never stale -- previously
          it only ever showed each param's placement-time default,
          regardless of any later switch or edit, which is exactly
          what was reported as "greyed out and stuck".

        notify=False is used from NodeInspector.show_node() itself (a
        silent recompute right before displaying, so opening or
        switching a receiver always shows the truth) to avoid it
        re-triggering its own display refresh and recursing; notify=True
        (the default) is used from scene_to_topology(), which also
        surfaces a status-bar message when something had to be
        auto-corrected.
        """
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
        # EdgeItem.role/channel_params and PLACEABLE_NODE_TYPES' module
        # comment) -- auto-create and wire that ONE edge's own "channel"
        # node here, the same auto-wiring precedent as the
        # Tx_Leakage/packet_hex_decoder wiring below, instead of relying
        # on an explicit channel box + two hand-drawn wires the way an
        # OLD saved layout still can (those edges have role/channel_params
        # None -- see _edge_channel_role() -- and just pass through
        # unchanged in the else branch, same as always). Interference_In
        # is deliberately left unconnected on every one of these --
        # plan_topology() already zero-sources any channel's unconnected
        # Interference_In on its own (see its docstring's "needs_zero_
        # source" bullet), so there's nothing to add for that here.
        # channel_counter guarantees a unique id per EDGE even in an
        # unusual fan-in (two edges into the same tag's Carrier_In, say)
        # rather than naming two different channels the same thing.
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
            nodes.append({"id": channel_id, "type": "channel", "params": dict(e.channel_params)})
            edges.append((e.from_node.node_id, e.from_port_name, channel_id, "Signal_In"))
            edges.append((channel_id, "out", e.to_node.node_id, e.to_port_name))

        # Auto-wire every sender's Tx_Leakage_Out to every receiver's
        # Tx_Leakage_In -- see AUTO_WIRED_PORTS' module-level comment.
        # BackscatterMiddleware/build_fixed_topology both connect this
        # unconditionally themselves; the canvas previously made you
        # draw it by hand, which is also what made it easy to mis-wire
        # (see NodeItem.PORT_SPACING's comment on the port-proximity fix).
        senders = [n for n in self.nodes.values() if n.type_name == "sender"]
        receivers = [n for n in self.nodes.values() if n.type_name.startswith("rx_")]
        for sender in senders:
            for rx in receivers:
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

            # Text/file-mode tags (see NodeItem.payload_mode/
            # message_framer.py): build each one's actual multi-fragment
            # bitstream here, now that upstream_tags for THIS receiver is
            # known -- tag_id is just this tag's index among the
            # receiver's own upstream tags (0..N-1), unique enough to
            # tell them apart ONLY within this one receiver's
            # reassembly, not globally across the canvas, which is all
            # FragmentReassembler ever needs (see its docstring).
            # Overwrites/adds onto the plain dict the top loop already
            # built for this tag (via node_dicts) rather than a second
            # pass over self.nodes. A raw-mode tag's dict is untouched --
            # plan_topology() reads its existing "payload" key exactly
            # as before. File mode uses build_fragments_from_bytes()
            # directly on the already-read file_bytes (never re-reads
            # the original file from disk) -- otherwise identical to
            # text mode, see that function's docstring.
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
            nodes.append(n)
        # Each edge saves as a plain [from, from_port, to, to_port] list,
        # same shape as before, UNLESS it actually carries a channel (see
        # EdgeItem.role/channel_params), in which case it's the dict form
        # below instead -- so an edited distance/fading/noise value on a
        # tag's channel round-trips through Save/Load instead of
        # silently resetting back to its seeded default. _load_layout_dict
        # below accepts both shapes, so an OLD saved layout (every edge
        # a plain list) still loads unchanged.
        edges = []
        for e in self.edges:
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
        self._node_counter = defaultdict(int)
        self.inspector.show_node(None)

        for n in data.get("nodes", []):
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
            )
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
        self.mw.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.validate_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.timer.start()

    def on_stop(self):
        self.timer.stop()
        if self.mw is not None:
            self.mw.stop()
            self.mw = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.validate_button.setEnabled(True)
        self.clear_button.setEnabled(True)

    def on_clear(self):
        if self.mw is not None:
            return
        self.view.scene().clear()
        self.nodes = {}
        self.edges = []
        self._node_counter = defaultdict(int)
        self.inspector.show_node(None)

    def _rebuild_decoder_docks(self):
        """(Re)builds one QDockWidget per receiver currently on the canvas,
        each holding that receiver's OWN decoded-packet table, replacing
        whatever docks the previous run left behind. Called from
        on_start() -- not kept in sync incrementally as nodes are
        added/removed/renamed -- because the decoder_id naming
        (f"decoder_{rx.node_id}") this relies on is only meaningful for
        an actual run, and a stale tab for a receiver deleted since the
        last run would just be confusing to leave sitting there.

        Docks, not a plain QTabWidget: Qt tabifies dock widgets added to
        the same area into what looks exactly like an ordinary tab bar
        by default (see the tabifyDockWidget() calls below), but each
        tab can also be dragged off that bar into its own free-floating
        top-level window, and dragged back to re-tabify it -- which is
        exactly the "separate tabs, but let me pop one out into its own
        window" ask, for free, with no custom detachable-tab code of our
        own to write or maintain.

        Each dock's widget is actually a small container holding TWO
        tables, not one: the per-fragment log on top (unchanged), and a
        second, smaller "Reassembled messages" table underneath, fed by
        pkt["message"] in poll_packets() -- see message_framer.py and
        TopologyMiddleware.get_packets()'s own docstring. Stays empty
        for an ordinary raw-bits receiver with no text-mode tag feeding
        it; that's fine, it just never gets a row.
        """
        for dock in self.decoder_docks.values():
            self.removeDockWidget(dock)
            dock.deleteLater()
        self.decoder_docks = {}
        self.decoder_tables = {}
        self.message_tables = {}

        receivers = [n for n in self.nodes.values() if n.type_name.startswith("rx_")]
        prev_dock = None
        for rx in receivers:
            decoder_id = f"decoder_{rx.node_id}"

            table = QtWidgets.QTableWidget(0, 4)
            table.setHorizontalHeaderLabels(["Timestamp", "Bits", "Hex", "ASCII"])
            table.horizontalHeader().setStretchLastSection(True)

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

            fragments_pane = QtWidgets.QWidget()
            fragments_layout = QtWidgets.QVBoxLayout(fragments_pane)
            fragments_layout.setContentsMargins(0, 0, 0, 0)
            fragments_layout.addWidget(QtWidgets.QLabel("Fragments"))
            fragments_layout.addWidget(table)

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

    def poll_packets(self):
        """Drains the backend (self.DECODER_POLL_INTERVAL_MS-ly, not on every
        single packet) and refreshes each receiver's own table.

        This used to insert one row PER packet, each with its own
        insertRow()/repaint/scrollToBottom() -- fine at a trickle, but a
        busy decoder can hand back hundreds of packets in one poll, and
        that turned into hundreds of individual table relayouts right as
        you're trying to switch tabs. Now: packets are grouped by their
        destination table first, each table's whole batch is inserted
        with repainting suspended (setUpdatesEnabled(False)) and exactly
        ONE scrollToBottom() at the end, and MAX_DECODER_ROWS caps how
        many rows a table is allowed to keep so it doesn't grow into
        something expensive to paint on its own, hours into a run.

        A completed reassembled message (pkt["message"], not None only
        on the one fragment that completes it -- see TopologyMiddleware.
        get_packets()'s own docstring) goes into that SAME receiver's
        separate, smaller "Reassembled messages" table, batched/capped
        the exact same way -- symmetric handling, even though a message
        row is rare compared to fragment rows (one per completed loop
        of a tag's message, not one per fragment), so it was never
        actually the bottleneck the batching/cap was built for."""
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
                    table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"{pkt['timestamp']:.3f}"))
                    table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(pkt['bits'])))
                    table.setItem(row, 2, QtWidgets.QTableWidgetItem(pkt['hex']))
                    table.setItem(row, 3, QtWidgets.QTableWidgetItem(pkt['ascii']))
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
