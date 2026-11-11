import numpy as np
from gnuradio import gr

class tag_pie_decoder(gr.sync_block):
    """
    State-machine based PIE command detector and slotted burst gate.
    States:
      0: IDLE (Listening for reader interrogation pulse [0, 1, 0, 1])
      1: DELAY (Interrogation received, counting down slot_id delay)
      2: BURST (Gate open, transmitting payload)
    """
    STATE_IDLE = 0
    STATE_DELAY = 1
    STATE_BURST = 2

    def __init__(self, samp_rate=2e6, bit_rate=40e3, packet_len_bits=16,
                 tari_us=12.5, threshold=0.5, enable_mac=True, slot_id=0,
                 target_cmd_bits="0101"):
        gr.sync_block.__init__(
            self,
            name="Tag PIE Decoder & Gate",
            in_sig=[np.complex64],
            out_sig=[np.float32]
        )
        self.samp_rate = float(samp_rate)
        self.bit_rate = float(bit_rate)
        self.packet_len_bits = int(packet_len_bits)
        self.tari_us = float(tari_us)
        self.threshold = float(threshold)   # now a FRACTION of the carrier level (0.6 = 60%)
        self.ref_level = 0.0                # running carrier level (peak follower)
        self.ref_decay = 0.9999             # how slowly ref_level forgets (~5 ms at 2 MHz)
        self.enable_mac = bool(enable_mac)
        self.slot_id = int(slot_id)

        self._recalculate_timing()

        # Detection variables
        self.high_count = 0
        self.in_notch = False
        self.decoded_bits = []
        self.target_cmd_bits = str(target_cmd_bits)
        self.target_cmd = self._parse_cmd_bits(self.target_cmd_bits)

        # Explicit state machine
        self.state = self.STATE_IDLE
        self.counter = 0

    def _parse_cmd_bits(self, bits_str):
        # Turn a string like "0101" into the same [0, 1, 0, 1]-style list
        # the match logic below compares self.decoded_bits against. Any
        # character that isn't '0' or '1' (spaces, commas, etc.) is just
        # skipped, so "0, 1, 0, 1" or "0 1 0 1" also work.
        parsed = [1 if ch == '1' else 0 for ch in str(bits_str) if ch in ('0', '1')]
        if not parsed:
            # Empty/garbage input would make `decoded_bits == target_cmd`
            # trivially true the moment decoded_bits is also empty --
            # fall back to the original default instead of matching on
            # nothing.
            return [0, 1, 0, 1]
        return parsed

    def _recalculate_timing(self):

        self.tari_samples = int(self.tari_us * 1e-6 * self.samp_rate)
        # Decision boundary between data-0 (0.5 * Tari) and data-1 (1.25 * Tari)
        self.decision_boundary = int(0.875 * self.tari_samples)
        if self.decision_boundary < 1:
            self.decision_boundary = 1

        if self.packet_len_bits > 0 and self.bit_rate > 0:
            self.samples_per_bit = int(self.samp_rate / self.bit_rate)
            self.burst_window_samples = self.packet_len_bits * self.samples_per_bit
            self.guard_samples = int(0.25 * self.burst_window_samples)
            self.slot_duration_samples = self.burst_window_samples + self.guard_samples
            print(f"[DEBUG]: packet_len_bits={self.packet_len_bits}, burst_window_samples={self.burst_window_samples}", flush=True)
        else:
            # Fallback if unconfigured: 500 samples default burst
            self.burst_window_samples = 1000
            self.slot_duration_samples = 1200
            print(f"[DEBUG]: packet_len_bits={self.packet_len_bits}, burst_window_samples={self.burst_window_samples}", flush=True)

    def set_slot_id(self, slot_id):
        self.slot_id = int(slot_id)

    def set_threshold(self, threshold):
        self.threshold = float(threshold)

    def set_enable_mac(self, enable_mac):
        self.enable_mac = bool(enable_mac)

    def set_packet_len_bits(self, packet_len_bits):
        self.packet_len_bits = int(packet_len_bits)
        self._recalculate_timing()

    def set_target_cmd_bits(self, target_cmd_bits):
        self.target_cmd_bits = str(target_cmd_bits)
        self.target_cmd = self._parse_cmd_bits(self.target_cmd_bits)
        # A mid-run length/content change could leave a stale
        # decoded_bits window comparing against the new target at the
        # wrong length -- reset it so the next match is clean.
        self.decoded_bits = []
        self.high_count = 0
        self.in_notch = False

    def work(self, input_items, output_items):
        rf_in = input_items[0]
        gate_out = output_items[0]
        n_samples = len(rf_in)

        if not self.enable_mac:
            gate_out[:] = 1.0
            return n_samples

        mag = np.abs(rf_in)
        i = 0
        while i < n_samples:
            # STATE 2: BURST -- counter decrement is data-independent,
            # so skip straight to where it would hit zero.
            if self.state == self.STATE_BURST:
                n_needed = self.counter if self.counter >= 1 else 1
                remaining = n_samples - i
                if n_needed > remaining:
                    gate_out[i:n_samples] = 1.0
                    self.counter -= remaining
                    i = n_samples
                else:
                    gate_out[i:i + n_needed] = 1.0
                    self.counter -= n_needed
                    self.state = self.STATE_IDLE
                    self.decoded_bits = []
                    self.high_count = 0
                    self.in_notch = False
                    print(f">>> [Tag Slot {self.slot_id}]: Burst finished. Sleeping.", flush=True)
                    i += n_needed

            # STATE 1: SLOT DELAY COUNTDOWN -- same reasoning.
            elif self.state == self.STATE_DELAY:
                n_needed = self.counter if self.counter >= 1 else 1
                remaining = n_samples - i
                if n_needed > remaining:
                    gate_out[i:n_samples] = 0.0
                    self.counter -= remaining
                    i = n_samples
                else:
                    if n_needed > 1:
                        gate_out[i:i + n_needed - 1] = 0.0
                    self.state = self.STATE_BURST
                    self.counter = self.burst_window_samples
                    gate_out[i + n_needed - 1] = 1.0
                    print(f">>> [Tag Slot {self.slot_id}]: Slot delay elapsed! Firing burst.", flush=True)
                    i += n_needed

            # STATE 0: IDLE -- genuinely per-sample, has to inspect mag[i].
            else:
                gate_out[i] = 0.0
                sample_mag = mag[i]

                # Track the carrier level: jump up instantly, sink back very slowly,
                # so short PIE notches don't drag the reference down.
                self.ref_level = max(sample_mag, self.ref_level * self.ref_decay)

                if sample_mag < self.threshold * self.ref_level:
                    if not self.in_notch:
                        if self.high_count >= 6:
                            bit = 1 if self.high_count >= self.decision_boundary else 0
                            self.decoded_bits.append(bit)
                            print(f"[Tag Slot {self.slot_id}]: Saw bit {bit} (high_count={self.high_count})", flush=True)
                        self.in_notch = True
                        self.high_count = 0
                else:
                    if self.in_notch:
                        self.in_notch = False
                        self.high_count = 1
                    else:
                        self.high_count += 1
                        if len(self.decoded_bits) == 3 and self.high_count > int(self.decision_boundary * 1.5):
                            self.decoded_bits.append(1)
                            print(f"[Tag Slot {self.slot_id}]: Saw bit 1 (trailing CW)", flush=True)
                            self.high_count = 1000

                if len(self.decoded_bits) > len(self.target_cmd):
                    self.decoded_bits.pop(0)

                if self.decoded_bits == self.target_cmd:
                    print(f">>> [Tag Slot {self.slot_id}]: MATCH {self.target_cmd}! Scheduling slot...", flush=True)
                    self.decoded_bits = []
                    self.high_count = 0
                    self.in_notch = False

                    delay = self.slot_id * self.slot_duration_samples
                    if delay > 0:
                        self.state = self.STATE_DELAY
                        self.counter = delay
                    else:
                        self.state = self.STATE_BURST
                        self.counter = self.burst_window_samples
                        gate_out[i] = 1.0
                i += 1

        return n_samples
