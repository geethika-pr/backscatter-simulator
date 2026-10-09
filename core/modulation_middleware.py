"""
Generalized version of MultiTagOokMiddleware -- builds the same proven
topology (throttle -> fwd channel -> fan-out to tags -> fan-in adder ->
ret channel -> rx -> decoder) but picks which tag/rx classes to use from
registry.MODULATION_MAP + registry.NODE_TYPES, instead of hardcoding
OOK's imports the way multi_tag_middleware.py does. This is what makes
"pick a modulation from a dropdown" possible without writing a new
middleware class per modulation.

This file is additive -- multi_tag_middleware.py / MultiTagOokMiddleware
is untouched and still what ui/main_window.py runs. Nothing here changes
that proven path.

IMPORTANT -- what this does and doesn't prove:
  This proves the registry-driven construction mechanism works: the
  right classes get imported, the right ports get wired, the right
  default params get applied, and any param name shared between a tag
  and its rx (bit_rate, m, samp_rate) gets synced automatically instead
  of silently drifting apart the way OOK's did earlier in this project.

  packet_bits and p_th are applied as DEFAULT_TAG_OVERRIDES for every
  modulation, not just OOK -- burst duration (packet_bits controls how
  long tag_pie_decoder holds its gate open) and the harvesting
  threshold are properties of tag_pie_decoder/harvesting_gate, which
  are the same shared sub-blocks nested inside every tag_* hier-block
  regardless of modulation. Leaving them at the registry's un-tuned
  defaults (packet_bits=16 for everything but OOK) produced a burst
  too short for the receiver chain to ever catch -- confirmed by
  testing: Miller captured nothing, BPSK caught a sliver and decoded
  garbage. Neither is a modulation-specific problem, so the fix isn't
  either.

  BPSK's earlier wrong-bit decode was NOT a phase-ambiguity problem --
  once the packet_bits/p_th fix above landed, BPSK decoded correctly.
  It was the same too-short-burst issue as everyone else, nothing
  BPSK-specific.

  FM0 and Miller needed one more fix, confirmed by reading
  backscatter_rx_fm0.py/backscatter_rx_miller.py and their embedded
  epy_blocks directly: both rx_fm0 and rx_miller already decimate
  their own output down to one sample per bit internally (their epy
  decoder is a gr.decim_block with decim=samp_rate/bit_rate) --
  unlike rx_ook/rx_bpsk, whose Data_Out is still at full sample rate.
  packet_hex_decoder was being built with samp_rate=SAMP_RATE
  unconditionally, so for FM0/Miller it was decimating an
  already-decimated, already-one-sample-per-bit stream a second time
  -- effectively sampling noise, which matches exactly what was seen
  (Miller: zero packets; occasional garbage). See
  RX_ALREADY_AT_BIT_RATE below for the fix: for those two rx types,
  packet_hex_decoder is now built with samp_rate=bit_rate (sps=1, no
  further decimation) instead of samp_rate=SAMP_RATE.

  What's still genuinely unverified per-modulation, now that every
  modulation's burst reaches the decoder at the rate it expects, is
  bit-level DEMODULATION correctness -- whether rx_bpsk/rx_fm0/rx_miller
  recover the right bits, not just that packets arrive at all. That's
  a separate question from the plumbing fix here.

  Analog is not supported yet -- its tag block takes a continuous
  Signal_In (float), not the Data_In (byte) bitstream every other
  modulation uses, so it needs a different payload-source block this
  middleware doesn't build yet. Selecting it raises NotImplementedError
  rather than silently building something wrong.

Known, deliberately-not-fixed-yet issues that apply here same as in
multi_tag_middleware.py: the self-interference sign bug present in
every rx_* block (leaked Tx carrier gets added back in instead of
cancelled), and every tag type's internal PIE-decoder timing being
pinned to a hardcoded bit_rate=10e3 regardless of what bit_rate you
pass in. Neither is new to this file, both are already tracked.
"""
import sys
import os
import time
import queue
import importlib
from collections import defaultdict

sys.path.insert(0, "blocks")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # so "import registry" works when run standalone

from gnuradio import gr, blocks

import registry
import message_framer
from backscatter_sender import backscatter_sender
from backscatter_channel_1 import backscatter_channel_1
from packet_hex_decoder import packet_hex_decoder

SAMP_RATE = 2e6
PREAMBLE = "10101011"
PREAMBLE_BITS = [int(b) for b in PREAMBLE]

# Burst-timing overrides, applied to every modulation (see module
# docstring for why this isn't OOK-specific): tag_pie_decoder and
# harvesting_gate are the same shared sub-blocks inside every tag_*
# hier-block, so a burst long enough to be reliably caught, and a
# sane harvesting threshold, are universal requirements, not physics
# tuning specific to OOK's demodulation scheme. Values validated
# against OOK in test_matching_working_canvas.py; not yet validated
# for the others, but definitely better than the un-tuned registry
# default of packet_bits=16 (a 1.6ms burst -- too short to work at all).
DEFAULT_TAG_OVERRIDES = {
    "packet_bits": 10000,
    "p_th": 0.5,
}

# si_isolation_db needs a genuinely different value per modulation's
# DETECTOR TYPE, not a tuning nicety -- see BackscatterMiddleware's class
# docstring for the full validated finding (OOK's envelope/magnitude
# detector has ~zero tolerance for leftover SI-cancellation residual:
# 1-25dB all identically broken, 100dB works; the phase-based schemes
# absorb the same residual fine at ~25dB because their linear detection +
# DC blocker combo removes any constant residual exactly). This lives
# here, not only in ui/main_window.py, so that build_fixed_topology()
# itself picks the right default for whatever modulation you asked for --
# someone testing OOK right after Miller doesn't have to already know OOK
# needs ~100dB to get a working default; ui/main_window.py imports this
# same dict instead of keeping its own copy, so the two can't drift.
SI_ISOLATION_DEFAULTS = {"OOK": 100.0, "BPSK": 25.0, "FM0": 25.0, "Miller": 25.0}

# rx_fm0 and rx_miller's embedded decoder (a gr.decim_block) already
# decimates its Data_Out down to one sample per bit -- unlike
# rx_ook/rx_bpsk, whose Data_Out is still at full sample rate. Building
# packet_hex_decoder with samp_rate=SAMP_RATE for these two makes it
# redundantly decimate an already-one-sample-per-bit stream a second
# time (sps = SAMP_RATE/bit_rate instead of 1), which is why FM0/Miller
# captured nothing/garbage even once the burst was long enough. See
# module docstring for the full diagnosis.
RX_ALREADY_AT_BIT_RATE = {"rx_fm0", "rx_miller"}


def _registry_defaults(type_name):
    return {p.name: p.default for p in registry.NODE_TYPES[type_name].params}


def _build_node(type_name, overrides, runtime_overrides=None):
    """Look up a NODE_TYPES entry, import its class, instantiate it
    with registry defaults + any overrides.

    runtime_overrides is for the NodeType.runtime_params case (currently
    just packet_hex_decoder's packet_queue): a constructor kwarg that
    isn't a registry Param at all, so it's layered on last, after the
    registry-defaults+overrides merge below, and a caller that doesn't
    pass it (every existing BackscatterMiddleware call site) sees no
    change in behavior.
    """
    nt = registry.NODE_TYPES[type_name]
    mod = importlib.import_module(nt.class_module)
    cls = getattr(mod, nt.class_name)
    kwargs = _registry_defaults(type_name)
    kwargs.update(overrides)
    if runtime_overrides:
        kwargs.update(runtime_overrides)
    return cls(**kwargs)


def _port_index(type_name, port_name, direction):
    """Resolve a port NAME to its positional index within that direction.

    Port names are cosmetic/for-display only (see registry.py's module
    note on tag_fm0/tag_miller's "Reflection_Out" vs tag_ook/tag_bpsk's
    plain "out") -- actual gnuradio wiring is always positional, via
    self.connect((block, port_index), ...). This is what lets a topology
    spec refer to "Carrier_In" instead of hardcoding a port number that
    means nothing to a human reading the spec.
    """
    matches = [p for p in registry.NODE_TYPES[type_name].ports if p.direction == direction]
    for idx, p in enumerate(matches):
        if p.name == port_name:
            return idx
    raise KeyError(
        f"{type_name!r} has no {direction} port named {port_name!r}; "
        f"has: {[p.name for p in matches]}"
    )


def plan_topology(nodes, edges):
    """Pure bookkeeping, no gnuradio objects touched -- resolves a
    {"nodes": [...], "edges": [...]} topology spec (see TopologyMiddleware's
    docstring for the exact format) into an ordered connection plan, so the
    "what connects to what" logic can be written and tested once, and
    sanity-checked on its own (see Test/test_topology_plan.py) independently
    of whether gnuradio/the real blocks are even importable.

    Returns a dict:
      "node_types": {node_id: type_name}
      "connects": list of ((from_id, from_role, from_idx), to_ref) in the
                  order they should be wired. from_role is "throttled" only
                  for a sender node's port 0 (Carrier_Out) -- see
                  TopologyMiddleware's docstring for why port 1 isn't.
                  to_ref is (to_id, to_idx) for a plain 1-to-1 connection,
                  or (to_id, "fanin", to_idx, group_size) when more than
                  one edge targets that same (node, port) -- the caller
                  builds one adder per such group instead of connecting
                  straight through.
      "needs_throttle": set of sender node ids.
      "needs_zero_source": set of (channel node id, Interference_In index)
                            pairs whose Interference_In no edge targets.
      "fanin_groups": {(to_id, to_idx): [(from_id, from_idx), ...]}
      "tag_frame_bits": {tag_node_id: list[0/1]} -- the EXACT bits that
                  tag's vector_source_b should loop forever: its own
                  "payload" (or, for a "payload_fragments" tag -- see
                  message_framer.py -- every fragment with the preamble
                  prepended to EACH one, all concatenated) with the
                  right preamble prepended. Missing for a tag_* node
                  that has neither "payload" nor "payload_fragments"
                  (TopologyMiddleware raises its own clear error for
                  that) or for tag_analog (different payload shape
                  entirely, raised elsewhere).
      "framed_decoders": set of packet_hex_decoder node ids with at
                  least one upstream tag transmitting message_framer-
                  framed fragments (not just raw hand-typed bits) --
                  see get_packets()'s own docstring for why only these
                  decoders attempt message reassembly.

    The "right preamble" above is resolved PER TAG by walking the real
    signal graph forward from the tag (through however many "channel"
    hops) until a receiver with its own auto-wired decoder is reached,
    and reading THAT decoder's own "preamble" param -- not a single
    hardcoded value applied to every tag regardless of what any
    receiver's own "Preamble" field says (which is what this used to
    do, as a module-level PREAMBLE_BITS constant baked in at the
    TopologyMiddleware.__init__ call site -- see NodeInspector.
    show_node()'s now-removed tooltip on that field for the bug this
    fixes). A tag with no path to any decoder (mid-edit on the canvas,
    or a hand-built topology that doesn't bother wiring one) falls back
    to PREAMBLE_BITS -- not an error, just nothing real to resolve.

    Raises KeyError via _port_index if a spec names a port that node type
    doesn't have -- fails loudly instead of building something silently
    wrong.
    """
    node_types = {n["id"]: n["type"] for n in nodes}

    resolved = []
    for (from_id, from_port, to_id, to_port) in edges:
        from_idx = _port_index(node_types[from_id], from_port, "out")
        to_idx = _port_index(node_types[to_id], to_port, "in")
        resolved.append((from_id, from_idx, to_id, to_idx))

    by_dest = defaultdict(list)
    for (from_id, from_idx, to_id, to_idx) in resolved:
        by_dest[(to_id, to_idx)].append((from_id, from_idx))

    def _role(from_id, from_idx):
        return "throttled" if node_types[from_id] == "sender" and from_idx == 0 else "direct"

    connects = []
    fanin_groups = {}
    for (to_id, to_idx), sources in by_dest.items():
        if len(sources) == 1:
            (from_id, from_idx) = sources[0]
            connects.append(((from_id, _role(from_id, from_idx), from_idx), (to_id, to_idx)))
        else:
            fanin_groups[(to_id, to_idx)] = sources
            for (from_id, from_idx) in sources:
                connects.append((
                    (from_id, _role(from_id, from_idx), from_idx),
                    (to_id, "fanin", to_idx, len(sources)),
                ))

    needs_throttle = {n_id for n_id, t in node_types.items() if t == "sender"}

    interference_idx = _port_index("channel", "Interference_In", "in")
    needs_zero_source = {
        (n_id, interference_idx)
        for n_id, t in node_types.items()
        if t == "channel" and (n_id, interference_idx) not in by_dest
    }

    # See this function's own docstring on "tag_frame_bits"/
    # "framed_decoders" for what/why -- forward_edges/decoder_of_rx
    # walk the signal graph (not registered port roles, just plain
    # from_id -> to_id reachability) to find, for a given tag, the ONE
    # packet_hex_decoder node that's actually downstream of it.
    forward_edges = defaultdict(list)
    for (from_id, _from_port, to_id, _to_port) in edges:
        forward_edges[from_id].append(to_id)
    decoder_of_rx = {
        from_id: to_id
        for (from_id, from_port, to_id, _to_port) in edges
        if from_port == "Data_Out" and node_types.get(to_id) == "packet_hex_decoder"
    }
    params_by_id = {n["id"]: n.get("params", {}) for n in nodes}

    def _resolve_decoder_id(tag_node_id):
        seen = set()
        frontier = [tag_node_id]
        while frontier:
            nid = frontier.pop()
            if nid in seen:
                continue
            seen.add(nid)
            decoder_id = decoder_of_rx.get(nid)
            if decoder_id is not None:
                return decoder_id
            frontier.extend(forward_edges.get(nid, []))
        return None

    tag_frame_bits = {}
    framed_decoders = set()
    for n in nodes:
        if not n["type"].startswith("tag_"):
            continue
        decoder_id = _resolve_decoder_id(n["id"])
        preamble_str = params_by_id.get(decoder_id, {}).get("preamble") if decoder_id else None
        preamble_bits = [int(b) for b in preamble_str] if preamble_str else PREAMBLE_BITS

        fragments = n.get("payload_fragments")
        if fragments is not None:
            if decoder_id is not None:
                framed_decoders.add(decoder_id)
            frame_bits = []
            for frag in fragments:
                frame_bits += preamble_bits + list(frag)
            tag_frame_bits[n["id"]] = frame_bits
        elif n.get("payload") is not None:
            tag_frame_bits[n["id"]] = preamble_bits + list(n["payload"])
        # else: tag_analog (different payload shape, raised on
        # elsewhere) or a tag with neither key -- left out of
        # tag_frame_bits; TopologyMiddleware raises its own clear
        # "needs a 'payload' entry" error for that case, same as
        # before this existed.

    return {
        "node_types": node_types,
        "connects": connects,
        "needs_throttle": needs_throttle,
        "needs_zero_source": needs_zero_source,
        "fanin_groups": fanin_groups,
        "tag_frame_bits": tag_frame_bits,
        "framed_decoders": framed_decoders,
    }


class BackscatterMiddleware:
    """
    modulation: one of registry.MODULATION_MAP's keys -- "OOK", "BPSK",
    "FM0", "Miller" (not yet "Analog", see module docstring).

    tag_specs: list of dicts, e.g.
        [{"slot_id": 0, "payload": [1,0,1,1,0]},
         {"slot_id": 1, "payload": [1,1,0,0,1]}]
    Every tag's payload must be the same length -- they share one
    decoder that reads a fixed bit count after each preamble match.
    That length is read from tag_specs itself (not a hardcoded
    constant), so this isn't OOK-only the way multi_tag_middleware.py's
    PAYLOAD_LEN_BITS=5 is.

    `distance` moves the RETURN channel only, live, same mechanism as
    MultiTagOokMiddleware -- the forward channel is fixed at the
    validated distance=1/noise=0.02 regardless of modulation.

    `si_isolation_db` needs a very different value depending on
    modulation, not just a tuning nicety: OOK's envelope/magnitude
    detector has essentially zero tolerance for leftover SI-cancellation
    residual (verified: 1-25dB all identically broken, 100dB works),
    while the phase-based schemes (BPSK/FM0/Miller) absorb the same
    residual cleanly at ~25dB because their linear detection + DC
    blocker combo removes any constant residual exactly regardless of
    its size. Pick the value per-modulation; there's no single default
    that's right for both detector types. The self-interference sign
    bug mentioned in the module docstring below is fixed in the current
    rx_* blocks (confirmed against backscatter_rx_ook.py/
    backscatter_rx_bpsk.py) -- what's left is this detector-type
    sensitivity difference, not a code bug.

    `extra_tag_overrides`: optional dict merged on top of
    DEFAULT_TAG_OVERRIDES for every tag, e.g. {"m": 4} to run Miller at
    a non-default order. Anything here whose name also exists on the rx
    type (m, bit_rate, samp_rate) gets synced to the rx automatically,
    same as the existing tag_kwargs->rx_overrides mechanism below.
    """

    def __init__(self, modulation, tag_specs, distance=1, si_isolation_db=100.0,
                 extra_tag_overrides=None):
        if modulation not in registry.MODULATION_MAP:
            raise ValueError(
                f"unknown modulation {modulation!r}, expected one of "
                f"{list(registry.MODULATION_MAP)}"
            )
        if modulation == "Analog":
            raise NotImplementedError(
                "Analog needs a different payload source (Signal_In is a "
                "float waveform, not a bitstream) -- not built yet, see "
                "module docstring."
            )

        payload_lens = {len(spec["payload"]) for spec in tag_specs}
        assert len(payload_lens) == 1, (
            f"all tags must use the same payload length, got {payload_lens}"
        )
        payload_len_bits = payload_lens.pop()

        tag_type = registry.MODULATION_MAP[modulation]["tag_type"]
        rx_type = registry.MODULATION_MAP[modulation]["rx_type"]

        tag_overrides = dict(DEFAULT_TAG_OVERRIDES)
        if extra_tag_overrides:
            tag_overrides.update(extra_tag_overrides)
        tag_kwargs = _registry_defaults(tag_type)
        tag_kwargs.update(tag_overrides)

        # Any param name that exists on BOTH the tag and its rx (bit_rate,
        # m, samp_rate) gets synced from whatever value the tag actually
        # used, so they can't silently drift apart the way OOK's
        # bit_rate/samp_rate did earlier in this project before that got
        # fixed in the .grc.
        rx_param_names = {p.name for p in registry.NODE_TYPES[rx_type].params}
        rx_overrides = {
            name: value for name, value in tag_kwargs.items() if name in rx_param_names
        }
        rx_overrides["si_isolation_db"] = si_isolation_db

        self.packet_queue = queue.Queue()
        self.tb = gr.top_block()
        self._blocks = []   # everything dynamically created lives here,
                             # so nothing gets garbage-collected mid-run

        sender = backscatter_sender(
            carrier_freq_send=0, command_bits=(0, 1, 0, 1), enable_gated=1,
            phase_noise_std_send=0, samp_rate_send=SAMP_RATE, tari=12.5, tx_power_send=20,
        )
        throttle = blocks.throttle(gr.sizeof_gr_complex, SAMP_RATE, True)
        fwd_channel = backscatter_channel_1(
            distance=1, fading_type_c1=0, freq=915e6,          # fixed, matching working canvas
            g_reader_dbi=6.0, g_tag_dbi=2.0, noise_volt_c1=0.02, samp_rate_c1=SAMP_RATE,
        )
        zero_source_a = blocks.vector_source_c([0j], True)
        self.tb.connect((sender, 0), (throttle, 0))
        self.tb.connect((throttle, 0), (fwd_channel, 0))
        self.tb.connect((zero_source_a, 0), (fwd_channel, 1))
        self._blocks += [sender, throttle, fwd_channel, zero_source_a]

        # --- fan-out: every tag gets the same forward-channel output ---
        tag_instances = []
        for spec in tag_specs:
            frame_bits = PREAMBLE_BITS + list(spec["payload"])
            payload_source = blocks.vector_source_b(frame_bits, True)
            tag = _build_node(tag_type, {**tag_overrides, "slot_id": spec["slot_id"]})
            self.tb.connect((fwd_channel, 0), (tag, 0))     # Carrier_In
            self.tb.connect((payload_source, 0), (tag, 1))    # Data_In
            tag_instances.append(tag)
            self._blocks += [payload_source, tag]

        # --- fan-in: sum every tag's output into one adder, always,
        #     even for a single tag, to keep this code path uniform ---
        adder = blocks.add_cc()
        for i, tag in enumerate(tag_instances):
            self.tb.connect((tag, 0), (adder, i))
        self._blocks.append(adder)

        zero_source_b = blocks.vector_source_c([0j], True)
        ret_channel = backscatter_channel_1(
            distance=distance, fading_type_c1=0, freq=915e6,   # live-adjustable, matching working canvas default
            g_reader_dbi=6.0, g_tag_dbi=2.0, noise_volt_c1=0.01, samp_rate_c1=SAMP_RATE,
        )
        self.tb.connect((adder, 0), (ret_channel, 0))
        self.tb.connect((zero_source_b, 0), (ret_channel, 1))
        self._blocks += [zero_source_b, ret_channel]
        self.ret_channel = ret_channel  # named too, for set_distance()

        rx = _build_node(rx_type, rx_overrides)
        self.tb.connect((ret_channel, 0), (rx, 0))
        self.tb.connect((sender, 1), (rx, 1))
        self._blocks.append(rx)

        # FM0/Miller's rx already hands us one sample per bit (see
        # RX_ALREADY_AT_BIT_RATE above) -- tell packet_hex_decoder its
        # input is already at bit_rate so it doesn't decimate again.
        # Everyone else (OOK/BPSK) still hands packet_hex_decoder a
        # full-rate stream, so it does its usual samp_rate/bit_rate
        # decimation as before.
        decoder_samp_rate = (
            tag_kwargs["bit_rate"] if rx_type in RX_ALREADY_AT_BIT_RATE else SAMP_RATE
        )
        decoder = packet_hex_decoder(
            bit_rate=tag_kwargs["bit_rate"], payload_len_bits=payload_len_bits,
            preamble=PREAMBLE, samp_rate=decoder_samp_rate,
        )
        decoder.epy_block_0.packet_queue = self.packet_queue
        self.tb.connect((rx, 0), (decoder, 0))
        self._blocks.append(decoder)

    def start(self):
        self.tb.start()

    def stop(self):
        self.tb.stop()
        self.tb.wait()

    def get_packets(self):
        packets = []
        while True:
            try:
                packets.append(self.packet_queue.get_nowait())
            except queue.Empty:
                break
        return packets

    def set_distance(self, meters):
        """Live update — moves the RETURN channel only (see class docstring)."""
        self.ret_channel.set_distance(meters)


class TopologyMiddleware:
    """
    Builds a gr.top_block from a generic {"nodes", "edges"} description
    instead of BackscatterMiddleware's fixed shape -- the foundation for
    an eventual drag-and-drop canvas UI. Deliberately validated here with
    hand-written topology dicts, no UI involved (see
    Test/test_topology_plan.py for the pure wiring-logic check, and
    Test/test_topology_middleware.py for a real run that reproduces
    BackscatterMiddleware's current 2-tag OOK setup and should decode
    the same way).

    This is additive, same as BackscatterMiddleware was to
    multi_tag_middleware.py -- nothing here changes what ui/main_window.py
    runs today.

    nodes: list of dicts, each
        {"id": str, "type": a key in registry.NODE_TYPES,
         "params": dict (optional; overrides registry defaults),
         "payload": list[0/1] (required for any "tag_*" node except
                     tag_analog; this is per-run DATA, not an RF/timing
                     Param, so it isn't in registry.py -- same thing
                     tag_specs's "payload" key already is for
                     BackscatterMiddleware)}

    edges: list of (from_id, from_port_name, to_id, to_port_name) tuples.
        Port names are whatever registry.NODE_TYPES lists for that node
        type (e.g. "Carrier_In", "Data_Out") -- see plan_topology()/
        _port_index() for how a name resolves to gnuradio's actual
        positional wiring.

    Plumbing that's NOT a node in the spec, built automatically, because
    no one dragging blocks onto a canvas would place these themselves:
      - blocks.throttle after every "sender" node's port-0 output
        (Carrier_Out) only -- matching exactly where BackscatterMiddleware
        puts it today. Port 1 (Tx_Leakage_Out) is deliberately NOT
        throttled, also matching today: the leakage reference needs to
        reach the rx at full speed, unthrottled, same as the existing
        `self.tb.connect((sender, 1), (rx, 1))` line.
      - a constant zero complex source (blocks.vector_source_c([0j], True))
        wired into any "channel" node's Interference_In that no edge
        targets -- today's fwd_channel/ret_channel both leave this
        unconnected to anything real, so a topology that doesn't mention
        Interference_In gets the same thing BackscatterMiddleware already
        builds (zero_source_a / zero_source_b).
      - fan-in: when more than one edge targets the same (node, port),
        they're summed with blocks.add_cc() sized to however many edges
        land there, instead of requiring an explicit adder node. This
        generalizes today's hardcoded "every tag always sums into one
        adder" rule to "any multiply-wired input sums" -- which is the
        natural reading of dragging two tags onto a canvas and wiring both
        to the same channel input. Only complex ports are supported; a
        topology that fans multiple edges into a byte or float port raises
        rather than guessing what that should mean.
      - packet_queue for every "packet_hex_decoder" node: not a registry
        Param (see its runtime_params entry) -- this class creates one
        queue.Queue() PER decoder node (self.packet_queues, keyed by
        that decoder's own node id) and hands each decoder its own.
        get_packets() tags every packet it drains with the decoder id
        it came from, so a topology with more than one receiver -- the
        actual, no-longer-theoretical case canvas_window.py's
        auto-created "decoder_<rx_id>" per receiver produces -- lets the
        caller tell them apart instead of getting one interleaved
        stream with no origin. (This used to be a single shared queue
        with that limitation flagged here as unsolved; it's solved now.)

    NOT handled, same limitation BackscatterMiddleware already has:
      - tag_analog's different Signal_In/float payload shape -- a "type"
        of "tag_analog" raises NotImplementedError, same reasoning as
        BackscatterMiddleware's constructor.
      - a port that's simply never wired and isn't a channel's
        Interference_In (e.g. a tag with no Carrier_In edge) -- raises
        when gnuradio's hier_block2 complains about an unconnected
        required port, rather than silently building something partial.
    """

    def __init__(self, nodes, edges):
        plan = plan_topology(nodes, edges)
        node_types = plan["node_types"]

        # One real queue.Queue() per decoder node, not one shared across
        # all of them -- see this class's own docstring on packet_queue.
        # Keyed by the decoder's own node id (e.g. "decoder_rx_ook1"),
        # the same id get_packets() tags each drained packet with.
        self.packet_queues = {}
        # One message_framer.FragmentReassembler per decoder too (same
        # key) -- only actually FED anything in get_packets() for a
        # decoder_id that ends up in self._framed_decoders below (i.e.
        # at least one upstream tag is in "text" mode and transmitting
        # message_framer-framed fragments, not just raw hand-typed
        # bits); built for every decoder regardless so get_packets()
        # never has to special-case a missing entry.
        self.reassemblers = {}
        # Which decoders actually have a framed (text-mode) tag feeding
        # them -- see get_packets()'s docstring. Resolved by
        # plan_topology() itself now (its own "framed_decoders" key),
        # alongside "tag_frame_bits" (each tag's exact preamble-resolved,
        # fragment-concatenated bits -- see that function's docstring
        # for the full "which decoder governs this tag" story, moved
        # there from here so it's pure, gnuradio-free logic that can be
        # tested on its own, same as everything else plan_topology does).
        self._framed_decoders = set(plan["framed_decoders"])
        self.tb = gr.top_block()
        self._blocks = []
        self.nodes = {}          # node_id -> the real block, for direct
                                  # access (e.g. .set_distance()) until
                                  # this grows its own live-param API
        self._throttled_out = {}  # (sender node_id) -> throttle block

        # pass 1: instantiate every real node from the spec
        for n in nodes:
            type_name = n["type"]
            if type_name == "tag_analog":
                raise NotImplementedError(
                    "tag_analog needs a different payload source "
                    "(Signal_In is a float waveform, not a bitstream) -- "
                    "not built yet, see BackscatterMiddleware's docstring "
                    "for the same limitation."
                )
            params = dict(n.get("params", {}))
            runtime_overrides = None
            if type_name == "packet_hex_decoder":
                decoder_queue = queue.Queue()
                self.packet_queues[n["id"]] = decoder_queue
                self.reassemblers[n["id"]] = message_framer.FragmentReassembler()
                runtime_overrides = {"packet_queue": decoder_queue}
            block = _build_node(type_name, params, runtime_overrides=runtime_overrides)
            self.nodes[n["id"]] = block
            self._blocks.append(block)

            if type_name == "sender":
                throttle = blocks.throttle(gr.sizeof_gr_complex, SAMP_RATE, True)
                self.tb.connect((block, 0), (throttle, 0))
                self._throttled_out[n["id"]] = throttle
                self._blocks.append(throttle)

            if type_name.startswith("tag_"):  # tag_analog already raised above
                frame_bits = plan["tag_frame_bits"].get(n["id"])
                if frame_bits is None:
                    raise ValueError(f"tag node {n['id']!r} needs a 'payload' entry")
                payload_source = blocks.vector_source_b(frame_bits, True)
                data_in_idx = _port_index(type_name, "Data_In", "in")
                self.tb.connect((payload_source, 0), (block, data_in_idx))
                self._blocks.append(payload_source)

        # pass 2: zero-source any channel's unconnected Interference_In
        for (node_id, port_idx) in plan["needs_zero_source"]:
            zero_source = blocks.vector_source_c([0j], True)
            self.tb.connect((zero_source, 0), (self.nodes[node_id], port_idx))
            self._blocks.append(zero_source)

        # pass 3: build one adder per fan-in group, sized to that group
        adders = {}
        for (to_id, to_idx), sources in plan["fanin_groups"].items():
            adder = blocks.add_cc()
            adders[(to_id, to_idx)] = adder
            self.tb.connect((adder, 0), (self.nodes[to_id], to_idx))
            self._blocks.append(adder)

        # pass 4: wire every connection the plan resolved
        fanin_slot = defaultdict(int)
        for (from_ref, to_ref) in plan["connects"]:
            from_id, from_role, from_idx = from_ref
            from_block = self._throttled_out[from_id] if from_role == "throttled" else self.nodes[from_id]
            from_port = 0 if from_role == "throttled" else from_idx

            if len(to_ref) == 2:
                to_id, to_idx = to_ref
                self.tb.connect((from_block, from_port), (self.nodes[to_id], to_idx))
            else:
                to_id, _fanin, to_idx, _size = to_ref
                adder = adders[(to_id, to_idx)]
                slot = fanin_slot[(to_id, to_idx)]
                fanin_slot[(to_id, to_idx)] += 1
                self.tb.connect((from_block, from_port), (adder, slot))

    def start(self):
        self.tb.start()

    def stop(self):
        self.tb.stop()
        self.tb.wait()

    def get_packets(self):
        """Drains every decoder's own queue (see self.packet_queues) and
        returns one flat list, each packet dict tagged with a
        'decoder_id' key -- the packet_hex_decoder node id it actually
        came from (e.g. "decoder_rx_ook1", canvas_window.py's own
        "decoder_<rx_node_id>" naming) -- so a caller with more than one
        receiver on the canvas can tell them apart instead of getting
        one merged, unattributed stream. The tag is added HERE, not by
        the decoder block itself -- packet_hex_decoder_epy_block_0.py
        doesn't need to know its own node id at all, it just drains
        whichever queue.Queue() this class handed it at construction.

        Each packet also gets a 'message' key -- None on every fragment
        that doesn't, on its own, complete a message (the common case:
        most fragments just join a still-in-progress buffer), or
        {"tag_id": int, "bytes": bytes, "text": str} the moment THIS
        fragment is the one that completes its tag's message (see
        message_framer.FragmentReassembler.feed_hex()). Only decoders in
        self._framed_decoders (at least one upstream tag is actually
        transmitting message_framer-framed fragments, not just raw
        hand-typed bits) run their fragments through reassembly at all
        -- an ordinary raw-bits tag's hex essentially never happens to
        also look like a valid header+CRC, but there's no reason to
        even risk a false "reassembled" hit on a receiver nothing framed
        is feeding. 'text' decodes the reassembled bytes as UTF-8 with
        errors="replace" rather than raising, since a header corrupted
        in a way the CRC didn't catch (the CRC only covers the chunk,
        not tag_id/frag_index/frag_count/used_bytes themselves) or
        genuinely non-text payload data shouldn't take the whole poller
        down with it."""
        packets = []
        for decoder_id, decoder_queue in self.packet_queues.items():
            is_framed = decoder_id in self._framed_decoders
            reassembler = self.reassemblers.get(decoder_id)
            while True:
                try:
                    pkt = decoder_queue.get_nowait()
                except queue.Empty:
                    break
                pkt = dict(pkt)
                pkt["decoder_id"] = decoder_id
                pkt["message"] = None
                if is_framed and reassembler is not None:
                    completed = reassembler.feed_hex(pkt.get("hex", ""))
                    if completed is not None:
                        tag_id, message_bytes = completed
                        pkt["message"] = {
                            "tag_id": tag_id,
                            "bytes": message_bytes,
                            "text": message_bytes.decode("utf-8", errors="replace"),
                        }
                packets.append(pkt)
        return packets

    def get_node(self, node_id):
        """Direct access to a built block, e.g. for
        get_node("ret_ch").set_distance(5) -- no generic live-param
        wrapper yet, same raw-object access BackscatterMiddleware's own
        self.ret_channel already relies on for set_distance()."""
        return self.nodes[node_id]


def build_fixed_topology(modulation, tag_specs, sender_params=None,
                          fwd_channel_params=None, ret_channel_params=None,
                          tag_params=None, rx_params=None, decoder_params=None):
    """
    Builds the (nodes, edges) spec for TopologyMiddleware that reproduces
    BackscatterMiddleware's fixed shape -- sender -> fwd channel -> fan out
    to N tags -> fan in -> ret channel -> rx -> decoder -- as a
    TopologyMiddleware topology instead of BackscatterMiddleware's own
    hardcoded gr.top_block construction. This is what proves
    TopologyMiddleware can fully stand in for BackscatterMiddleware, not
    just run alongside it.

    Every *_params argument is a dict of overrides for exactly the node it's
    named after -- nothing here is split into some separate top-level
    "common params" tier. `distance` is a ret_channel_params entry because
    it IS a channel param (see registry.NODE_TYPES["channel"]), not a
    BackscatterMiddleware-level concept; same for si_isolation_db under
    rx_params. BackscatterMiddleware's own flat distance=/si_isolation_db=
    kwargs aren't a real category scheme -- they exist because that's
    specifically what ui/main_window.py's interim form needs direct access
    to (one Setup field, one Live-adjustable field), not a reflection of
    which params matter more. A caller that wants that same shortcut can
    still build it one layer up (e.g. `ret_channel_params={"distance": d}`),
    this function just doesn't bake the shortcut in.

    modulation, tag_specs: same meaning as BackscatterMiddleware's own
        (modulation is one of registry.MODULATION_MAP's keys, not yet
        "Analog"; tag_specs is [{"slot_id":.., "payload":[0/1,...]}, ...],
        every tag's payload the same length).

    sender_params: overrides for the one sender node. Defaults to
        {"enable_gated": 1} -- NOT the registry/raw-block default (0): see
        backscatter_sender.py's blocks_selector_0 -- enable_gated=0 outputs
        the raw, never-PIE-modulated carrier, which gives tags no slot
        timing at all (confirmed by hand the hard way in
        Test/test_topology_middleware.py's first draft). Baking this in
        here means a caller can no longer forget it the way that draft did.

    fwd_channel_params: overrides for the forward channel. Defaults to
        {"distance": 1, "noise_volt_c1": 0.02} -- BackscatterMiddleware's
        own fixed, validated forward-channel values ("matching working
        canvas" per its comment), not the registry's plain defaults (which
        are actually the RETURN channel's values).

    ret_channel_params: overrides for the return channel. No defaults
        baked in beyond the registry's own (distance=2, noise_volt_c1=0.01)
        -- this is the channel meant to be tweaked, live-adjustable via
        TopologyMiddleware.get_node("ret_channel").set_distance(...), same
        mechanism as BackscatterMiddleware.set_distance().

    tag_params: overrides merged on top of DEFAULT_TAG_OVERRIDES for every
        tag (e.g. {"m": 4} for Miller) -- same role as
        BackscatterMiddleware's extra_tag_overrides.

    rx_params: overrides for the rx node, applied AFTER two automatic
        steps: the tag->rx param sync (bit_rate/m/samp_rate, whichever
        names the tag and rx types actually share -- same sync
        BackscatterMiddleware already does, so they can't silently drift
        apart; pass a DIFFERENT value here than tag_params's to deliberately
        mismatch them, e.g. tag m=2 vs rx m=8, to test what a mismatch
        actually does), and si_isolation_db defaulting to
        SI_ISOLATION_DEFAULTS[modulation] (so switching modulations gets
        the right isolation value without the caller needing to already
        know it -- pass it here to override that default).

    decoder_params: overrides for the decoder node, applied AFTER the
        automatic bit_rate/payload_len_bits/samp_rate derivation (the
        RX_ALREADY_AT_BIT_RATE logic, same as BackscatterMiddleware) --
        an escape hatch, not something meant to be set routinely; matches
        the "not up to the user much" expectation already true today.
    """
    if modulation not in registry.MODULATION_MAP:
        raise ValueError(
            f"unknown modulation {modulation!r}, expected one of "
            f"{list(registry.MODULATION_MAP)}"
        )
    if modulation == "Analog":
        raise NotImplementedError(
            "Analog needs a different payload source (Signal_In is a "
            "float waveform, not a bitstream) -- not built yet, see "
            "module docstring."
        )

    payload_lens = {len(spec["payload"]) for spec in tag_specs}
    assert len(payload_lens) == 1, (
        f"all tags must use the same payload length, got {payload_lens}"
    )
    payload_len_bits = payload_lens.pop()

    tag_type = registry.MODULATION_MAP[modulation]["tag_type"]
    rx_type = registry.MODULATION_MAP[modulation]["rx_type"]

    tag_overrides = dict(DEFAULT_TAG_OVERRIDES)
    if tag_params:
        tag_overrides.update(tag_params)
    tag_kwargs = _registry_defaults(tag_type)
    tag_kwargs.update(tag_overrides)

    rx_param_names = {p.name for p in registry.NODE_TYPES[rx_type].params}
    rx_overrides = {name: value for name, value in tag_kwargs.items() if name in rx_param_names}
    # si_isolation_db's correct value depends on the modulation's detector
    # type (see SI_ISOLATION_DEFAULTS above), not on anything tag-side, so
    # it's not part of the tag->rx sync above -- baked in as a default
    # here instead, same way enable_gated/noise_volt_c1 are baked in for
    # sender/fwd_channel, and just as overridable via rx_params.
    rx_overrides["si_isolation_db"] = SI_ISOLATION_DEFAULTS.get(modulation, 25.0)
    if rx_params:
        rx_overrides.update(rx_params)

    decoder_samp_rate = tag_kwargs["bit_rate"] if rx_type in RX_ALREADY_AT_BIT_RATE else SAMP_RATE
    decoder_overrides = {
        "bit_rate": tag_kwargs["bit_rate"], "payload_len_bits": payload_len_bits,
        "preamble": PREAMBLE, "samp_rate": decoder_samp_rate,
    }
    if decoder_params:
        decoder_overrides.update(decoder_params)

    sender_overrides = {"enable_gated": 1}
    if sender_params:
        sender_overrides.update(sender_params)

    fwd_channel_overrides = {"distance": 1, "noise_volt_c1": 0.02}
    if fwd_channel_params:
        fwd_channel_overrides.update(fwd_channel_params)

    ret_channel_overrides = dict(ret_channel_params) if ret_channel_params else {}

    nodes = [
        {"id": "sender", "type": "sender", "params": sender_overrides},
        {"id": "fwd_channel", "type": "channel", "params": fwd_channel_overrides},
    ]
    edges = [("sender", "Carrier_Out", "fwd_channel", "Signal_In")]

    # tag_fm0/tag_miller name their one output port "Reflection_Out", not
    # "out" like tag_ook/tag_bpsk/tag_analog (cosmetic naming quirk, see
    # registry.py's module note) -- every tag type has exactly one output
    # port, so look its real name up instead of assuming "out" and
    # breaking silently for those two.
    tag_out_ports = [p.name for p in registry.NODE_TYPES[tag_type].ports if p.direction == "out"]
    assert len(tag_out_ports) == 1, f"expected exactly one output port on {tag_type!r}, got {tag_out_ports}"
    tag_out_port = tag_out_ports[0]

    for i, spec in enumerate(tag_specs):
        tag_id = f"tag{i}"
        nodes.append({
            "id": tag_id, "type": tag_type, "payload": spec["payload"],
            "params": {**tag_overrides, "slot_id": spec["slot_id"]},
        })
        edges.append(("fwd_channel", "out", tag_id, "Carrier_In"))

    nodes += [
        {"id": "ret_channel", "type": "channel", "params": ret_channel_overrides},
        {"id": "rx", "type": rx_type, "params": rx_overrides},
        {"id": "decoder", "type": "packet_hex_decoder", "params": decoder_overrides},
    ]
    for i in range(len(tag_specs)):
        edges.append((f"tag{i}", tag_out_port, "ret_channel", "Signal_In"))
    edges += [
        ("ret_channel", "out", "rx", "Antenna_In"),
        ("sender", "Tx_Leakage_Out", "rx", "Tx_Leakage_In"),
        ("rx", "Data_Out", "decoder", "Data_In"),
    ]

    return nodes, edges


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modulation", default="OOK",
        choices=[m for m in registry.MODULATION_MAP if m != "Analog"],
    )
    args = parser.parse_args()

    tag_specs = [
        {"slot_id": 0, "payload": [1, 0, 1, 1, 0]},
        {"slot_id": 1, "payload": [1, 0, 0, 1, 0]},
    ]
    mw = BackscatterMiddleware(args.modulation, tag_specs)

    print(f"Starting flowgraph ({args.modulation}) with {len(tag_specs)} tags...")
    mw.start()
    try:
        for _ in range(10):
            time.sleep(1)
            packets = mw.get_packets()
            if packets:
                for pkt in packets:
                    print("PACKET:", pkt)
            else:
                print("...(no packets this second)")
    except KeyboardInterrupt:
        pass
    finally:
        print("Stopping flowgraph...")
        mw.stop()


if __name__ == "__main__":
    main()
