import numpy as np
from gnuradio import gr
import time

class packet_hex_decoder(gr.sync_block):
    def __init__(self, preamble="10101011", payload_len_bits=16,
                 samp_rate=2e6, bit_rate=40e3, packet_queue=None):
        gr.sync_block.__init__(
            self,
            name="Packet Hex & ASCII Decoder",
            in_sig=[np.uint8],
            out_sig=None
        )
        self.preamble = [int(b) for b in preamble]
        self.payload_len_bits = int(payload_len_bits)
        self.samp_rate = float(samp_rate)
        self.bit_rate = float(bit_rate)
        self.packet_queue = packet_queue  # Thread-safe queue passed from the UI

        self.sps = max(1, int(round(self.samp_rate / self.bit_rate))) if self.bit_rate > 0 else 1
        self.shift_reg = []
        self.state = "SEARCHING"
        self.collected_bits = []
        self.sample_subcount = 0

    def work(self, input_items, output_items):
        in_bits = input_items[0]
        n_samples = len(in_bits)

        for i in range(n_samples):
            self.sample_subcount += 1
            if self.sample_subcount < self.sps:
                continue
            self.sample_subcount = 0

            bit = 1 if in_bits[i] > 0 else 0

            if self.state == "SEARCHING":
                self.shift_reg.append(bit)
                if len(self.shift_reg) > len(self.preamble):
                    self.shift_reg.pop(0)

                if self.shift_reg == self.preamble:
                    self.state = "COLLECTING"
                    self.collected_bits = []
                    self.shift_reg = []

            elif self.state == "COLLECTING":
                self.collected_bits.append(bit)
                if len(self.collected_bits) >= self.payload_len_bits:
                    self._dispatch_packet(self.collected_bits)
                    self.state = "SEARCHING"
                    self.collected_bits = []

        return n_samples

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
