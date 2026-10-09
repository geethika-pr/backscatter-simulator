"""
Real run, needs the project venv + gnuradio -- builds the SAME 2-tag OOK
setup as test_topology_middleware.py, but via build_fixed_topology()
instead of a hand-written nodes/edges spec. The point of this one is
specifically to confirm the generator's baked-in defaults (sender's
enable_gated=1, fwd_channel's distance=1/noise_volt_c1=0.02) are enough on
their own -- unlike test_topology_middleware.py's first draft, this
should NOT need those spelled out by the caller.

Compare against:
    python3 -m core.modulation_middleware --modulation OOK
Same expectation: both tags' hex/ascii show up correctly, repeatedly.

Run from the project root, venv active:
    python3 Test/test_build_fixed_topology.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "blocks"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core.modulation_middleware import build_fixed_topology, TopologyMiddleware  # noqa: E402

tag_specs = [
    {"slot_id": 0, "payload": [1, 0, 1, 1, 0]},
    {"slot_id": 1, "payload": [1, 1, 0, 0, 1]},
]


def main():
    print("Building topology via build_fixed_topology(\"OOK\", ...)...")
    # Nothing needs to be said explicitly here anymore -- enable_gated,
    # fwd_channel's noise_volt_c1, the tag/decoder burst-timing overrides,
    # AND si_isolation_db (100.0 for OOK specifically, via
    # SI_ISOLATION_DEFAULTS) all come from the generator's own
    # modulation-aware defaults. Only the return channel's distance is
    # passed here as an example of overriding one.
    nodes, edges = build_fixed_topology(
        "OOK", tag_specs,
        ret_channel_params={"distance": 2},
    )
    tmw = TopologyMiddleware(nodes, edges)

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
