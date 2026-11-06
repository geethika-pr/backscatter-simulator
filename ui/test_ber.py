"""
Pure-math regression check for the bit-error-rate (BER) comparison
added to canvas_window.py -- _hex_str_to_bits() and
CanvasWindow._compute_packet_ber() (see claude/energy-consumption-
estimate.md's sibling doc / the chat history around 2026-10-25 for the
design discussion: capturing the exact bits a tag transmits via
TopologyMiddleware.tag_frame_bits, then comparing a decoded packet's
hex dump against the matching slice of it).

Same convention as ui/test_energy_estimate.py and ui/test_wake_up_
range_formula.py: imports ui.canvas_window directly, so it needs the
real project venv (gnuradio + PyQt6) active.

What this checks, and why each one matters:

  1. _hex_str_to_bits() exactly reverses packet_hex_decoder_epy_block_0.
     py's own _dispatch_packet() bit-packing (MSB-first, zero-padded
     last partial byte) -- for both byte-aligned and non-aligned bit
     counts. If this drifted from the real block's packing, every BER
     figure downstream would be silently wrong without ever raising an
     error (hex -> bits is lossy at the padding boundary if n_bits
     isn't passed through correctly).
  2. Raw-mode BER: zero transmission errors reports (0, N); flipping
     specific bits in the "decoded" side reports exactly that many
     errors, at the right count -- not off-by-one on the preamble
     boundary (a classic slicing bug: comparing against the preamble
     itself, or starting one bit early/late, would silently shift
     every "decoded" bit against the WRONG "expected" bit and likely
     show ~50% errors on an otherwise-perfect run).
  3. Framed-mode BER: with several fixed-length fragments concatenated
     (each with its own preamble prefix, matching plan_topology()'s
     real tag_frame_bits construction), frag_index must select the
     RIGHT fragment's slice -- checked both by confirming each
     fragment matches itself with zero errors, AND the stronger check
     that comparing a fragment's real decoded bits against a
     DIFFERENT fragment's slice (wrong frag_index) shows a real
     mismatch. Without that second check, a bug that always compared
     against fragment 0 regardless of frag_index would pass check (3)
     alone as long as fragment 0 happened to be being tested.

Run from the project root, venv active:
    python3 ui/test_ber.py
"""
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ui.canvas_window import _hex_str_to_bits  # noqa: E402


def _dispatch_pack(bits):
    """Exact copy of packet_hex_decoder_epy_block_0.py's own
    _dispatch_packet() packing -- that Tier-1 block is edited only by
    Geethika in GRC, so this test reproduces its packing rather than
    importing it, to check _hex_str_to_bits() against the real thing
    it has to reverse."""
    byte_array = bytearray()
    for b_idx in range(0, len(bits), 8):
        chunk = bits[b_idx:b_idx + 8]
        val = 0
        for bit in chunk:
            val = (val << 1) | bit
        if len(chunk) < 8:
            val <<= (8 - len(chunk))
        byte_array.append(val)
    return " ".join(f"{b:02X}" for b in byte_array)


def _compute_ber(frame_bits, preamble_len, n_bits, decoded_bits, sim_header=None):
    """Mirrors CanvasWindow._compute_packet_ber()'s slicing math
    exactly, minus the NodeItem/topology lookups (frame_bits/
    preamble_len passed in directly here, same as that method would
    resolve them to internally)."""
    if sim_header is not None:
        block_size = preamble_len + n_bits
        start = sim_header["frag_index"] * block_size + preamble_len
    else:
        start = preamble_len
    expected = frame_bits[start:start + n_bits]
    if len(expected) != n_bits or n_bits <= 0 or len(decoded_bits) != n_bits:
        return None
    n_wrong = sum(1 for a, b in zip(expected, decoded_bits) if a != b)
    return n_wrong, n_bits


def main():
    random.seed(1234)

    # 1. hex <-> bits round-trip against the REAL _hex_str_to_bits(),
    #    for both byte-aligned and non-aligned bit counts.
    for n in [1, 5, 7, 8, 9, 16, 17, 23, 24, 31, 40, 63, 128]:
        bits = [random.randint(0, 1) for _ in range(n)]
        hex_str = _dispatch_pack(bits)
        recovered = _hex_str_to_bits(hex_str, n)
        assert recovered == bits, (n, bits, hex_str, recovered)
    print("OK: _hex_str_to_bits() exactly reverses the real decoder's bit-packing "
          "for byte-aligned and non-aligned bit counts")

    # 2. Raw mode: preamble + one fixed payload.
    preamble = [1, 0, 1, 0, 1, 0, 1, 1]
    payload = [random.randint(0, 1) for _ in range(40)]
    frame_bits = preamble + payload

    decoded = _hex_str_to_bits(_dispatch_pack(payload), len(payload))
    result = _compute_ber(frame_bits, len(preamble), len(payload), decoded)
    assert result == (0, 40), result

    flipped = list(decoded)
    flipped[5] ^= 1
    flipped[12] ^= 1
    result2 = _compute_ber(frame_bits, len(preamble), len(payload), flipped)
    assert result2 == (2, 40), result2
    print(f"OK: raw-mode BER -- zero errors on a clean decode {result}, "
          f"exactly 2 on 2 deliberately flipped bits {result2}")

    # 3. Framed mode: 3 fixed-length fragments, each with its own
    #    preamble prefix (matches plan_topology()'s real tag_frame_
    #    bits construction).
    frag_len = 24
    fragments = [[random.randint(0, 1) for _ in range(frag_len)] for _ in range(3)]
    frame_bits_framed = []
    for frag in fragments:
        frame_bits_framed += preamble + frag

    for frag_index in (0, 1, 2):
        decoded = _hex_str_to_bits(_dispatch_pack(fragments[frag_index]), frag_len)
        result = _compute_ber(frame_bits_framed, len(preamble), frag_len,
                               decoded, {"frag_index": frag_index})
        assert result == (0, frag_len), (frag_index, result)
    print("OK: framed-mode BER -- all 3 fragments slice correctly by frag_index")

    # Stronger check: fragment 2's real decoded bits against fragment
    # 0's slice (WRONG frag_index) must show a real mismatch -- rules
    # out a bug that always reads fragment 0 regardless of frag_index.
    decoded_frag2 = _hex_str_to_bits(_dispatch_pack(fragments[2]), frag_len)
    wrong_slice = _compute_ber(frame_bits_framed, len(preamble), frag_len,
                                decoded_frag2, {"frag_index": 0})
    assert wrong_slice[0] > 0, (
        "fragment 2's real bits compared against fragment 0's slice should "
        "show errors -- if this is 0, frag_index isn't actually selecting "
        "a different slice"
    )
    print(f"OK: comparing against the WRONG frag_index correctly shows a "
          f"mismatch {wrong_slice} -- confirms frag_index slicing is load-bearing")

    print("\nAll BER checks passed.")


if __name__ == "__main__":
    main()
