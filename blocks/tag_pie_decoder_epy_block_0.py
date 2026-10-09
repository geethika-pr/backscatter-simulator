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
                 tari_us=12.5, threshold=0.5, enable_mac=True, slot_id=0):
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
        self.threshold = float(threshold)
        self.enable_mac = bool(enable_mac)
        self.slot_id = int(slot_id)

        self._recalculate_timing()

        # Detection variables
        self.high_count = 0
        self.in_notch = False
        self.decoded_bits = []
        self.target_cmd = [0, 1, 0, 1]

        # Explicit state machine
        self.state = self.STATE_IDLE
        self.counter = 0

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

    def work(self, input_items, output_items):
        rf_in = input_items[0]
        gate_out = output_items[0]
        n_samples = len(rf_in)

        if not self.enable_mac:
            gate_out[:] = 1.0
            return n_samples

        mag = np.abs(rf_in)

        for i in range(n_samples):
            # -------------------------------------------------------------
            # STATE 2: BURST TRANSMISSION
            # -------------------------------------------------------------
            if self.state == self.STATE_BURST:
                gate_out[i] = 1.0
                self.counter -= 1
                if self.counter <= 0:
                    # Packet burst finished -> return to listening
                    self.state = self.STATE_IDLE
                    self.decoded_bits = []
                    self.high_count = 0
                    self.in_notch = False
                    print(f">>> [Tag Slot {self.slot_id}]: Burst finished. Sleeping.", flush=True)

            # -------------------------------------------------------------
            # STATE 1: SLOT DELAY COUNTDOWN
            # -------------------------------------------------------------
            elif self.state == self.STATE_DELAY:
                gate_out[i] = 0.0
                self.counter -= 1
                if self.counter <= 0:
                    # Delay over -> trigger transmission
                    self.state = self.STATE_BURST
                    self.counter = self.burst_window_samples
                    gate_out[i] = 1.0
                    print(f">>> [Tag Slot {self.slot_id}]: Slot delay elapsed! Firing burst.", flush=True)

            # -------------------------------------------------------------
            # STATE 0: IDLE (DECODING READER COMMAND)
            # -------------------------------------------------------------
            else:
                gate_out[i] = 0.0
                sample_mag = mag[i]

                # Notch low detection
                if sample_mag < self.threshold:
                    if not self.in_notch:
                        if self.high_count >= 6:
                            bit = 1 if self.high_count >= self.decision_boundary else 0
                            self.decoded_bits.append(bit)
                            print(f"[Tag Slot {self.slot_id}]: Saw bit {bit} (high_count={self.high_count})", flush=True)
                        self.in_notch = True
                        self.high_count = 0
                else:
                    # Carrier high
                    if self.in_notch:
                        self.in_notch = False
                        self.high_count = 1
                    else:
                        self.high_count += 1
                        # Capture trailing bit when carrier returns to continuous CW
                        if len(self.decoded_bits) == 3 and self.high_count > int(self.decision_boundary * 1.5):
                            self.decoded_bits.append(1)
                            print(f"[Tag Slot {self.slot_id}]: Saw bit 1 (trailing CW)", flush=True)
                            self.high_count = 1000

                # Keep window bounded to target length
                if len(self.decoded_bits) > len(self.target_cmd):
                    self.decoded_bits.pop(0)

                # Command Match Check
                if self.decoded_bits == self.target_cmd:
                    print(f">>> [Tag Slot {self.slot_id}]: MATCH [0, 1, 0, 1]! Scheduling slot...", flush=True)
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

        return n_samples
