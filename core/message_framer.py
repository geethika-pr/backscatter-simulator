"""Long-message framing/reassembly for backscatter tags.

Deliberately NOT a GNU Radio block, and not imported by anything under
blocks/ -- a tag transmits one dumb, pre-computed bit-vector on an
endless loop (backscatter_tag_ook & friends have no idea a "message"
exists; they just play back whatever vector_source_b.vector_source_b()
was handed). So turning an arbitrary message into that bit-vector, and
turning a reader's many decoded fragments back into the original
message, is exactly the application-layer job that the rest of this
project's "GNU Radio stays dumb" boundary already draws -- see
TopologyMiddleware's own docstring/comments on payload/payload_fragments
for where this plugs in, and NodeItem.payload_mode in canvas_window.py
for how a tag UI node picks between this and the older "raw bits,
repeated forever" mode.

Wire format, per fragment -- all byte-aligned on purpose, because
packet_hex_decoder_epy_block_0.py's own _dispatch_packet() only ever
hands back a hex string of whatever bits it collected (plus a bit
COUNT, not the bits themselves -- see its "bits" field), never the raw
bit list. Byte-aligning every field means FragmentReassembler can
recover everything it needs from that hex string with a plain
bytes.fromhex(), with no bit-shifting required on this side either:

    byte 0:    tag_id       (0-255 -- unique only among tags feeding the
                             SAME receiver, assigned by canvas_window.py's
                             scene_to_topology() per receiver, not a
                             global/stable id)
    bytes 1-2: frag_index   (0-65535, big-endian -- was one byte/0-255
                             until the 2026-11-02 wire-version bump below)
    bytes 3-4: frag_count   (0-65535, big-endian, total fragments in
                             this message -- same bump)
    byte 5:    used_bytes   (how many of THIS fragment's chunk bytes are
                             real message data -- always chunk_bytes
                             except possibly on the last fragment; still
                             one byte, since chunk_bytes itself is
                             separately capped at MAX_CHUNK_BYTES=255)
    byte 6:    crc8         (over bytes 0-5 PLUS the full, post-padding
                             chunk -- i.e. the whole frame except this
                             byte itself, see _crc8(). Originally this
                             covered only the chunk, which meant a noise-
                             flipped bit in tag_id/frag_index/frag_count/
                             used_bytes could slip a perfectly good chunk
                             into the wrong tag's buffer, or the wrong
                             slot of the right tag's buffer, instead of
                             being caught and dropped -- exactly the
                             "message shows up under the wrong tag"
                             symptom reported in practice with two tags on
                             one receiver. Covering the header closes that:
                             any header corruption now fails the same CRC
                             check the chunk already relied on.)
    bytes 7..: chunk        (chunk_bytes of message data, zero-padded)

Revision (2026-11-02): frag_index/frag_count widened from 1 byte each to
2 bytes each (HEADER_BYTES 5 -> 7, MAX_FRAGMENTS 255 -> 65535) -- the old
1-byte frag_count capped ANY message/file at 255 fragments x (at most)
255 bytes/fragment = 65,025 bytes total, regardless of chunk_bytes. No
actual wire-protocol-version field was needed for this: the framer that
builds fragments and the one that parses them are the same Python module
in the same run, never two independently-versioned ends like a real RF
link would have, so there's nothing to negotiate -- "bump the version"
here just meant "widen these two fields consistently," done in
parse_fragment_hex()/build_fragments_from_bytes() together. used_bytes/
chunk_bytes were deliberately left at 1 byte/255 -- untouched, since the
ask was specifically about frag_count, not per-fragment chunk size (a
separate, independent cap if that's ever wanted too).

The long-run / OOK-BPSK-vs-FM0-Miller sync problem reported in practice
(one tag's message -- whichever one happened to get tag_id 0 --
essentially never completing in OOK/BPSK even at high TX power, while
FM0/Miller worked "flawlessly" with the exact same content) traces to
tag_id/frag_index being small integers: tag_id=0 (first tag on a
receiver) and frag_index=0 (every message's first fragment, sent
every loop) are both all-zero bytes, producing a long near-constant
stretch right after the preamble on every loop for whichever tag
lands on tag_id 0. Every OOK/BPSK/FM0/Miller receiver hier-block has
an identical filter.dc_blocker_ff/cc(...) ahead of the slicer, which
decays a long near-constant stretch toward zero regardless of signal
amplitude (hence independent of TX power) -- FM0/Miller's self-
clocking line coding never feeds it one in the first place; OOK/BPSK's
raw/NRZ transmission does. Bumping the dc_blocker's length (Tier 1,
blocks/backscatter_rx_*.py) tolerates a longer run but trades off
settling time for OTHER content/tags (confirmed in practice: pushing
it to 8000-16000 samples helped one tag's completions non-
monotonically while taking another tag's to zero) -- not a clean fix
on its own.

So the header -- bytes 0-6 above (was 0-4 pre-widening), OUR OWN
bookkeeping fields, never the chunk -- is whitened: XORed with
_SCRAMBLE_BYTE before it becomes bits, undone symmetrically on receipt
(XOR is self-inverse). This is simulator-internal framing/addressing
metadata, not anything a real backscatter tag would be understood to
"send" as a message, so scrambling it doesn't conflict with the payload
staying exactly what the user typed: bytes 7+ (the chunk) are NEVER
touched by this, in either direction, so the hex/ASCII dump's payload
half always reads as the real message, full stop. An earlier version of
this file scrambled header+chunk together and was reverted specifically
because it crossed that line -- see the project's whitening-fix-findings
doc for that history. A SEPARATE, user-controlled header concept (a field
that rides inside the untouched payload bytes, entirely the user's to
define/parse) is a natural follow-up if building something on top of
this testbed (a custom protocol, a crypto scheme, etc.) calls for one
-- not implemented here, but nothing about this scrambling would need
to change to add it, since it would live entirely in payload space.

_SCRAMBLE_BYTE = 0x33 specifically: originally checked exhaustively
against every pair of adjacent header-byte VALUES (0-255 x 0-255)
straddling an 8-bit window, against the default preamble "10101011" --
no single repeated byte avoids every such collision (that's provably
impossible for a periodic one-byte mask), but 0x33's closest collision
needs a field value in roughly the 100s (e.g. a 100+-fragment message),
while 0x55/0xAA collide at small, everyday values (0x55 turns
frag_index=0 + frag_count in {2, 3} -- any short multi-fragment message
-- into an exact false preamble lock). See test_header_whitening.py for
the original exhaustive check and the deterministic-run-length
verification (referenced here, but -- pre-existing gap, not introduced
by this revision -- that file was never actually committed to this
project; Test/test_message_framer.py's own docstring flags the same
gap).

Re-verified for the 2026-11-02 frag_index/frag_count widening above,
since splitting each into 2 bytes introduces NEW adjacent byte pairs
(a field's own hi|lo bytes, and each field's hi byte against its
neighbor) that didn't exist in the 1-byte layout: the collision property
is intrinsic to (scramble_byte, preamble) and depends only on raw
adjacent BYTE VALUES, not on which field occupies a position, so it's a
fully reproducible, cheap exhaustive check (done ad hoc, not committed
as its own test file here -- same standing gap as test_header_whitening.
py above). Result: the pre-existing collision class is unchanged --
still roughly 100s-of-fragments before any realistic small-value header
risks a false lock, same ballpark as the original 1-byte finding above,
since e.g. frag_index=100 (hi=0, lo=100) next to tag_id=0 hits the exact
same (0,100) byte-pair collision a 1-byte frag_index=100 next to
tag_id=0 already did. The NEW boundary pairs the 2-byte split introduces
(a field's hi byte against its neighbor, e.g. tag_id=0 | frag_index_hi)
only collide once frag_index/frag_count reach the tens of thousands
(~25,600 / ~38,912) -- i.e. only relevant for messages needing tens of
thousands of fragments, squarely the large-message case this widening
exists to support, not a new small-value regression. This check (like
the original) only covers the DEFAULT preamble "10101011" -- a receiver
whose preamble was hand-edited to something else has never been
re-verified against 0x33 either, pre-existing limitation, not new here.

None of this lives in registry.py: like the tag's own "payload" entry
(see modulation_middleware.py's own docstring on that), this is
per-run DATA, not an RF/timing Param.
"""

HEADER_BYTES = 7
MAX_CHUNK_BYTES = 255
MAX_FRAGMENTS = 65535

# See the module docstring's header-whitening note -- applied to the
# 5 header bytes ONLY (tag_id/frag_index/frag_count/used_bytes/crc),
# never the chunk. 0x33 was picked over the equally run-breaking
# 0x55/0xAA specifically because it doesn't collide with the default
# preamble at small, realistic header-field values -- see the module
# docstring and test_header_whitening.py for the exhaustive check.
_SCRAMBLE_BYTE = 0x33


def _scramble(data):
    """XOR every byte with _SCRAMBLE_BYTE -- its own inverse, so the
    exact same call descrambles on the receive side. Only ever called
    on the HEADER_BYTES-length header, never the chunk -- see the
    module docstring."""
    return bytes(b ^ _SCRAMBLE_BYTE for b in data)


def _crc8(data, poly=0x07):
    """Textbook CRC-8 (poly 0x07, no reflection/no init-complement) --
    plenty to catch the odd bit-flip this sim's noise/fading model can
    introduce into one fragment. Not meant to be cryptographic, or to
    match any particular RFID standard's exact CRC choice."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ poly) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _bytes_to_bits(data):
    bits = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def _bits_to_bytes(bits):
    if len(bits) % 8 != 0:
        raise ValueError(f"bit count {len(bits)} isn't a whole number of bytes")
    out = bytearray()
    for i in range(0, len(bits), 8):
        val = 0
        for b in bits[i:i + 8]:
            val = (val << 1) | (1 if b else 0)
        out.append(val)
    return bytes(out)


def parse_fragment_hex(hex_str):
    """Parses packet_hex_decoder's own space-separated hex string (a
    decoded packet's "hex" field -- header+chunk, exactly as collected
    off the wire) into OUR bookkeeping fields plus the chunk, WITHOUT
    doing any reassembly and WITHOUT dropping a CRC failure -- that
    decision is FragmentReassembler.feed_parsed()'s job, not this
    function's. Added so a second caller (the UI's Fragments table,
    canvas_window.py) can show "this is the simulator's own tag/
    fragment bookkeeping, not what you typed" for every decoded
    fragment -- CRC failures included -- without duplicating the
    descramble/CRC logic that feed_hex() already had inline, and
    without that display path accidentally doing its own reassembly.

    Returns None if hex_str isn't valid hex, or is shorter than
    HEADER_BYTES -- not one of ours, or (just as likely) a raw/
    non-framed tag's payload, which has no header at all; the caller
    falls back to treating the whole decoded payload as the chunk.

    Returns a dict otherwise:
      "tag_id", "frag_index", "frag_count", "used_bytes": the plain
          (descrambled) header field values, exactly as received --
          NOT range-checked, since a corrupted frame can report
          anything in them.
      "crc_ok": bool -- whether this fragment's own CRC-8 matches.
          feed_parsed() treats False the same as every other
          malformed-fragment case (drops it); a display-only caller
          can still show it (e.g. "CRC fail") instead of hiding it.
      "chunk": bytes -- exactly chunk_bytes long (zero-padded on the
          final, shorter fragment); "used_bytes" says how many of it
          are real data, same meaning as the module docstring's byte 5.
    """
    try:
        raw = bytes.fromhex(hex_str.replace(" ", ""))
    except ValueError:
        return None
    if len(raw) < HEADER_BYTES:
        return None
    # Undo build_fragments()' header-only whitening FIRST -- _scramble()
    # is its own inverse, so this one call recovers the plain header
    # before anything below looks at field values or recomputes the
    # CRC. The chunk (raw[HEADER_BYTES:]) was never scrambled in the
    # first place -- see the module docstring.
    header = _scramble(raw[:HEADER_BYTES])
    # tag_id/used_bytes/crc stay single bytes; frag_index/frag_count are
    # now 2 bytes each, big-endian (see the module docstring's 2026-11-02
    # wire-widening note) -- can't flat-unpack `header` into 5 names any
    # more now that fields aren't all the same width.
    tag_id = header[0]
    frag_index = (header[1] << 8) | header[2]
    frag_count = (header[3] << 8) | header[4]
    used_bytes = header[5]
    crc = header[6]
    chunk = raw[HEADER_BYTES:]
    # CRC covers the header fields (tag_id/frag_index/frag_count/
    # used_bytes) AND the chunk -- see the module docstring's byte-6
    # note on why. header[:HEADER_BYTES - 1] -- NOT raw[...] -- since
    # the CRC was computed over the PLAIN (pre-scramble) fields on the
    # transmit side; header is already descrambled above.
    crc_ok = _crc8(header[:HEADER_BYTES - 1] + chunk) == crc
    return {
        "tag_id": tag_id, "frag_index": frag_index, "frag_count": frag_count,
        "used_bytes": used_bytes, "crc_ok": crc_ok, "chunk": chunk,
    }


def fragment_len_bits(chunk_bytes):
    """What a single fragment's length, in bits, will be for a given
    chunk_bytes -- this is what has to end up as the governing
    receiver's payload_len/payload_len_bits (see NodeItem.
    effective_fragment_bits() and CanvasWindow._sync_decoder_params()),
    NOT the length of the whole framed message."""
    return (HEADER_BYTES + chunk_bytes) * 8


def build_fragments_from_bytes(data, tag_id, chunk_bytes):
    """Splits `data` (already raw bytes -- arbitrary binary, not
    necessarily text) into chunk_bytes-sized pieces and returns a list
    of same-length bit-lists, one per fragment -- every fragment is
    exactly fragment_len_bits(chunk_bytes) long, the final one
    zero-padded if the data didn't divide evenly (see "used_bytes" in
    the module docstring for how a reassembler knows to drop that
    padding rather than keeping it).

    This is the actual framing engine; build_fragments() below is just
    this with a str -> UTF-8 encode in front of it, for the "Text
    message" payload mode. A "File" payload mode (reading an arbitrary
    file's bytes off disk) calls THIS directly instead, since a file's
    content has no reason to be valid UTF-8 (or any text encoding at
    all) -- round-tripping it through str would risk mangling bytes
    that don't decode cleanly.

    Nothing about frag_count/used_bytes/CRC cares what the bytes mean
    -- a file's size is exactly as self-describing on the wire as a
    text message's: frag_count says how many fragments make up the
    whole thing, and the last fragment's used_bytes says how many of
    ITS chunk_bytes are real data vs zero-padding. A reassembler never
    needs to be told "how big" a message/file is up front -- it just
    waits until it has every index 0..frag_count-1, exactly the same
    way regardless of what payload mode produced the bytes.

    An empty input still produces exactly one (all-padding, 0 real
    bytes) fragment, so "nothing to send yet" isn't a special case
    downstream -- the tag just loops a fragment whose used_bytes is 0.
    """
    if not (0 <= tag_id <= 255):
        raise ValueError(f"tag_id must be 0-255, got {tag_id}")
    if not (1 <= chunk_bytes <= MAX_CHUNK_BYTES):
        raise ValueError(f"chunk_bytes must be 1-{MAX_CHUNK_BYTES}, got {chunk_bytes}")

    chunks = [data[i:i + chunk_bytes] for i in range(0, len(data), chunk_bytes)] or [b""]
    frag_count = len(chunks)
    if frag_count > MAX_FRAGMENTS:
        raise ValueError(
            f"{len(data)} byte(s) needs {frag_count} fragments at {chunk_bytes} byte(s) "
            f"each -- max {MAX_FRAGMENTS} (frag_count is a 2-byte wire field). Raise "
            f"chunk_bytes or shorten the message/file ({MAX_FRAGMENTS * chunk_bytes} bytes "
            f"is this chunk_bytes' current ceiling; {MAX_FRAGMENTS * MAX_CHUNK_BYTES} bytes "
            "is the ceiling at any chunk_bytes)."
        )

    fragments = []
    for frag_index, chunk in enumerate(chunks):
        used_bytes = len(chunk)
        padded_chunk = chunk + b"\x00" * (chunk_bytes - used_bytes)
        # frag_index/frag_count are 2-byte big-endian fields now (see the
        # module docstring's 2026-11-02 wire-widening note) -- tag_id/
        # used_bytes stay single bytes.
        header_fields = bytes([
            tag_id,
            (frag_index >> 8) & 0xFF, frag_index & 0xFF,
            (frag_count >> 8) & 0xFF, frag_count & 0xFF,
            used_bytes,
        ])
        # CRC is computed over the PLAIN header fields + plain chunk --
        # it's checking the actual content; scrambling is a transmit-
        # line-coding concern layered on top afterward (see module
        # docstring). _scramble() runs last, on the header only -- the
        # chunk goes out exactly as built, untouched.
        header = header_fields + bytes([_crc8(header_fields + padded_chunk)])
        fragments.append(_bytes_to_bits(_scramble(header) + padded_chunk))
    return fragments


def build_fragments(message, tag_id, chunk_bytes):
    """Splits `message` (a str, UTF-8 encoded) into chunk_bytes-sized
    pieces -- see build_fragments_from_bytes() (this is just that, with
    the str -> bytes encode done here first) for the actual framing
    logic and every other detail."""
    return build_fragments_from_bytes(message.encode("utf-8"), tag_id, chunk_bytes)


class FragmentReassembler:
    """Buffers decoded fragments -- everything one receiver's decoder
    hands back, across however many tags feed it -- keyed by tag_id,
    and yields the complete message exactly once a tag_id's full
    frag_count of DISTINCT fragment indices has arrived, regardless of
    arrival order.

    A corrupted fragment (CRC mismatch) is dropped on the spot, same as
    if it were simply lost to noise -- it never occupies that slot, so
    a later GOOD copy of the same fragment (the tag loops its whole
    message forever, so every fragment comes back around eventually)
    can still complete the message.

    Once a tag_id's message completes, its buffer is cleared -- the
    NEXT lap the tag makes around its own loop starts a fresh one, so
    one feed_hex() call per completed loop, not per decoded fragment,
    is the expected, steady-state rate of completions out of this.
    """

    def __init__(self):
        self._partial = {}         # tag_id -> {frag_index: bytes}
        self._expected_count = {}  # tag_id -> frag_count

    def feed_hex(self, hex_str):
        """Takes packet_hex_decoder's own space-separated hex string
        (a decoded packet's "hex" field) and returns (tag_id, message
        bytes) the moment that tag_id's message becomes complete, or
        None otherwise (mid-message, a corrupt/unparseable fragment, or
        too short to even be one of ours). Thin wrapper over
        parse_fragment_hex()+feed_parsed() -- see feed_parsed() for the
        actual reassembly logic; this function is kept so existing
        callers that only have the hex string don't need to change."""
        parsed = parse_fragment_hex(hex_str)
        if parsed is None:
            return None
        return self.feed_parsed(parsed)

    def feed_parsed(self, parsed):
        """Same reassembly logic feed_hex() used to run inline, now
        taking an already-parsed dict (parse_fragment_hex()'s return
        shape) instead of the raw hex string -- lets a caller that
        already parsed the fragment for display purposes (canvas_
        window.py's Fragments table, via TopologyMiddleware.
        get_packets()) hand the SAME parse result in here instead of
        parsing twice. Returns (tag_id, message bytes) the moment that
        tag_id's message becomes complete, or None otherwise (mid-
        message, or a corrupt/malformed fragment -- see below)."""
        tag_id, frag_index, frag_count, used_bytes, crc_ok, chunk = (
            parsed["tag_id"], parsed["frag_index"], parsed["frag_count"],
            parsed["used_bytes"], parsed["crc_ok"], parsed["chunk"],
        )
        # CRC covers the header fields (tag_id/frag_index/frag_count/
        # used_bytes) AND the chunk -- see the module docstring's byte-4
        # note on why the header needed to be in scope too, not just
        # the chunk: tag_id/frag_index are used as trusted dict keys
        # right below, so a corrupted one has to be caught HERE, not
        # downstream.
        if frag_count == 0 or used_bytes > len(chunk) or not crc_ok:
            return None  # corrupted/malformed fragment -- drop it, see docstring

        slots = self._partial.setdefault(tag_id, {})
        self._expected_count[tag_id] = frag_count
        if 0 <= frag_index < frag_count and frag_index not in slots:
            slots[frag_index] = chunk[:used_bytes]

        # Check every index 0..frag_count-1 is actually present, not just
        # len(slots) >= frag_count. The header is CRC-protected now too
        # (see above), so this is no longer guarding against a corrupted
        # frag_index/frag_count slipping through unnoticed -- it's kept
        # as defense in depth (CRC-8 is an 8-bit check, not cryptographic;
        # collisions that pass by chance are rare but not impossible)
        # rather than trusting frag_count alone to mean every index below
        # it is actually present.
        if not all(i in slots for i in range(frag_count)):
            return None
        message = b"".join(slots[i] for i in range(frag_count))
        del self._partial[tag_id]
        del self._expected_count[tag_id]
        return tag_id, message
