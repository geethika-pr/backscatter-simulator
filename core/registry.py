

from dataclasses import dataclass, field


@dataclass
class Port:
    name: str            # must match the pad_source/pad_sink label in the .grc
    direction: str        # "in" or "out"
    dtype: str             # "complex", "byte", "float" — matches the pad's GRC type


@dataclass
class Param:
    name: str            # must match the GRC parameter block's id (the variable name)
    default: object
    basic: bool           # True = shown by default, False = behind "advanced"
    label: str = ""         # human-readable label, for the UI


@dataclass
class NodeType:
    class_module: str    # filename (without .py) inside blocks/
    class_name: str       # the class name inside that module
    ports: list           # list[Port]
    params: list          # list[Param]
    # Param NAMES (not Param objects) that the constructor accepts but that
    # are NOT GRC parameters -- no value field, no default a canvas/UI would
    # ever show. The builder has to supply these itself at construction time
    # (e.g. a live queue.Queue() instance), never from user-entered params or
    # a topology spec's "params" dict. Currently just packet_hex_decoder's
    # packet_queue -- see its entry below.
    runtime_params: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# NODE_TYPES — one entry per placeable block type.
#
# All param names/defaults below were pulled directly from each block's
# compiled blocks/*.py constructor signature and .block.yml (ground truth,
# not guessed). One real naming inconsistency left to know about, not a
# bug, just how the .grc canvas was drawn:
#   - tag_fm0 / tag_miller's third (output) port is named "Reflection_Out",
#     not "out" like tag_ook/tag_bpsk/tag_analog. Cosmetic only — Python
#     wiring is positional (self.connect((a, 0), (b, 0))), so the label
#     never affects behavior, only what a future topology-canvas UI would
#     display on that connector.
#   (tag_fm0/tag_miller's bit_rate/samp_rate used to be renamed
#   bit_rate_tag_fm0/samp_rate_tag_fm0 etc. -- since fixed in GRC and
#   regenerated, all five tag types now use the same plain names.)
#
# tag_analog is a genuinely different shape, not just renamed: its second
# input is "Signal_In" (float), not "Data_In" (byte) like the other four.
# It takes a continuous analog waveform, not a bitstream — a generic
# "wire a list of 0/1 bits into Data_In" builder will NOT work for this
# one unchanged. Left as a known gap for whenever Analog support is
# actually built, not solved here.
#
# basic/advanced split below follows tag_ook's existing pattern
# (identity/scheduling params basic, RF-physics tuning params advanced) --
# a first pass, not final; easy to move things between the two lists
# later once the UI is actually surfacing them.
# ---------------------------------------------------------------------------

NODE_TYPES = {

    "tag_ook": NodeType(
        class_module="backscatter_tag_ook",
        class_name="backscatter_tag_ook",
        ports=[
            Port("Carrier_In", "in", "complex"),
            Port("Data_In", "in", "byte"),
            Port("out", "out", "complex"),
        ],
        params=[
            # --- basic: what a first-time user actually needs to touch ---
            Param("slot_id", 0, basic=True, label="Assigned Slot ID"),
            Param("packet_bits", 16000, basic=True, label="Number of Packet Bits"),
            Param("bit_rate", 10e3, basic=True, label="Bit Rate"),
            Param("enable_gated", 1, basic=True, label="Gated Mode"),

            # --- advanced: physical/RF tuning, hidden by default ---
            Param("p_th", 0.001, basic=False, label="Harvesting Threshold"),
            Param("alpha", 0.01, basic=False, label="Alpha Factor"),
            Param("as_real", 0.05, basic=False, label="Structural Scattering (Real)"),
            Param("as_imag", 0.0, basic=False, label="Structural Scattering (Imag)"),
            Param("gamma0_mag", 0.1, basic=False, label="Gamma 0 Magnitude"),
            Param("gamma0_phase_deg", 180.0, basic=False, label="Gamma 0 Phase"),
            Param("gamma1_mag", 0.9, basic=False, label="Gamma 1 Magnitude"),
            Param("gamma1_phase_deg", 180.0, basic=False, label="Gamma 1 Phase"),

            # --- shared/system, not usually user-facing at the node level ---
            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    "tag_bpsk": NodeType(
        class_module="backscatter_tag_bpsk",
        class_name="backscatter_tag_bpsk",
        ports=[
            Port("Carrier_In", "in", "complex"),
            Port("Data_In", "in", "byte"),
            Port("out", "out", "complex"),
        ],
        params=[
            Param("slot_id", 0, basic=True, label="Assigned Slot ID"),
            Param("packet_bits", 16, basic=True, label="Number of Packet Bits"),
            Param("bit_rate", 10e3, basic=True, label="Bit Rate"),
            Param("enable_gated", 1, basic=True, label="Gated Mode"),

            Param("p_th", 0.001, basic=False, label="Harvesting Threshold"),
            Param("alpha", 0.01, basic=False, label="Alpha Factor"),
            Param("as_real", 0.0, basic=False, label="Structural Scattering (Real)"),
            Param("as_imag", 0.0, basic=False, label="Structural Scattering (Imag)"),
            Param("gamma0_mag", 0.8, basic=False, label="Gamma 0 Magnitude"),
            Param("gamma0_phase_deg", 0.0, basic=False, label="Gamma 0 Phase"),
            Param("gamma1_mag", 0.8, basic=False, label="Gamma 1 Magnitude"),
            Param("gamma1_phase_deg", 180.0, basic=False, label="Gamma 1 Phase"),

            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    "tag_fm0": NodeType(
        class_module="backscatter_tag_fm0",
        class_name="backscatter_tag_fm0",
        ports=[
            Port("Carrier_In", "in", "complex"),
            Port("Data_In", "in", "byte"),
            Port("Reflection_Out", "out", "complex"),   # named differently than tag_ook/tag_bpsk -- see module note
        ],
        params=[
            Param("slot_id", 0, basic=True, label="Assigned Slot ID"),
            Param("packet_bits", 16, basic=True, label="Number of Packet Bits"),
            Param("bit_rate", 10e3, basic=True, label="Bit Rate"),
            Param("enable_gated", 1, basic=True, label="Gated Mode"),

            Param("p_th", 0.001, basic=False, label="Harvesting Threshold"),
            Param("alpha", 0.01, basic=False, label="Alpha Factor"),
            Param("as_real", 0.05, basic=False, label="Structural Scattering (Real)"),
            Param("as_imag", 0.02, basic=False, label="Structural Scattering (Imag)"),
            Param("gamma0_mag", 0.2, basic=False, label="Gamma 0 Magnitude"),
            Param("gamma0_phase_deg", 15.0, basic=False, label="Gamma 0 Phase"),
            Param("gamma1_mag", 0.85, basic=False, label="Gamma 1 Magnitude"),
            Param("gamma1_phase_deg", 160.0, basic=False, label="Gamma 1 Phase"),

            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    "tag_miller": NodeType(
        class_module="backscatter_tag_miller",
        class_name="backscatter_tag_miller",
        ports=[
            Port("Carrier_In", "in", "complex"),
            Port("Data_In", "in", "byte"),
            Port("Reflection_Out", "out", "complex"),   # named differently than tag_ook/tag_bpsk -- see module note
        ],
        params=[
            Param("slot_id", 0, basic=True, label="Assigned Slot ID"),
            Param("packet_bits", 16, basic=True, label="Number of Packet Bits"),
            Param("bit_rate", 10e3, basic=True, label="Bit Rate"),
            Param("enable_gated", 1, basic=True, label="Gated Mode"),
            Param("m", 2, basic=True, label="Miller Order (M)"),   # changes the encoding scheme itself

            Param("mod_depth", 1.0, basic=False, label="Modulation Depth"),
            Param("p_th", 0.001, basic=False, label="Harvesting Threshold"),
            Param("alpha", 0.01, basic=False, label="Alpha Factor"),
            Param("as_real", 0.05, basic=False, label="Structural Scattering (Real)"),
            Param("as_imag", 0.02, basic=False, label="Structural Scattering (Imag)"),
            Param("gamma0_mag", 0.2, basic=False, label="Gamma 0 Magnitude"),
            Param("gamma0_phase_deg", 15.0, basic=False, label="Gamma 0 Phase"),
            Param("gamma1_mag", 0.85, basic=False, label="Gamma 1 Magnitude"),
            Param("gamma1_phase_deg", 160.0, basic=False, label="Gamma 1 Phase"),

            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    "tag_analog": NodeType(
        class_module="backscatter_tag_analog",
        class_name="backscatter_tag_analog",
        ports=[
            Port("Carrier_In", "in", "complex"),
            Port("Signal_In", "in", "float"),   # NOT "Data_In" / byte -- see module note, real shape difference
            Port("out", "out", "complex"),
        ],
        params=[
            Param("slot_id", 0, basic=True, label="Assigned Slot ID"),
            Param("packet_bits", 16, basic=True, label="Number of Packet Bits"),
            Param("bit_rate", 10e3, basic=True, label="Bit Rate"),
            Param("enable_gated", 1, basic=True, label="Gated Mode"),
            Param("analog_mode", 0, basic=True, label="Analog Mode"),   # selects preset vs custom gamma below

            Param("mod_depth", 0.8, basic=False, label="Modulation Depth"),
            Param("p_th", 0.001, basic=False, label="Harvesting Threshold"),
            Param("alpha", 0.01, basic=False, label="Alpha Factor"),
            Param("as_real", 0.05, basic=False, label="Structural Scattering (Real)"),
            Param("as_imag", 0.02, basic=False, label="Structural Scattering (Imag)"),
            # note the different naming convention here vs the other four --
            # "custom_g0/g1" not "gamma0/gamma1", real names, not a typo
            Param("custom_g0_magnitude", 0.2, basic=False, label="Custom Gamma 0 Magnitude"),
            Param("custom_g0_phase_deg", 0.0, basic=False, label="Custom Gamma 0 Phase"),
            Param("custom_g1_mag", 0.85, basic=False, label="Custom Gamma 1 Magnitude"),
            Param("custom_g1_phase_deg", 180.0, basic=False, label="Custom Gamma 1 Phase"),

            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    # --- receivers ---
    # All five share the same two input ports (Antenna_In, Tx_Leakage_In).
    # Tx_Leakage_In is a topology-level connection (wired to the sender's
    # tx_ref output, or not, depending on si_enable) -- not a value you'd
    # set through params, see the channel TODO note below and §8 of the
    # project plan (monostatic/bistatic SI toggle is a wiring decision,
    # not a live parameter).

    "rx_ook": NodeType(
        class_module="backscatter_rx_ook",
        class_name="backscatter_rx_ook",
        ports=[
            Port("Antenna_In", "in", "complex"),
            Port("Tx_Leakage_In", "in", "complex"),
            Port("Data_Out", "out", "byte"),
        ],
        params=[
            Param("si_enable", 1, basic=True, label="Enable Self-Interference Cancellation"),
            # dc_len: see rx_bpsk's own dc_len comment below -- same
            # Tier-1 edit (hers, not mine), same reasoning, same generic
            # exposure path. OOK and BPSK are the two modulations that
            # actually need this lever (raw/NRZ transmission can feed
            # the DC blocker a long constant run from EITHER the header
            # -- fixed, see whitening-fix-findings.md -- or, now
            # confirmed in practice, from ordinary payload content that
            # happens to contain a longer run of similar bytes). FM0/
            # Miller's self-clocking line coding never produces that
            # long a run in the first place, which is exactly why she
            # reported no problem there even before this param existed.
            Param("dc_len", 1024, basic=True, label="DC Blocker Length (samples)"),
            Param("si_isolation_db", 25.0, basic=False, label="SI Isolation (dB)"),
            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    "rx_bpsk": NodeType(
        class_module="backscatter_rx_bpsk",
        class_name="backscatter_rx_bpsk",
        ports=[
            Port("Antenna_In", "in", "complex"),
            Port("Tx_Leakage_In", "in", "complex"),
            Port("Data_Out", "out", "byte"),
        ],
        params=[
            Param("si_enable", 1, basic=True, label="Enable Self-Interference Cancellation"),
            # dc_len: backscatter_rx_bpsk.py now exposes the dc_blocker_cc
            # length as a constructor param (Tier-1 edit, not mine --
            # was hardcoded at 1024 before). Exposed here purely so the
            # inspector can drive it without touching GRC: see
            # whitening-fix-findings.md for why this is the actual fix
            # for OOK/BPSK's long-identical-bit-run problem (a DC-blocker
            # droop, independent of TX power) -- a longer window
            # tolerates a longer constant run before the filter decays
            # it toward zero. No canvas_window.py change needed: the
            # params form and build-kwargs path are both already fully
            # generic over NodeType.params (see modulation_middleware.py's
            # _build_node()).
            Param("dc_len", 1024, basic=True, label="DC Blocker Length (samples)"),
            Param("si_isolation_db", 25.0, basic=False, label="SI Isolation (dB)"),
            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    "rx_fm0": NodeType(
        class_module="backscatter_rx_fm0",
        class_name="backscatter_rx_fm0",
        ports=[
            Port("Antenna_In", "in", "complex"),
            Port("Tx_Leakage_In", "in", "complex"),
            Port("Data_Out", "out", "byte"),
        ],
        params=[
            Param("bit_rate", 10000, basic=True, label="Bit Rate"),
            Param("si_enable", 1, basic=True, label="Enable Self-Interference Cancellation"),
            Param("threshold", 0.02, basic=False, label="Slicer Threshold"),
            # dc_len: exposed for consistency with rx_ook/rx_bpsk (same
            # Tier-1 edit, same dc_blocker_cc underneath) -- FM0 doesn't
            # actually NEED this lever day to day. Its self-clocking
            # line coding (a transition every half-bit, regardless of
            # the data bits) never feeds the DC blocker a long constant
            # run the way OOK/BPSK's raw/NRZ transmission can, which is
            # exactly why no OOK/BPSK-style problem showed up on this
            # receiver even before this param existed. Here mainly so
            # it's available to experiment with if a future edge case
            # ever needs it.
            Param("dc_len", 1024, basic=True, label="DC Blocker Length (samples)"),
            Param("si_isolation_db", 25.0, basic=False, label="SI Isolation (dB)"),
            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
            # The embedded FM0 decoder now re-syncs once per packet instead
            # of trusting a single lock forever (fixes it decoding a later,
            # unrelated tag's burst at a stale phase) -- it needs to know
            # exactly how long one packet is to do that, hence these two.
            # canvas_window.py's _sync_decoder_params() keeps payload_len
            # derived from this receiver's own upstream tag(s), same as it
            # already does for decoder_params["payload_len_bits"].
            Param("payload_len", 5, basic=False, label="Payload Length (bits)"),
            Param("preamble", "10101011", basic=False, label="Preamble"),
        ],
    ),

    "rx_miller": NodeType(
        class_module="backscatter_rx_miller",
        class_name="backscatter_rx_miller",
        ports=[
            Port("Antenna_In", "in", "complex"),
            Port("Tx_Leakage_In", "in", "complex"),
            Port("Data_Out", "out", "byte"),
        ],
        params=[
            Param("bit_rate", 10000, basic=True, label="Bit Rate"),
            Param("m", 2, basic=True, label="Miller Order (M)"),   # must match the tag's own m
            Param("si_enable", 1, basic=True, label="Enable Self-Interference Cancellation"),
            # dc_len: see rx_fm0's own dc_len comment above -- same
            # reasoning applies here (Miller is self-clocking too, so
            # this isn't a lever Miller actually needs for the OOK/
            # BPSK-style long-run problem), exposed for consistency and
            # future experimentation.
            Param("dc_len", 1024, basic=True, label="DC Blocker Length (samples)"),
            Param("si_isolation_db", 25.0, basic=False, label="SI Isolation (dB)"),
            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
            # See rx_fm0's own payload_len/preamble comment -- same fix,
            # same reason, same auto-sync from _sync_decoder_params().
            Param("payload_len", 5, basic=False, label="Payload Length (bits)"),
            Param("preamble", "10101011", basic=False, label="Preamble"),
        ],
    ),

    "rx_analog": NodeType(
        class_module="backscatter_rx_analog",
        class_name="backscatter_rx_analog",
        ports=[
            Port("Antenna_In", "in", "complex"),
            Port("Tx_Leakage_In", "in", "complex"),
            Port("Data_Out", "out", "float"),   # float, not byte -- the odd one out, matches tag_analog's float side
        ],
        params=[
            Param("si_enable", 1, basic=True, label="Enable Self-Interference Cancellation"),
            Param("si_isolation_db", 25.0, basic=False, label="SI Isolation (dB)"),
            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    # --- channel, sender, pie_decoder, packet_hex_decoder ---
    # Pulled from the actual compiled blocks/*.py constructors the same way
    # as everything above (ground truth, not guessed) -- these are the
    # pieces shared across every modulation rather than swapped per-tag/rx,
    # so BackscatterMiddleware's fixed topology already builds exactly one
    # of each. Listed here now so a future generic nodes+edges topology
    # builder can place/wire them the same way it places tags and rx's,
    # instead of them staying hardcoded.

    "channel": NodeType(
        class_module="backscatter_channel_1",
        class_name="backscatter_channel_1",
        ports=[
            # Signal_In gets scaled by the distance-derived friis_gain
            # multiply inside this block; Interference_In is summed in
            # AFTER that scaling (confirmed from the current wiring:
            # blocks_add_xx_0 adds the post-friis-gain, fading-selected
            # path to the raw, unscaled self-input-1) -- reflects the
            # actual .grc wiring today, not an assumption.
            Port("Signal_In", "in", "complex"),
            Port("Interference_In", "in", "complex"),
            Port("out", "out", "complex"),
        ],
        params=[
            # --- basic: what you'd actually turn while testing ---
            Param("distance", 2, basic=True, label="Distance (m)"),
            Param("fading_type_c1", 0, basic=True, label="Fading Model"),
            Param("noise_volt_c1", 0.01, basic=True, label="Noise Voltage"),

            # --- advanced: fixed physical assumptions behind friis_gain ---
            Param("freq", 915e6, basic=False, label="Carrier Frequency"),
            Param("g_reader_dbi", 6.0, basic=False, label="Reader Antenna Gain (dBi)"),
            Param("g_tag_dbi", 2.0, basic=False, label="Tag Antenna Gain (dBi)"),
            Param("samp_rate_c1", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    "sender": NodeType(
        class_module="backscatter_sender",
        class_name="backscatter_sender",
        ports=[
            # No inputs -- this is the signal source. Both outputs carry
            # the same reader-carrier signal (confirmed: both come from
            # blocks_selector_0's single output); Carrier_Out is what the
            # forward channel/tags see, Tx_Leakage_Out is the clean
            # reference every rx_* node's Tx_Leakage_In cancels against.
            Port("Carrier_Out", "out", "complex"),
            Port("Tx_Leakage_Out", "out", "complex"),
        ],
        params=[
            Param("tari", 12.5, basic=True, label="Tari (reader symbol time, µs)"),
            Param("enable_gated", 0, basic=True, label="Gated Mode"),
            Param("tx_power_send", 20, basic=True, label="Tx Power"),

            Param("command_bits", (0, 1, 0, 1), basic=False, label="Reader Command Bits"),
            Param("carrier_freq_send", 0, basic=False, label="Carrier Frequency Offset"),
            Param("phase_noise_std_send", 0, basic=False, label="Phase Noise Std Dev"),
            Param("samp_rate_send", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    # Nested inside every tag_* hier-block today (see
    # modulation_middleware's docstring), but it's also its own standalone
    # compiled block -- placeable on its own, e.g. for decoding PIE straight
    # off an antenna input without a full tag wrapped around it.
    "pie_decoder": NodeType(
        class_module="tag_pie_decoder",
        class_name="tag_pie_decoder",
        ports=[
            Port("Carrier_In", "in", "complex"),
            Port("out", "out", "float"),
        ],
        params=[
            Param("slot_id", 0, basic=True, label="Assigned Slot ID"),
            Param("packet_bits", 16, basic=True, label="Number of Packet Bits"),
            Param("bit_rate", 10e3, basic=True, label="Bit Rate"),
            Param("tari", 12.5, basic=True, label="Tari (reader symbol time, µs)"),

            Param("threshold", 0.6, basic=False, label="Slicer Threshold"),
            Param("enable_mac", 1, basic=False, label="Enable MAC"),
            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
        ],
    ),

    "packet_hex_decoder": NodeType(
        class_module="packet_hex_decoder",
        class_name="packet_hex_decoder",
        ports=[
            Port("Data_In", "in", "byte"),
            # sink -- no output port, decoded packets leave via packet_queue
        ],
        params=[
            Param("payload_len_bits", 16, basic=True, label="Payload Length (bits)"),
            Param("bit_rate", 10e3, basic=True, label="Bit Rate"),

            Param("preamble", "10101011", basic=False, label="Preamble"),
            Param("samp_rate", 2e6, basic=False, label="Sample Rate"),
        ],
        # packet_queue is a live queue.Queue() the middleware/builder hands
        # this node at construction time, not a value anyone types in --
        # see the NodeType.runtime_params field note above.
        runtime_params=["packet_queue"],
    ),
}


# ---------------------------------------------------------------------------
# MODULATION_MAP — the thing that resolves "OOK" (one project-level choice)
# into the matching tag type AND the matching receiver type, so a mismatched
# tag/rx pair is structurally impossible for the default flow.
# ---------------------------------------------------------------------------

MODULATION_MAP = {
    "OOK":    {"tag_type": "tag_ook",    "rx_type": "rx_ook"},
    "BPSK":   {"tag_type": "tag_bpsk",   "rx_type": "rx_bpsk"},
    "FM0":    {"tag_type": "tag_fm0",    "rx_type": "rx_fm0"},
    "Miller": {"tag_type": "tag_miller", "rx_type": "rx_miller"},
    "Analog": {"tag_type": "tag_analog", "rx_type": "rx_analog"},
    # Analog's tag entry takes a different second input (Signal_In/float,
    # not Data_In/byte) -- see the NODE_TYPES module note. A generic
    # bit-payload builder will need a branch for this one specifically.
}
