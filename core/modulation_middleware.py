"""
Registry-driven version of MultiTagOokMiddleware: builds the same
topology (throttle -> fwd channel -> fan-out to tags -> fan-in adder ->
ret channel -> rx -> decoder) but picks tag/rx classes from
registry.MODULATION_MAP/NODE_TYPES instead of hardcoding OOK's imports.
This is what makes the modulation dropdown possible. Additive --
multi_tag_middleware.py/MultiTagOokMiddleware is untouched.

Gotchas carried over from debugging each modulation:
- packet_bits/p_th (DEFAULT_TAG_OVERRIDES) must be raised for ALL
  modulations, not just OOK -- the registry's un-tuned defaults give a
  burst too short for any receiver to catch.
- FM0/Miller's rx already decimates to one sample/bit internally; see
  RX_ALREADY_AT_BIT_RATE -- building their decoder at full SAMP_RATE
  double-decimates and yields garbage/nothing.
- Analog tag type is unsupported (continuous Signal_In, not a bitstream)
  -- raises NotImplementedError rather than building something wrong.
- SI sign bug in rx_* blocks: fixed directly in the Tier-1 blocks
  (2026-10-22), not here.
- Known, not-yet-fixed: every tag's internal PIE-decoder timing pinned
  to bit_rate=10e3.
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

# Burst-timing overrides, applied to every modulation -- tag_pie_decoder/
# harvesting_gate are shared sub-blocks inside every tag_* hier-block, so
# these aren't OOK-specific tuning. Validated against OOK; better than the
# registry default (packet_bits=16, too short to ever be caught).
DEFAULT_TAG_OVERRIDES = {
    "packet_bits": 10000,
    "p_th": 0.5,
}

# si_isolation_db is tuned per-modulation DETECTOR TYPE: OOK's envelope
# detector needs ~100dB (1-25dB all broken); phase-based schemes (BPSK/
# FM0/Miller) are fine at ~25dB since their DC blocker removes constant
# residual exactly. ui/main_window.py imports this same dict so the two
# can't drift apart.
SI_ISOLATION_DEFAULTS = {"OOK": 100.0, "BPSK": 25.0, "FM0": 25.0, "Miller": 25.0}

# rx_fm0/rx_miller already decimate Data_Out to one sample/bit internally,
# unlike rx_ook/rx_bpsk (full sample rate). Building their decoder at
# samp_rate=SAMP_RATE double-decimates -> garbage/nothing captured.
RX_ALREADY_AT_BIT_RATE = {"rx_fm0", "rx_miller"}


def _registry_defaults(type_name):
    return {p.name: p.default for p in registry.NODE_TYPES[type_name].params}


def _build_node(type_name, overrides, runtime_overrides=None):
    """Look up a NODE_TYPES entry, import its class, instantiate with
    registry defaults + overrides. runtime_overrides is for non-Param
    constructor kwargs (e.g. packet_hex_decoder's packet_queue); applied
    last, after the defaults+overrides merge.
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
    Port names are cosmetic only -- gnuradio wiring is always positional
    (self.connect((block, port_index), ...)); this lets topology specs
    use names instead of raw indices.
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
    """Pure bookkeeping (no gnuradio objects touched): resolves a
    {"nodes", "edges"} topology spec into an ordered connection plan.
    Testable standalone (Test/test_topology_plan.py) without gnuradio.

    Returns a dict:
      "node_types": {node_id: type_name}
      "connects": [((from_id, from_role, from_idx), to_ref), ...] in wire
                  order. from_role is "throttled" only for a sender's
                  port 0 (Carrier_Out) -- port 1 (leakage ref) is not
                  throttled. to_ref is (to_id, to_idx), or
                  (to_id, "fanin", to_idx, group_size) when several edges
                  target the same (node, port) -- caller builds one adder
                  per group.
      "needs_throttle": set of sender node ids.
      "needs_zero_source": (channel id, Interference_In idx) pairs with
                  no edge wired to them.
      "fanin_groups": {(to_id, to_idx): [(from_id, from_idx), ...]}
      "tag_frame_bits": {tag_id: list[0/1]} -- exact bits for that tag's
                  vector_source_b (payload, or every payload_fragments
                  fragment concatenated, each with its preamble). The
                  preamble is resolved PER TAG by walking forward through
                  any channel hops to the first receiver's decoder and
                  reading ITS "preamble" param, not a single hardcoded
                  value -- falls back to PREAMBLE_BITS if no decoder is
                  reachable yet (mid-edit canvas).
      "framed_decoders": packet_hex_decoder ids with >=1 upstream tag
                  transmitting message_framer-framed fragments -- only
                  these attempt message reassembly (see get_packets()).

    Raises KeyError via _port_index on an unknown port name.
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
    modulation: a registry.MODULATION_MAP key -- "OOK"/"BPSK"/"FM0"/
        "Miller" (not "Analog" yet).

    tag_specs: [{"slot_id": int, "payload": [0/1,...]}, ...]. Every
        tag's payload must be the same length (shared decoder reads a
        fixed bit count per preamble match); length is read from the
        specs, not hardcoded.

    distance: moves the RETURN channel only, live. Forward channel is
        fixed at distance=1/noise=0.02.

    si_isolation_db: detector-type dependent, not a tuning nicety --
        OOK needs ~100dB, phase-based schemes (BPSK/FM0/Miller) are fine
        at ~25dB. No single default works for both.

    extra_tag_overrides: merged onto DEFAULT_TAG_OVERRIDES for every tag
        (e.g. {"m": 4}). Any name shared with the rx type (m, bit_rate,
        samp_rate) auto-syncs to the rx.
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

        # FM0/Miller already output one sample/bit (RX_ALREADY_AT_BIT_RATE)
        # -- tell the decoder not to decimate again. OOK/BPSK still get
        # the normal samp_rate/bit_rate decimation.
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
    Builds a gr.top_block from a generic {"nodes", "edges"} spec --
    foundation for the canvas UI. Additive; doesn't change
    BackscatterMiddleware/ui/main_window.py's existing path.

    nodes: [{"id", "type" (a registry.NODE_TYPES key), "params" (optional
        overrides), "payload" (list[0/1], required for tag_* except
        tag_analog -- per-run data, not a registry Param)}, ...]

    edges: [(from_id, from_port_name, to_id, to_port_name), ...]. Port
        names resolve to gnuradio's positional wiring via _port_index().

    Plumbing built automatically (not part of the spec, since no one
    dragging blocks onto a canvas would place these themselves):
      - blocks.throttle after every sender's Carrier_Out (port 0) only --
        Tx_Leakage_Out (port 1) stays unthrottled so the rx's leakage
        reference arrives at full speed.
      - a constant-zero complex source wired into any channel's
        Interference_In that's left unconnected.
      - fan-in: multiple edges into the same (node, port) are summed via
        a sized blocks.add_cc() instead of requiring an explicit adder
        node. Complex ports only -- fanning into byte/float raises
        rather than guessing.
      - one queue.Queue() per packet_hex_decoder node (self.packet_queues,
        keyed by node id); get_packets() tags each drained packet with
        its decoder id so multi-receiver topologies stay attributable.

    Not handled: tag_analog (raises NotImplementedError, different
    payload shape); an unwired required port (raises via gnuradio's own
    hier_block2 check) other than a channel's Interference_In.
    """

    def __init__(self, nodes, edges):
        plan = plan_topology(nodes, edges)
        node_types = plan["node_types"]

        # One real queue.Queue() per decoder node, not one shared across
        # all of them -- see this class's own docstring on packet_queue.
        # Keyed by the decoder's own node id (e.g. "decoder_rx_ook1"),
        # the same id get_packets() tags each drained packet with.
        self.packet_queues = {}
        # One FragmentReassembler per decoder, built unconditionally so
        # get_packets() never has to special-case a missing entry --
        # only actually fed when that decoder is in self._framed_decoders.
        self.reassemblers = {}
        # Which decoders have a framed (text-mode) tag feeding them --
        # resolved by plan_topology()'s "framed_decoders" key.
        self._framed_decoders = set(plan["framed_decoders"])
        # Ground truth for bit-error-rate checking (ui/canvas_window.py's
        # poll_packets()/_compute_packet_ber()): the EXACT bits each tag
        # actually transmits, preamble included, already computed once by
        # plan_topology() to build that tag's vector_source_b in the first
        # place -- kept here (not recomputed) so BER comparisons always
        # match what was literally fed into the flowgraph for this run,
        # not a fresh re-derivation that could drift from it.
        self.tag_frame_bits = plan["tag_frame_bits"]
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
        """Drains every decoder's queue into one flat list, each packet
        tagged with its source decoder_id (added here, not by the
        decoder block itself, so multi-receiver topologies stay
        attributable).

        Each packet also gets a 'message' key: None for an in-progress
        fragment, or {"tag_id", "bytes", "text"} the moment a fragment
        completes reassembly. Only decoders in self._framed_decoders run
        reassembly at all, to avoid a false hit on a receiver nothing
        framed is feeding. 'text' decodes as UTF-8 with errors="replace"
        so corrupted/non-text payloads don't crash the poller.

        Also adds, for every packet (not just the one that completes a
        message): 'sim_header' -- None for a raw/non-framed decoder, or
        {"tag_id", "frag_index", "frag_count", "used_bytes", "crc_ok"}
        for a framed one, straight from message_framer.
        parse_fragment_hex() -- and 'payload_hex'/'payload_ascii', the
        hex/ASCII of ONLY the chunk (i.e. what the user actually typed/
        chose), as opposed to 'hex'/'ascii' which are the decoder's raw
        header+chunk dump. This is purely a display concern (see
        canvas_window.py's Fragments table) -- the reassembler below
        still runs on the full parse, unaffected by any of this. A
        framed packet whose hex fails to even parse (shouldn't happen
        in practice -- the decoder's payload_len_bits is set to exactly
        HEADER_BYTES+chunk_bytes for a framed tag) falls back to
        sim_header=None, payload_hex/ascii == hex/ascii, same as a raw
        decoder -- so a display caller never needs a None-check beyond
        what it already does for sim_header itself."""
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
                pkt["sim_header"] = None
                pkt["payload_hex"] = pkt.get("hex")
                pkt["payload_ascii"] = pkt.get("ascii")

                parsed = message_framer.parse_fragment_hex(pkt.get("hex", "")) if is_framed else None
                if parsed is not None:
                    pkt["sim_header"] = {
                        "tag_id": parsed["tag_id"], "frag_index": parsed["frag_index"],
                        "frag_count": parsed["frag_count"], "used_bytes": parsed["used_bytes"],
                        "crc_ok": parsed["crc_ok"],
                    }
                    pkt["payload_hex"] = " ".join(f"{b:02X}" for b in parsed["chunk"])
                    pkt["payload_ascii"] = "".join(
                        chr(b) if 32 <= b <= 126 else "." for b in parsed["chunk"]
                    )
                    if reassembler is not None:
                        completed = reassembler.feed_parsed(parsed)
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
    Builds the (nodes, edges) TopologyMiddleware spec that reproduces
    BackscatterMiddleware's fixed shape (sender -> fwd channel -> fan out
    to N tags -> fan in -> ret channel -> rx -> decoder), proving
    TopologyMiddleware can fully stand in for it.

    Each *_params dict overrides exactly the node it's named after (e.g.
    `distance` lives under ret_channel_params since it's a channel Param,
    not a top-level concept).

    modulation, tag_specs: same meaning as BackscatterMiddleware's.

    sender_params: defaults to {"enable_gated": 1} -- NOT the raw-block
        default (0), which outputs an un-PIE-modulated carrier and gives
        tags no slot timing at all.

    fwd_channel_params: defaults to {"distance": 1, "noise_volt_c1": 0.02}
        -- BackscatterMiddleware's validated forward-channel values (the
        registry's plain defaults are actually the RETURN channel's).

    ret_channel_params: no extra defaults beyond the registry's own --
        this is the channel meant to be tweaked live via
        get_node("ret_channel").set_distance(...).

    tag_params: merged onto DEFAULT_TAG_OVERRIDES for every tag.

    rx_params: applied after two automatic steps: tag->rx param sync
        (bit_rate/m/samp_rate, whichever names are shared -- pass a
        different value here than tag_params to deliberately mismatch
        them for testing), and si_isolation_db defaulting to
        SI_ISOLATION_DEFAULTS[modulation].

    decoder_params: applied after the automatic bit_rate/payload_len_bits/
        samp_rate derivation (RX_ALREADY_AT_BIT_RATE) -- an escape hatch,
        not for routine use.
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
