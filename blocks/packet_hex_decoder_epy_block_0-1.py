import numpy as np
from gnuradio import gr
import time

class packet_hex_decoder(gr.sync_block):
    """Finds the preamble in a sliced 0/1 stream and dispatches each packet.

    Timing (2026-11-11 revision): when the input runs at several samples per
    bit (OOK/BPSK, sps = samp_rate/bit_rate, e.g. 200), the preamble is
    searched at EVERY sample offset, not only at one fixed offset per bit.
    The offsets where it matches form one contiguous run around the bits;
    the decoder takes the MIDDLE of that run as its sampling point for the
    payload (centre of the "eye"). Before, the sampling point was wherever
    the stream happened to start, which is harmless on raw samples but
    breaks receiver averaging (the moving average needs the decision near
    the end of each bit). With sps == 1 (FM0/Miller, whose own decoders
    already output one value per bit) every run has length 1, so behaviour
    is exactly as before.
    """
    def __init__(self, preamble="10101011", payload_len_bits=16,
        samp_rate=2e6, bit_rate=40e3, packet_queue=None, accept_inverted=False):
        gr.sync_block.__init__(
            self,
            name="Packet Hex & ASCII Decoder",
            in_sig=[np.uint8],
            out_sig=None
        )
        self.preamble = [int(b) for b in preamble]

        # Costas-loop phase tracking can lock 180 deg off for BPSK, which inverts
        # every bit. If enabled, an inverted preamble also starts a packet and
        # the payload is flipped back.
        self.accept_inverted = bool(accept_inverted)
        self.inv_preamble = [1 - b for b in self.preamble]
        self.invert = False

        self.payload_len_bits = int(payload_len_bits)
        self.samp_rate = float(samp_rate)
        self.bit_rate = float(bit_rate)
        self.packet_queue = packet_queue  # Thread-safe queue passed from the UI

        self.sps = max(1, int(round(self.samp_rate / self.bit_rate))) if self.bit_rate > 0 else 1
        # Minimum matching offsets (within one bit) to accept a preamble. Noise
        # alone matches an 8-bit pattern at ~1 in 256 offsets (~0.8 per 200), so
        # sps/16 (12 at 200 sps) practically never comes from noise; a real
        # preamble gives up to sps matches. With sps == 1 this is 1 = old behaviour.
        self.min_hits = max(1, self.sps // 16)

        # Sample bookkeeping uses ABSOLUTE sample numbers (count since start).
        self.abs_start = 0                  # absolute index of in_bits[0] this call
        self.span = (len(self.preamble) - 1) * self.sps   # preamble length in samples, minus one bit
        self.hist = np.zeros(0, dtype=np.uint8)            # last `span` samples of the previous call
        self.search_from = 0                # earliest absolute sample a preamble may START at
        self.state = "SEARCHING"
        self.next_sample = 0                # absolute index of the next payload decision
        self.collected_bits = []

    def _match_masks(self, x, x_abs0):
        """For every sample t of x (absolute index x_abs0 + t) that can be a
        preamble's LAST decision point, does the preamble (or its inverse)
        appear when reading every sps-th sample backwards from t?"""
        n = len(x)
        P, sps = len(self.preamble), self.sps
        t = np.arange(self.span, n)
        if len(t) == 0:
            return t, np.zeros(0, bool), np.zeros(0, bool)
        bits = (x > 0).astype(np.uint8)
        m = np.ones(len(t), bool)
        mi = np.ones(len(t), bool) if self.accept_inverted else np.zeros(len(t), bool)
        for k in range(P):
            col = bits[t - (P - 1 - k) * sps]
            m &= (col == self.preamble[k])
            if self.accept_inverted:
                mi &= (col == self.inv_preamble[k])
        # a preamble may only start at or after search_from (i.e. entirely after
        # the previous packet, same as the old shift-register reset)
        start_abs = x_abs0 + t - self.span
        ok = start_abs >= self.search_from
        return t, m & ok, mi & ok

    def work(self, input_items, output_items):
        in_bits = input_items[0]
        n_new = len(in_bits)
        x = np.concatenate([self.hist, in_bits]) if len(self.hist) else np.asarray(in_bits)
        x_abs0 = self.abs_start - len(self.hist)     # absolute index of x[0]
        end_abs = self.abs_start + n_new             # one past the last sample we have

        sps = self.sps
        while True:
            if self.state == "SEARCHING":
                t, m, mi = self._match_masks(x, x_abs0)
                hits = np.flatnonzero(m | mi)
                if len(hits) == 0:
                    break
                first = hits[0]
                # Need two bits' worth of offsets after the first hit to judge it.
                if t[first] + 2 * sps > len(x):
                    self.search_from = x_abs0 + t[first] - self.span
                    break
                # A real preamble matches at MANY neighbouring offsets (one bit
                # wide); random noise (e.g. before the tag starts) matches at
                # isolated offsets. Slide a one-bit-wide window over the hits that
                # start within one bit of the first hit, keep the window with the
                # most hits (either polarity), and require at least min_hits.
                best = (0, None, None)          # (count, mask, window start)
                for mask in (m, mi):
                    hp = np.flatnonzero(mask)
                    hp = hp[(hp >= first) & (hp < first + sps)]
                    for h in hp:
                        c = int(np.count_nonzero(mask[h:h + sps]))
                        if c > best[0]:
                            best = (c, mask, h)
                count, mask, h = best
                if count < self.min_hits:
                    # isolated chance match: forbid preambles starting there, search on
                    self.search_from = x_abs0 + t[first] - self.span + 1
                    continue
                in_window = h + np.flatnonzero(mask[h:h + sps])
                centre_abs = int(x_abs0 + t[in_window[len(in_window) // 2]])
                self.invert = mask is mi and mask is not m
                self.state = "COLLECTING"
                self.collected_bits = []
                self.next_sample = centre_abs + sps
            if self.state == "COLLECTING":
                while self.next_sample < end_abs and len(self.collected_bits) < self.payload_len_bits:
                    bit = 1 if x[self.next_sample - x_abs0] > 0 else 0
                    self.collected_bits.append(bit ^ 1 if self.invert else bit)
                    self.next_sample += self.sps
                if len(self.collected_bits) >= self.payload_len_bits:
                    self._dispatch_packet(self.collected_bits)
                    self.collected_bits = []
                    self.state = "SEARCHING"
                    # the next preamble must start after this packet's last decision
                    self.search_from = self.next_sample - self.sps + 1
                    continue
                break

        keep = min(len(x), self.span + 2 * self.sps)
        self.hist = np.array(x[len(x) - keep:], dtype=np.uint8)
        self.abs_start = end_abs
        return n_new

    def _dispatch_packet(self, bits):
        byte_array = bytearray()
        for b_idx in range(0, len(bits), 8):
            chunk = bits[b_idx:b_idx+8]
            val = 0
            for bit in chunk:
                val = (val << 1) | bit
            if len(chunk) < 8:
                val <<= (8 - len(chunk))
            byte_array.append(val)

        hex_str = " ".join(f"{b:02X}" for b in byte_array)
        ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in byte_array)

        packet_data = {
            "timestamp": time.time(),
            "bits": len(bits),
            "hex": hex_str,
            "ascii": ascii_str
        }

        # If a queue was supplied by the UI, push the structured packet
        if self.packet_queue is not None:
            self.packet_queue.put(packet_data)
        else:
            print(f"[DECODED]: {hex_str} | {ascii_str}", flush=True)
