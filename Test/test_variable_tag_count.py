"""
Real run, needs the project venv + gnuradio -- validates build_fixed_topology()
+ TopologyMiddleware with tag counts OTHER than the 2 everything else in
this project has been tested with: 1 tag, then 3 tags, same OOK setup
otherwise. This is the actual point of generalizing away from
BackscatterMiddleware's hardcoded-2-tags shape -- plan_topology()'s
fan-in/fan-out logic was already checked structurally for N=1,2,3,5
(no gnuradio needed for that), this is the real-hardware-in-the-loop
version: does it actually still decode correctly with a different tag
count, not just wire up without crashing.

Each tag gets a distinct slot_id and payload so you can tell them apart
in the decoded output.

Run from the project root, venv active:
    python3 Test/test_variable_tag_count.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "blocks"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core.modulation_middleware import build_fixed_topology, TopologyMiddleware  # noqa: E402


def run(tag_specs, seconds=8, label=""):
    print(f"\n=== {label}: {len(tag_specs)} tag(s) ===")
    nodes, edges = build_fixed_topology(
        "OOK", tag_specs,
        ret_channel_params={"distance": 2},
    )
    tmw = TopologyMiddleware(nodes, edges)
    tmw.start()
    try:
        for _ in range(seconds):
            time.sleep(1)
            for pkt in tmw.get_packets():
                print("PACKET:", pkt)
    finally:
        tmw.stop()


def main():
    run([{"slot_id": 0, "payload": [1, 0, 1, 1, 0]}], label="1-tag")

    run([
        {"slot_id": 0, "payload": [1, 0, 1, 1, 0]},
        {"slot_id": 1, "payload": [1, 1, 0, 0, 1]},
        {"slot_id": 2, "payload": [0, 1, 1, 0, 1]},
    ], seconds=25, label="3-tag")


if __name__ == "__main__":
    main()
