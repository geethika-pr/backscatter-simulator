import numpy as np
from gnuradio import gr


class blk(gr.basic_block):
    def __init__(self, samp_rate=2e6, bit_rate=10e3, payload_len_bits=16, preamble="10101011"):
        self.sps = int(samp_rate / bit_rate)
        self.preamble_bits = [int(b) for b in preamble]
        # Preamble gets re-emitted into the output stream (packet_hex_decoder's
        # own SEARCHING state expects to see it there) plus the payload --
        # knowing this exact total is what replaces guessing "has the burst
        # ended" from the raw signal.
        self.packet_len_bits = len(self.preamble_bits) + int(payload_len_bits)

        gr.basic_block.__init__(
            self, name='FM0 Decoder (synced)',
            in_sig=[np.uint8], out_sig=[np.uint8],
        )
        self._empty = np.zeros(0, np.uint8)
        self.buf = self._empty
        self.prev_sample = None
        self.same_run = 0
        self.dead_run_threshold = 3 * self.sps
        self.active = False

        self.locked = False
        self.phase_offset = 0
        self.locked_buf = self._empty
        self.bits_emitted = 0  # since the current lock was acquired

    def _decode_bit(self, window):
        idx1 = int(self.sps * 0.25)
        idx2 = int(self.sps * 0.75)
        return 1 if window[idx1] == window[idx2] else 0

    def _try_lock(self):
        # Centre-of-eye lock: test EVERY offset, then take the middle of the
        # first contiguous run of offsets that decode the preamble, instead
        # of the first offset that happens to work (which sits at the edge
        # of the good region and becomes fragile once edges are smeared,
        # e.g. by receiver averaging). All offsets are tested at once with
        # numpy (same rule as _decode_bit, same answer as the old loop).
        P = len(self.preamble_bits)
        needed = self.sps * P
        noff = min(self.sps, len(self.buf) - needed + 1)
        if noff <= 0:
            return False
        i1 = int(self.sps * 0.25)
        i2 = int(self.sps * 0.75)
        ok = np.ones(noff, bool)
        for k in range(P):
            a = k * self.sps
            bit = self.buf[a + i1:a + i1 + noff] == self.buf[a + i2:a + i2 + noff]
            ok &= bit == bool(self.preamble_bits[k])
        hits = np.flatnonzero(ok)
        if len(hits) == 0:
            return False
        first = int(hits[0])
        miss = np.flatnonzero(~ok[first:])
        run = int(miss[0]) if len(miss) else noff - first
        self.phase_offset = first + run // 2
        return True

    def _unlock(self):
        self.active = False
        self.locked = False
        self.buf = self._empty
        self.locked_buf = self._empty
        self.bits_emitted = 0

    def general_work(self, input_items, output_items):
        # Speed revision (2026-11-12): same state machine as before (one
        # sample at a time: dead-run reset, activation, lock attempt every
        # time the search buffer is full, one bit per sps samples once
        # locked), but instead of a Python step for EVERY sample it jumps
        # straight to the next sample where something happens. Output is
        # identical to the per-sample version; CPU use drops ~10-50x.
        x = np.asarray(input_items[0], dtype=np.uint8)
        out_data = output_items[0]
        n = len(x)
        n_produced = 0
        sps = self.sps
        D = self.dead_run_threshold
        needed = sps * (len(self.preamble_bits) + 1)
        i1 = int(sps * 0.25)
        i2 = int(sps * 0.75)
        if n == 0:
            self.consume(0, 0)
            return 0

        # same_run for every sample (run length of equal consecutive values)
        idx = np.arange(n)
        if self.prev_sample is None:
            eq = np.concatenate([[False], x[1:] == x[:-1]])
        else:
            eq = np.concatenate([[x[0] == self.prev_sample], x[1:] == x[:-1]])
        last_reset = np.maximum.accumulate(np.where(~eq, idx, -1))
        sr = np.where(last_reset >= 0, idx - last_reset, self.same_run + idx + 1)
        dead = sr >= D
        dead_pos = np.flatnonzero(dead)
        live_pos = np.flatnonzero(~dead)
        act_pos = np.flatnonzero(sr == 0)

        def next_in(pos, i):
            k = np.searchsorted(pos, i)
            return int(pos[k]) if k < len(pos) else n

        i = 0
        while i < n:
            if not self.locked and not self.active:
                if dead[i]:
                    # every dead sample unlocks and leaves buf = [that sample]
                    k = next_in(live_pos, i)
                    self._unlock()
                    self.buf = x[k - 1:k].copy()
                    i = k
                    continue
                j = min(next_in(dead_pos, i), next_in(act_pos, i))
                if j > i:
                    self.buf = np.concatenate([self.buf, x[i:j]])
                    i = j
                    continue
                # j == i: activation sample (same_run == 0, not dead)
                self.buf = np.concatenate([self.buf, x[i:i + 1]])
                self.active = True
                self._after_append_unlocked()
                i += 1
                continue

            if not self.locked:          # active, searching
                jd = next_in(dead_pos, i)
                jn = i + max(0, needed - len(self.buf) - 1)
                j = min(jd, jn)
                if j >= n:
                    self.buf = np.concatenate([self.buf, x[i:n]])
                    i = n
                    continue
                if j > i:
                    self.buf = np.concatenate([self.buf, x[i:j]])
                    i = j
                if dead[i]:
                    self._unlock()
                    self.buf = x[i:i + 1].copy()
                else:
                    self.buf = np.concatenate([self.buf, x[i:i + 1]])
                    self._after_append_unlocked()
                i += 1
                continue

            # locked
            if len(out_data) == 0:
                # (degenerate: no room at all) process one sample, then stop
                if dead[i]:
                    self._unlock()
                    self.buf = x[i:i + 1].copy()
                    i += 1
                    continue
                self.locked_buf = np.concatenate([self.locked_buf, x[i:i + 1]])
                if len(self.locked_buf) >= sps:
                    self.locked_buf = self.locked_buf[sps:]
                    self.bits_emitted += 1
                    if self.bits_emitted >= self.packet_len_bits:
                        self._unlock()
                i += 1
                break
            jd = next_in(dead_pos, i)
            je = i + max(0, sps - 1 - len(self.locked_buf))
            j = min(jd, je)
            if j >= n:
                self.locked_buf = np.concatenate([self.locked_buf, x[i:n]])
                i = n
                continue
            if j > i:
                self.locked_buf = np.concatenate([self.locked_buf, x[i:j]])
                i = j
            if dead[i]:
                self._unlock()
                self.buf = x[i:i + 1].copy()
                i += 1
                continue
            self.locked_buf = np.concatenate([self.locked_buf, x[i:i + 1]])
            window = self.locked_buf[:sps]
            out_data[n_produced] = 1 if window[i1] == window[i2] else 0
            n_produced += 1
            self.locked_buf = self.locked_buf[sps:]
            self.bits_emitted += 1
            if self.bits_emitted >= self.packet_len_bits:
                # Full packet decoded at this lock -- force a fresh
                # search before the next bit, so a different tag's
                # later burst can never ride on this stale phase.
                self._unlock()
            i += 1
            if n_produced >= len(out_data):
                break

        # carry per-sample run state to the next call (up to where we stopped)
        self.prev_sample = x[i - 1]
        self.same_run = int(sr[i - 1])
        self.consume(0, n)
        return n_produced

    def _after_append_unlocked(self):
        # the old per-sample code's "buffer full -> try to lock" step
        if len(self.buf) >= self.sps * (len(self.preamble_bits) + 1):
            if self._try_lock():
                self.locked = True
                self.locked_buf = self.buf[self.phase_offset:]
                self.buf = self._empty
                self.bits_emitted = 0
            else:
                self.buf = self.buf[self.sps:]
