"""
Pure-Python, no gnuradio needed (same as test_topology_plan.py) --
message_framer.py has zero gnuradio dependency, so this runs directly,
right here, rather than needing the project venv + a real flowgraph
like most of this directory's other tests.

What this covers: the refactor that split FragmentReassembler.feed_hex()
(previously one function doing parse+descramble+CRC+reassembly inline)
into message_framer.parse_fragment_hex() (parse+descramble+CRC only,
CRC failures included rather than dropped) + FragmentReassembler.
feed_parsed() (the reassembly logic, now taking an already-parsed dict).
feed_hex() itself becomes a thin wrapper chaining the two. This was
needed so a second caller -- TopologyMiddleware.get_packets(), for the
UI's Fragments table -- could get at the header fields (tag_id/
frag_index/frag_count/crc_ok) to LABEL and separate them from the
user's actual payload bytes, without re-implementing the descramble/
CRC logic a second time, and without that display-only path accidentally
doing its own reassembly.

The differential check: a REFERENCE implementation below is feed_hex()'s
ORIGINAL body, verbatim (copied before the refactor, operating on its
own plain dicts instead of a FragmentReassembler instance), diffed
against the real, refactored message_framer module across many fuzzed
inputs -- both well-formed fragments (via build_fragments_from_bytes())
and garbage/corrupted/too-short hex strings -- to prove the refactor
didn't change observable behavior.

NOT covered here: the exhaustive _SCRAMBLE_BYTE=0x33-vs-0x55/0xAA
collision claim in the module docstring/test_header_whitening.py's own
reference (that file was referenced by name there but was never
actually committed to this project -- a pre-existing gap, not something
this refactor touched or verified one way or the other).

Run from the project root:
    python3 Test/test_message_framer.py
"""
import sys
import os
import random

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import message_framer  # noqa: E402


# --- reference implementation: feed_hex()'s ORIGINAL body, before the
# parse_fragment_hex()/feed_parsed() split, operating on plain dicts so
# this test doesn't need a FragmentReassembler instance at all. ---
def _old_feed_hex(partial, expected_count, hex_str):
    try:
        raw = bytes.fromhex(hex_str.replace(" ", ""))
    except ValueError:
        return None
    if len(raw) < message_framer.HEADER_BYTES:
        return None
    header = message_framer._scramble(raw[:message_framer.HEADER_BYTES])
    # tag_id/used_bytes/crc are single bytes; frag_index/frag_count are
    # 2-byte big-endian fields as of the 2026-11-02 wire widening (see
    # message_framer.py's module docstring) -- this reference
    # implementation has to track that same layout to stay a meaningful
    # differential check against the real parse_fragment_hex(), rather
    # than silently pinning the OLD (pre-widening) format forever.
    tag_id = header[0]
    frag_index = (header[1] << 8) | header[2]
    frag_count = (header[3] << 8) | header[4]
    used_bytes = header[5]
    crc = header[6]
    chunk = raw[message_framer.HEADER_BYTES:]
    if (frag_count == 0 or used_bytes > len(chunk)
            or message_framer._crc8(header[:message_framer.HEADER_BYTES - 1] + chunk) != crc):
        return None
    slots = partial.setdefault(tag_id, {})
    expected_count[tag_id] = frag_count
    if 0 <= frag_index < frag_count and frag_index not in slots:
        slots[frag_index] = chunk[:used_bytes]
    if not all(i in slots for i in range(frag_count)):
        return None
    message = b"".join(slots[i] for i in range(frag_count))
    del partial[tag_id]
    del expected_count[tag_id]
    return tag_id, message


def _hexify(bits):
    raw = message_framer._bits_to_bytes(bits)
    return " ".join(f"{b:02X}" for b in raw)


def _well_formed_hex_stream(rng, n):
    """n well-formed fragment hex strings (as packet_hex_decoder would
    hand back), drawn from a handful of random messages/chunk sizes/
    tag_ids, each fragment possibly repeated (as a real tag looping
    would produce) -- exercises the "already complete, slot ignored"
    path in feed_parsed() too, not just first-arrival."""
    out = []
    for _ in range(n):
        tag_id = rng.randrange(0, 4)
        chunk_bytes = rng.randrange(1, 6)
        msg_len = rng.randrange(0, 20)
        data = bytes(rng.randrange(0, 256) for _ in range(msg_len))
        fragments = message_framer.build_fragments_from_bytes(data, tag_id, chunk_bytes)
        frag = rng.choice(fragments)
        out.append(_hexify(frag))
    return out


def _garbage_hex_stream(rng, n):
    """n deliberately malformed/corrupted/too-short inputs: non-hex
    text, odd-length hex, short-but-valid-hex (< HEADER_BYTES), and
    well-formed fragments with a single bit flipped (so they parse as
    hex but fail CRC)."""
    out = []
    for _ in range(n):
        kind = rng.randrange(0, 4)
        if kind == 0:
            out.append("not hex at all, zz zz")
        elif kind == 1:
            out.append("A")  # odd-length hex string
        elif kind == 2:
            nbytes = rng.randrange(0, message_framer.HEADER_BYTES)
            out.append(" ".join(f"{rng.randrange(0, 256):02X}" for _ in range(nbytes)))
        else:
            tag_id = rng.randrange(0, 4)
            chunk_bytes = rng.randrange(1, 6)
            data = bytes(rng.randrange(0, 256) for _ in range(rng.randrange(1, 10)))
            frag = rng.choice(message_framer.build_fragments_from_bytes(data, tag_id, chunk_bytes))
            raw = bytearray(message_framer._bits_to_bytes(frag))
            flip_byte = rng.randrange(0, len(raw))
            raw[flip_byte] ^= 1 << rng.randrange(0, 8)
            out.append(" ".join(f"{b:02X}" for b in raw))
    return out


def test_parse_fragment_hex_matches_old_inline_parse():
    """parse_fragment_hex() alone (ignoring reassembly) must agree with
    the parse+descramble+CRC steps _old_feed_hex() did inline, for
    every input that's even hex-parseable and >= HEADER_BYTES long --
    the two places _old_feed_hex() returned None early for a SHORTER
    reason (garbage hex, too-short) are exactly where parse_fragment_
    hex() also returns None."""
    rng = random.Random(1)
    checked = 0
    for hex_str in _well_formed_hex_stream(rng, 300) + _garbage_hex_stream(rng, 300):
        parsed = message_framer.parse_fragment_hex(hex_str)
        try:
            raw = bytes.fromhex(hex_str.replace(" ", ""))
            too_short_or_bad_hex = len(raw) < message_framer.HEADER_BYTES
        except ValueError:
            too_short_or_bad_hex = True
        if too_short_or_bad_hex:
            assert parsed is None, f"expected None for {hex_str!r}, got {parsed}"
            continue
        assert parsed is not None, f"expected a parse for {hex_str!r}"
        header = message_framer._scramble(raw[:message_framer.HEADER_BYTES])
        # Same 2-byte frag_index/frag_count layout as _old_feed_hex()
        # above -- see that function's comment.
        tag_id = header[0]
        frag_index = (header[1] << 8) | header[2]
        frag_count = (header[3] << 8) | header[4]
        used_bytes = header[5]
        crc = header[6]
        chunk = raw[message_framer.HEADER_BYTES:]
        expected_crc_ok = message_framer._crc8(header[:message_framer.HEADER_BYTES - 1] + chunk) == crc
        assert parsed == {
            "tag_id": tag_id, "frag_index": frag_index, "frag_count": frag_count,
            "used_bytes": used_bytes, "crc_ok": expected_crc_ok, "chunk": chunk,
        }, f"mismatch for {hex_str!r}: {parsed}"
        checked += 1
    assert checked > 0
    print(f"test_parse_fragment_hex_matches_old_inline_parse: {checked} parsed + "
          f"{600 - checked} short/garbage checked, all agree")


def test_feed_hex_differential_against_old_implementation():
    """Feeds the SAME sequence of hex strings (well-formed + garbage,
    interleaved and possibly out of order/repeated -- matching how
    real decode traffic from multiple looping tags would actually
    arrive) to both the old reference reassembler and the real,
    refactored FragmentReassembler, and requires every return value
    (None, or (tag_id, message bytes)) to match at every step."""
    rng = random.Random(2)
    old_partial, old_expected = {}, {}
    new = message_framer.FragmentReassembler()

    stream = _well_formed_hex_stream(rng, 500) + _garbage_hex_stream(rng, 200)
    rng.shuffle(stream)

    completions = 0
    for i, hex_str in enumerate(stream):
        old_result = _old_feed_hex(old_partial, old_expected, hex_str)
        new_result = new.feed_hex(hex_str)
        assert old_result == new_result, (
            f"step {i}, hex={hex_str!r}: old={old_result!r} new={new_result!r}"
        )
        if new_result is not None:
            completions += 1
        # Internal state must stay in lockstep too, not just the
        # returned value -- a divergence here would only surface
        # later, on some FUTURE fragment's reassembly.
        assert new._partial == old_partial, f"step {i}: _partial diverged"
        assert new._expected_count == old_expected, f"step {i}: _expected_count diverged"

    assert completions > 0, "fuzz run never completed a single message -- test is too weak"
    print(f"test_feed_hex_differential_against_old_implementation: {len(stream)} fragments fed, "
          f"{completions} message(s) completed, old/new agreed at every step")


def test_feed_parsed_equivalent_to_feed_hex():
    """feed_hex(s) and feed_parsed(parse_fragment_hex(s)) must be the
    exact same call for every well-formed/garbage input -- this is the
    "second caller gets the same answer by reusing the same parse"
    property the refactor exists for (TopologyMiddleware.get_packets()
    parses once and calls feed_parsed() directly, see modulation_
    middleware.py)."""
    rng = random.Random(3)
    a = message_framer.FragmentReassembler()
    b = message_framer.FragmentReassembler()
    stream = _well_formed_hex_stream(rng, 400) + _garbage_hex_stream(rng, 150)
    rng.shuffle(stream)
    for i, hex_str in enumerate(stream):
        r1 = a.feed_hex(hex_str)
        parsed = message_framer.parse_fragment_hex(hex_str)
        r2 = b.feed_parsed(parsed) if parsed is not None else None
        assert r1 == r2, f"step {i}, hex={hex_str!r}: feed_hex={r1!r} feed_parsed={r2!r}"
    print(f"test_feed_parsed_equivalent_to_feed_hex: {len(stream)} fragments, feed_hex == "
          "parse_fragment_hex+feed_parsed at every step")


if __name__ == "__main__":
    test_parse_fragment_hex_matches_old_inline_parse()
    test_feed_hex_differential_against_old_implementation()
    test_feed_parsed_equivalent_to_feed_hex()
    print("All message_framer refactor tests passed.")
