"""SignalProbe -- taps ONE output port of an already-RUNNING
TopologyMiddleware's live flowgraph, for the inspector's "View signal"
buttons (see ui/canvas_window.py's SignalViewWindow/WaveformWidget).

Why this is a separate, small Tier-2 module rather than just inline code
in canvas_window.py: it's the one place anything here talks to the live
gr.top_block's wiring directly (lock/connect/disconnect/unlock), same
separation TopologyMiddleware itself already draws between "build/run a
real flowgraph" (core/) and "draw it on screen" (ui/).

Design choice worth being explicit about: this deliberately does NOT use
gr-qtgui's own Qt-embedded sink blocks (qtgui.time_sink_c/time_sink_f,
etc.), even though that's the obvious first instinct for "show me this
signal" in a GNU Radio app. Those blocks hand back a raw Qt widget via
sip, bound to whichever Qt binding gr-qtgui itself was compiled against
on this machine (PyQt5 or PyQt6, depending on how GNU Radio was
packaged) -- and this app's own UI is PyQt6. There's no guaranteed way to
embed one inside the other, and getting it wrong would mean a crash or a
blank window, not a clear error. A plain sync_block-based sink has no
such problem: no GUI toolkit opinion at all -- rendering stays entirely
in this app's own PyQt6 code (WaveformWidget), on a plain QPainter
canvas, no extra third-party plotting dependency either.

Tapping an already-wired output port is safe precisely because GNU
Radio ports support fan-out: adding a second connection off an output
that already feeds something else doesn't change what that something
else receives, so a running simulation's actual signal flow is
untouched by opening (or closing) one of these probes.

History (2026-10-22), three attempts to bound this sink's cost without
losing the picture:
  1. gnuradio.blocks.vector_sink_* -- the original choice. Accumulates
     every sample between polls with no bound at all: up to ~100k
     samples sitting in it every SignalViewWindow.POLL_INTERVAL_MS tick
     at real sample rates, all of which read_new_samples() converted to
     Python objects on the main/UI thread. That conversion was the
     actual stutter: a bursty main-thread spike every tick, not
     sustained CPU load.
  2. A custom "keep newest N, drop older" ring buffer. Wrong: N (8192)
     was far smaller than real throughput (~100k samples/poll), so
     nearly every poll silently dropped ~90%+ of the interval and
     stitched together two non-adjacent moments in time -- WaveformWidget
     just concatenated whatever it was handed, so that showed up as a
     visible jump/glitch every tick, worse than the original
     slow-but-continuous vector_sink.
  3. A decimating ring buffer -- decimate evenly across each chunk based
     on a *measured* long-run throughput rate, instead of truncating to
     a recent tail, so retained samples stayed temporally continuous.
     This fixed the glitching, but it coupled the displayed window's
     real-time span to an arbitrary absolute "samples/sec to retain"
     constant with no relationship to the signal's own bit_rate/samp_rate
     -- change those (which this project needs to stay free to do) and
     the amount of real time visible on screen silently shifts with no
     control over it. In practice it made the trace dense/illegible.

This version (current) drops all of that rate-estimation machinery and
instead copies what GNU Radio's own qtgui sinks actually do, confirmed
directly against this project's own compiled blocks -- every hier-block
GRC generates under blocks/*.py exposes a get_samp_rate() accessor (see
e.g. blocks/backscatter_tag_ook.py) for whatever its Sample Rate
parameter is actually set to right now. So: no decimation, no
throughput measurement. Just a small FIXED-SIZE ring of the most recent
_NUM_POINTS raw samples (same "Number of Points" idea GNU Radio's own
time sinks use), and a plain snapshot() read of "whatever's in the ring
right now" -- not an attempt to account for every sample that ever
arrived. Per-poll conversion cost is now bounded by _NUM_POINTS alone,
completely independent of the tapped port's real sample rate, so there
is nothing left here to retune if bit_rate/samp_rate change. The ring's
own samp_rate (for anyone who wants to know how much real time
_NUM_POINTS actually spans) is read straight off the live block via
get_samp_rate() at connect time, never assumed.
"""

import threading

import numpy as np
from gnuradio import gr

import core.registry as registry
from core.modulation_middleware import _port_index


# dtype (registry.Port.dtype) -> matching NumPy scalar type for the ring
# buffer's in_sig. One entry per dtype actually used by an output port
# anywhere in registry.py today ("complex", "byte", "float").
_NUMPY_DTYPES = {
    "complex": np.complex64,
    "float": np.float32,
    "byte": np.uint8,
}

# Fixed display resolution -- the same role GNU Radio's own qtgui time
# sinks' "Number of Points" parameter plays. A display/UI choice, not
# derived from any signal parameter: changing bit_rate/samp_rate in GRC
# never has to touch this. 1024 matches what's already been confirmed to
# look right when driving an actual qtgui sink against this project's
# own signals.
_NUM_POINTS = 1024

# Common spellings of the "sample rate" parameter across this project's
# node types (registry.py: most use "samp_rate", the channel/sender
# blocks use "samp_rate_c1"/"samp_rate_send"). Tried in order against
# the live block's own GRC-generated get_<param>() accessor -- read
# directly off whatever's actually configured, never assumed.
_SAMP_RATE_GETTERS = ("get_samp_rate", "get_samp_rate_c1", "get_samp_rate_send")


class _SnapshotRingSink(gr.sync_block):
    """Fixed-size ring of the most recent _NUM_POINTS raw samples -- no
    decimation, no throughput estimate, no "new since last read"
    bookkeeping. work() (a GNU Radio scheduler thread) just keeps
    overwriting the ring with whatever arrives; snapshot() (the Qt main
    thread, via SignalViewWindow's poll timer) just reads its current
    contents in chronological order. This matches GNU Radio's own
    free-running display mode: every poll shows "the latest window",
    full stop -- there's no bookkeeping left to go wrong the way the
    two previous ring-buffer designs here did."""

    def __init__(self, np_dtype, capacity=_NUM_POINTS):
        gr.sync_block.__init__(self, name="snapshot_ring_sink",
                                in_sig=[np_dtype], out_sig=None)
        self._capacity = capacity
        self._buf = np.zeros(capacity, dtype=np_dtype)
        self._write_pos = 0  # next slot to write, mod capacity
        self._filled = 0     # samples written so far, capped at capacity
        self._lock = threading.Lock()

    def work(self, input_items, output_items):
        in0 = input_items[0]
        n = len(in0)
        if n == 0:
            return 0
        cap = self._capacity
        with self._lock:
            if n >= cap:
                # This one chunk alone already overflows the ring -- it
                # only has room for the tail of it.
                self._buf[:] = in0[-cap:]
                self._write_pos = 0
            else:
                end = self._write_pos + n
                if end <= cap:
                    self._buf[self._write_pos:end] = in0
                else:
                    first = cap - self._write_pos
                    self._buf[self._write_pos:] = in0[:first]
                    self._buf[:end - cap] = in0[first:]
                self._write_pos = end % cap
            self._filled = min(cap, self._filled + n)
        return n

    def snapshot(self):
        """Current ring contents, oldest first, always <= capacity --
        regardless of how much raw data has actually streamed through
        since the last call. That boundedness, independent of real
        throughput, is the entire point."""
        with self._lock:
            filled = self._filled
            if filled < self._capacity:
                # Ring not full yet -- the valid data is exactly the
                # first `filled` samples written, already in order.
                out = self._buf[:filled].copy()
            else:
                start = self._write_pos
                out = np.concatenate((self._buf[start:], self._buf[:start]))
        return out.tolist()


def _resolve_output_dtype(type_name, port_name):
    for p in registry.NODE_TYPES[type_name].ports:
        if p.direction == "out" and p.name == port_name:
            return p.dtype
    raise KeyError(f"{type_name!r} has no OUTPUT port named {port_name!r}")


class SignalProbe:
    """One tap on one (node_id, port_name) output of a running
    TopologyMiddleware. Connects itself on construction, stays connected
    until teardown() is called (idempotent -- safe to call more than
    once, since both the owning window's closeEvent and a forced
    CanvasWindow.on_stop() cleanup may both end up calling it)."""

    def __init__(self, middleware, node_id, type_name, port_name):
        self.middleware = middleware
        self.node_id = node_id
        self.port_name = port_name
        self.dtype = _resolve_output_dtype(type_name, port_name)
        self._port_idx = _port_index(type_name, port_name, "out")
        self._sink = _SnapshotRingSink(_NUMPY_DTYPES[self.dtype])
        self._connected = False
        # Read directly off the live block -- whatever it's actually
        # configured to right now, not a guess. None if this node type's
        # sample-rate param uses a spelling we don't know about yet, or
        # the block doesn't have one -- callers (WaveformWidget) treat
        # that as "don't show a time-span label", not an error.
        self.samp_rate = self._read_samp_rate()
        self._connect()

    def _read_samp_rate(self):
        block = self.middleware.nodes[self.node_id]
        for getter_name in _SAMP_RATE_GETTERS:
            getter = getattr(block, getter_name, None)
            if getter is None:
                continue
            try:
                return float(getter())
            except Exception:
                return None
        return None

    def _connect(self):
        tb = self.middleware.tb
        source_block = self.middleware.nodes[self.node_id]
        tb.lock()
        try:
            tb.connect((source_block, self._port_idx), (self._sink, 0))
        finally:
            tb.unlock()
        self._connected = True

    def read_snapshot(self):
        """Returns the ring's current contents -- see
        _SnapshotRingSink.snapshot(). Always <= _NUM_POINTS samples,
        regardless of how fast the tapped port is actually running."""
        return self._sink.snapshot()

    def teardown(self):
        if not self._connected:
            return
        tb = self.middleware.tb
        source_block = self.middleware.nodes[self.node_id]
        tb.lock()
        try:
            tb.disconnect((source_block, self._port_idx), (self._sink, 0))
        finally:
            tb.unlock()
        self._connected = False
