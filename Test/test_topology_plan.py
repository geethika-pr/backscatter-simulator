"""
Validates plan_topology()/_port_index() from core.modulation_middleware --
the port-name-resolution and wiring-plan logic behind TopologyMiddleware.
plan_topology() and _port_index() themselves only touch registry.py (plain
dataclasses, no gnuradio), but importing modulation_middleware.py at all
still pulls in gnuradio at module level (same as importing
BackscatterMiddleware always has) -- so this still needs the project venv
active, same as everything else here. The payoff over
test_topology_middleware.py is that nothing here actually builds a
flowgraph or touches a real block constructor: it's checking the wiring
PLAN is right, not running it, so it runs in well under a second and
can't hang/crash on anything gnuradio-runtime-related.

What this checks:
  1. Port-name -> positional-index resolution for every node type,
     including the two known naming quirks (tag_fm0/tag_miller's
     "Reflection_Out", sender's two same-signal outputs).
  2. A bad port name raises instead of silently resolving to something.
  3. The hand-written topology spec below -- meant to reproduce
     BackscatterMiddleware's current fixed 2-tag OOK shape exactly --
     produces the same abstract wiring graph: sender -> throttle only on
     the Carrier_Out side (not Tx_Leakage_Out), both channels' unwired
     Interference_In get flagged for a zero source, and the two tags'
     outputs land in one fan-in group of size 2 feeding the return
     channel, same as today's hardcoded adder.

Run: python3 Test/test_topology_plan.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

from modulation_middleware import plan_topology, _port_index  # noqa: E402
import registry  # noqa: E402

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


# --- 1. port-index resolution, every node type that has ports ---
PORT_CHECKS = [
    ("tag_ook", "Carrier_In", "in", 0),
    ("tag_ook", "Data_In", "in", 1),
    ("tag_ook", "out", "out", 0),
    ("tag_fm0", "Reflection_Out", "out", 0),
    ("tag_miller", "Reflection_Out", "out", 0),
    ("tag_analog", "Signal_In", "in", 1),
    ("rx_ook", "Antenna_In", "in", 0),
    ("rx_ook", "Tx_Leakage_In", "in", 1),
    ("rx_ook", "Data_Out", "out", 0),
    ("rx_analog", "Data_Out", "out", 0),
    ("sender", "Carrier_Out", "out", 0),
    ("sender", "Tx_Leakage_Out", "out", 1),
    ("channel", "Signal_In", "in", 0),
    ("channel", "Interference_In", "in", 1),
    ("channel", "out", "out", 0),
    ("pie_decoder", "Carrier_In", "in", 0),
    ("pie_decoder", "out", "out", 0),
    ("packet_hex_decoder", "Data_In", "in", 0),
]
for type_name, port_name, direction, expected in PORT_CHECKS:
    got = _port_index(type_name, port_name, direction)
    check(f"_port_index({type_name!r}, {port_name!r}, {direction!r}) == {expected}", got == expected)

try:
    _port_index("tag_ook", "NotAPort", "in")
    check("bad port name raises KeyError", False)
except KeyError:
    check("bad port name raises KeyError", True)

# --- 2. every registered node type is at least resolvable (no typos in registry.py ports) ---
for type_name, nt in registry.NODE_TYPES.items():
    try:
        for p in nt.ports:
            _port_index(type_name, p.name, p.direction)
        ok = True
    except Exception as e:
        ok = False
        print(f"       -> {type_name}: {e}")
    check(f"every port on {type_name!r} resolves to itself", ok)

# --- 3. hand-written topology spec reproducing today's exact 2-tag OOK shape ---
topology = {
    "nodes": [
        {"id": "sender0", "type": "sender"},
        {"id": "fwd_ch", "type": "channel"},
        {"id": "tag0", "type": "tag_ook", "payload": [1, 0, 1, 1, 0]},
        {"id": "tag1", "type": "tag_ook", "payload": [1, 1, 0, 0, 1]},
        {"id": "ret_ch", "type": "channel"},
        {"id": "rx0", "type": "rx_ook"},
        {"id": "dec0", "type": "packet_hex_decoder"},
    ],
    "edges": [
        ("sender0", "Carrier_Out", "fwd_ch", "Signal_In"),
        ("fwd_ch", "out", "tag0", "Carrier_In"),
        ("fwd_ch", "out", "tag1", "Carrier_In"),
        ("tag0", "out", "ret_ch", "Signal_In"),
        ("tag1", "out", "ret_ch", "Signal_In"),
        ("ret_ch", "out", "rx0", "Antenna_In"),
        ("sender0", "Tx_Leakage_Out", "rx0", "Tx_Leakage_In"),
        ("rx0", "Data_Out", "dec0", "Data_In"),
    ],
}

plan = plan_topology(topology["nodes"], topology["edges"])

check("sender0's Carrier_Out gets throttled", plan["needs_throttle"] == {"sender0"})

fwd_interference_idx = _port_index("channel", "Interference_In", "in")
check(
    "both fwd_ch and ret_ch flagged for an auto zero-source on Interference_In",
    plan["needs_zero_source"] == {("fwd_ch", fwd_interference_idx), ("ret_ch", fwd_interference_idx)},
)

ret_signal_idx = _port_index("channel", "Signal_In", "in")
fanin = plan["fanin_groups"].get(("ret_ch", ret_signal_idx))
check(
    "tag0 + tag1 both land in one fan-in group of size 2 at ret_ch.Signal_In",
    fanin is not None and sorted(fanin) == [("tag0", 0), ("tag1", 0)],
)

# the sender->rx leakage path must NOT be in needs_throttle / must be "direct"
leakage_connect = [
    c for c in plan["connects"]
    if c[0][0] == "sender0" and c[0][2] == 1  # from sender0, port index 1 (Tx_Leakage_Out)
]
check(
    "sender0's Tx_Leakage_Out (port 1) is wired direct, NOT throttled",
    len(leakage_connect) == 1 and leakage_connect[0][0][1] == "direct",
)

carrier_connect = [
    c for c in plan["connects"]
    if c[0][0] == "sender0" and c[0][2] == 0  # from sender0, port index 0 (Carrier_Out)
]
check(
    "sender0's Carrier_Out (port 0) is wired throttled",
    len(carrier_connect) == 1 and carrier_connect[0][0][1] == "throttled",
)

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
else:
    print("All checks passed.")
