"""
Real run, needs the project venv + gnuradio -- builds TopologyMiddleware
from a hand-written topology spec that reproduces BackscatterMiddleware's
current fixed 2-tag OOK shape exactly (same node params, same payloads,
same preamble), runs it for a few seconds, and prints whatever
packet_hex_decoder decodes.

This is the actual regression check for the registry.py / TopologyMiddleware
work: it should decode the same two payloads BackscatterMiddleware already
decodes correctly today. Compare against:
    python3 -m core.modulation_middleware --modulation OOK
(same tag payloads, same expectation: both tags' hex/ascii show up
correctly, repeatedly, once PIE/harvesting settles in).

Run from the project root, venv active:
    python3 Test/test_topology_middleware.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "blocks"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core.modulation_middleware import TopologyMiddleware  # noqa: E402

# Same shape as BackscatterMiddleware(modulation="OOK", tag_specs=[...])
# builds today -- sender -> throttle -> fwd channel -> fan out to 2 OOK
# tags -> fan in -> ret channel -> rx_ook -> packet_hex_decoder. Every
# node here uses registry defaults (no "params" overrides), same as
# BackscatterMiddleware's un-overridden path, EXCEPT this doesn't apply
# DEFAULT_TAG_OVERRIDES (packet_bits=10000, p_th=0.5) the way
# BackscatterMiddleware does -- TopologyMiddleware doesn't have that
# modulation-agnostic burst-timing convenience layered on yet, so it's
# given explicitly per tag below instead, to make this an apples-to-apples
# comparison against a working BackscatterMiddleware run.
TAG_OVERRIDES = {"packet_bits": 10000, "p_th": 0.5}

topology = {
    "nodes": [
        {"id": "sender0", "type": "sender", "params": {"enable_gated": 1}},
        {"id": "fwd_ch", "type": "channel", "params": {"distance": 1, "noise_volt_c1": 0.02}},
        {"id": "tag0", "type": "tag_ook", "payload": [1, 0, 1, 1, 0],
         "params": {"slot_id": 0, **TAG_OVERRIDES}},
        {"id": "tag1", "type": "tag_ook", "payload": [1, 1, 0, 0, 1],
         "params": {"slot_id": 1, **TAG_OVERRIDES}},
        {"id": "ret_ch", "type": "channel", "params": {"distance": 2}},
        {"id": "rx0", "type": "rx_ook", "params": {"si_isolation_db": 100.0}},
        {"id": "dec0", "type": "packet_hex_decoder",
         "params": {"payload_len_bits": 5, "bit_rate": 10e3}},
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


def main():
    print("Building TopologyMiddleware from the hand-written 2-tag OOK spec...")
    tmw = TopologyMiddleware(topology["nodes"], topology["edges"])

    print("Starting flowgraph...")
    tmw.start()
    try:
        for _ in range(10):
            time.sleep(1)
            packets = tmw.get_packets()
            if packets:
                for pkt in packets:
                    print("PACKET:", pkt)
            else:
                print("...(no packets this second)")
    except KeyboardInterrupt:
        pass
    finally:
        print("Stopping flowgraph...")
        tmw.stop()


if __name__ == "__main__":
    main()
