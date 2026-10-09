import numpy as np
from gnuradio import gr


class miller_decoder(gr.basic_block):
    def __init__(self, M=2, samp_rate=2e6, bit_rate=10e3, payload_len_bits=16, preamble="10101011"):
        self.M = int(M)
        self.sps = max(1, int(round(samp_rate / bit_rate)))
        self.seg = max(1, self.sps // (2 * self.M))
        self.preamble_bits = [int(b) for b in preamble]
        self.preamble_len = len(self.preamble_bits)
        self.packet_len_bits = self.preamble_len + int(payload_len_bits)

        gr.basic_block.__init__(
            self,
            name=f'Miller Decoder (M={self.M})',
            in_sig=[np.uint8],
            out_sig=[np.uint8],
        )

        self.buf = np.array([], dtype=np.uint8)
        self.locked = False
        self.bits_emitted = 0
        self._printed_lock = False
        self._dump_done = False
        self._dump_buf = []

    def _decode_bit(self, chunk):
        # Tolerant decode, used once locked -- deliberately loose about
        # noise/fading so a degraded in-packet bit still decodes to its
        # best guess instead of dropping the whole packet.
        idx = [min(j * self.seg + self.seg // 2, len(chunk) - 1) for j in range(2 * self.M)]
        transitions = np.count_nonzero(np.diff(chunk[idx]))
        return 1 if transitions < 2 * self.M - 1 else 0

    def _decode_bit_strict(self, chunk):
        # Used ONLY while searching for the preamble. A genuine
        # Miller-encoded bit's 2M midpoint samples always form one of
        # exactly two shapes: fully alternating (bit=0), or alternating
        # everywhere EXCEPT the one boundary exactly in the middle
        # (bit=1). A window straddling the dead/silent gap between one
        # tag's burst and the next can satisfy the loose "count the
        # transitions" rule used by _decode_bit above by accident -- a
        # flat gap segment followed by real signal segments can produce
        # a transition count that matches a real bit's, with the one
        # "missing" transition sitting wherever the gap happened to end
        # rather than at that exact middle boundary -- and that false
        # match is what let the brute-force search below lock onto the
        # wrong phase, corrupting a few bits of the real packet that
        # followed it (the "D8 instead of C8" multi-tag bug). Checking
        # the exact SHAPE, not just the transition count, is what tells
        # a genuine preamble bit apart from this spurious mashup.
        idx = [min(j * self.seg + self.seg // 2, len(chunk) - 1) for j in range(2 * self.M)]
        vals = chunk[idx]
        diffs = [bool(vals[i] != vals[i + 1]) for i in range(len(vals) - 1)]
        n_trans = sum(diffs)
        if n_trans == len(diffs):
            return 0  # alternates at every boundary
        if n_trans == len(diffs) - 1 and not diffs[self.M - 1]:
            return 1  # alternates everywhere except the exact middle boundary
        return None  # doesn't match either valid shape -- not a real bit

    def _decode_bits_at_strict(self, start, n_bits):
        bits = []
        for k in range(n_bits):
            a = start + k * self.sps
            bit = self._decode_bit_strict(self.buf[a:a + self.sps])
            if bit is None:
                return None
            bits.append(bit)
        return bits

    def _search_for_lock(self):
        needed = self.sps * (self.preamble_len + 1)
        while len(self.buf) >= needed:
            for phase in range(self.sps):
                if phase + self.sps * self.preamble_len > len(self.buf):
                    break
                if self._decode_bits_at_strict(phase, self.preamble_len) == self.preamble_bits:
                    return phase
            self.buf = self.buf[self.sps:]
        return None

    def general_work(self, input_items, output_items):
        in_data = input_items[0]
        out = output_items[0]

        if not self._dump_done:
            self._dump_buf.extend(in_data.tolist())
            if len(self._dump_buf) >= 4000:
                with open("miller_raw_dump.txt", "w") as f:
                    f.write(",".join(str(x) for x in self._dump_buf[:4000]))
                print("[Miller Decoder] raw dump written", flush=True)
                self._dump_done = True

        self.buf = np.concatenate([self.buf, in_data])
        self.consume(0, len(in_data))

        if not self.locked:
            offset = self._search_for_lock()
            if offset is None:
                return 0
            self.buf = self.buf[offset:]
            self.locked = True
            self.bits_emitted = 0
            if not self._printed_lock:
                print("[Miller Decoder] locked", flush=True)
                self._printed_lock = True

        remaining_in_packet = self.packet_len_bits - self.bits_emitted
        n_bits = min(len(self.buf) // self.sps, len(out), remaining_in_packet)
        if n_bits == 0:
            return 0

        for k in range(n_bits):
            out[k] = self._decode_bit(self.buf[k * self.sps:(k + 1) * self.sps])

        self.buf = self.buf[n_bits * self.sps:]
        self.bits_emitted += n_bits

        if self.bits_emitted >= self.packet_len_bits:
            self.locked = False
            self.bits_emitted = 0
            self._printed_lock = False

        return n_bits
