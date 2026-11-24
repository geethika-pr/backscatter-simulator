import numpy as np
from gnuradio import gr


class burst_player(gr.basic_block):
    """Tag burst player: replaces the tag's Repeat + Multiply pair.

    in0: the tag's symbol stream (0/1 floats, one item per symbol, BEFORE
         repetition -- the same thing that used to go into the Repeat block)
    in1: the gate from Tag PIE Decoder (1 = transmitting, 0 = silent),
         one item per sample
    out: gated, repeated symbol levels, one item per sample (the same thing
         Multiply used to output)

    Difference from Repeat + Multiply: the data only moves forward while the
    gate is open. Before, the data ran from the moment Start was pressed, so
    when the tag started talking (~120 samples later, after the reader's
    command) it began partway through the first packet and that packet was
    lost. Now every burst starts at the beginning of a packet, the way a
    real tag starts its reply from the first bit.

    interp:        samples per symbol (same value the Repeat block had)
    sym_per_bit:   symbols per data bit (OOK/BPSK 1, FM0 2, Miller 2*M) --
                   only used to convert lead_in_bits into symbols
    lead_in_bits:  optional pilot played at the start of every burst, before
                   the first packet: alternating levels at the symbol rate
                   (OOK/BPSK: 1010..., FM0: FM0 zeros, Miller: plain
                   subcarrier). 0 = off. Gives the receiver's DC blocker time
                   to settle on the tag's signal. The burst must be made
                   longer by the same number of bits (the UI does this), or
                   the end of the last packet gets cut.
    """

    def __init__(self, interp=200, sym_per_bit=1, lead_in_bits=0):
        gr.basic_block.__init__(
            self,
            name="Tag Burst Player",
            in_sig=[np.float32, np.float32],
            out_sig=[np.float32],
        )
        self.interp = max(1, int(interp))
        self.sym_per_bit = max(1, int(sym_per_bit))
        self.lead_in_bits = max(0, int(lead_in_bits))
        self.pos = 0            # samples already played of the current symbol
        self.lead_left = 0      # lead-in symbols still to play in this burst
        self.lead_level = 1.0   # current lead-in level (alternates)
        # OOK/BPSK (one symbol per bit): change level every 2 symbols
        # (1100 1100...). A plain 1010... pilot looks like the preamble
        # (10101011): while the DC blocker is still settling, one 0 read as
        # a 1 makes a fake "...1011" and the decoder locks onto the pilot,
        # swallowing the first real packet (measured). 1100 needs several
        # wrong bits to look like the preamble. FM0/Miller: change every
        # symbol (= FM0 zeros / plain Miller subcarrier, the usual pilot).
        self.lead_run = 2 if self.sym_per_bit == 1 else 1
        self.lead_count = 0
        self.prev_gate = 0.0

    def set_lead_in_bits(self, lead_in_bits):
        self.lead_in_bits = max(0, int(lead_in_bits))

    def general_work(self, input_items, output_items):
        sym = input_items[0]
        gate = input_items[1]
        out = output_items[0]
        n = min(len(gate), len(out))
        n_sym = len(sym)
        k = 0                   # symbols consumed from in0
        i = 0
        while i < n:
            g = gate[i] > 0.5
            if not g:
                # silent: find the end of this closed stretch in one go
                opened = np.flatnonzero(gate[i:n] > 0.5)
                j = i + (int(opened[0]) if len(opened) else n - i)
                out[i:j] = 0.0
                self.prev_gate = 0.0
                i = j
                continue
            if self.prev_gate <= 0.5:
                # burst starts: queue the lead-in, start on a fresh symbol
                self.lead_left = self.lead_in_bits * self.sym_per_bit
                self.lead_level = 1.0
                self.lead_count = 0
                self.pos = 0
            self.prev_gate = 1.0
            # how many samples until the current symbol ends or the gate closes
            closed = np.flatnonzero(gate[i:n] <= 0.5)
            run = int(closed[0]) if len(closed) else n - i
            step = min(run, self.interp - self.pos)
            if self.lead_left > 0:
                level = self.lead_level
            else:
                if k >= n_sym:
                    break       # need more symbols; produce what we have
                level = sym[k]
            out[i:i + step] = level
            i += step
            self.pos += step
            if self.pos >= self.interp:
                self.pos = 0
                if self.lead_left > 0:
                    self.lead_left -= 1
                    self.lead_count += 1
                    if self.lead_count >= self.lead_run:
                        self.lead_count = 0
                        self.lead_level = 1.0 - self.lead_level
                else:
                    k += 1
        self.consume(0, k)
        self.consume(1, i)
        return i
