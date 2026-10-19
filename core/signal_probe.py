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
blank window, not a clear error. A plain vector_sink_* has no such
problem: it's just a block that accumulates samples for Python to read
back (`.data()`), with no GUI toolkit opinion at all -- rendering stays
entirely in this app's own PyQt6 code (WaveformWidget), on a plain
QPainter canvas, no extra third-party plotting dependency either.

Tapping an already-wired output port is safe precisely because GNU
Radio ports support fan-out: adding a second connection off an output
that already feeds something else doesn't change what that something
else receives, so a running simulation's actual signal flow is
untouched by opening (or closing) one of these probes.
"""

from gnuradio import blocks

import core.registry as registry
from core.modulation_middleware import _port_index


# dtype (registry.Port.dtype) -> the matching gnuradio.blocks vector_sink
# factory. One real sink type per dtype actually used by an output port
# anywhere in registry.py today ("complex", "byte", "float") -- no
# conversion blocks needed, since vector_sink_b/_f/_c all exist as
# standard blocks and each already matches one of these three exactly.
_SINK_FACTORIES = {
    "complex": lambda: blocks.vector_sink_c(1),
    "float": lambda: blocks.vector_sink_f(1),
    "byte": lambda: blocks.vector_sink_b(1),
}


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
        self._sink = _SINK_FACTORIES[self.dtype]()
        self._connected = False
        self._connect()

    def _connect(self):
        tb = self.middleware.tb
        source_block = self.middleware.nodes[self.node_id]
        tb.lock()
        try:
            tb.connect((source_block, self._port_idx), (self._sink, 0))
        finally:
            tb.unlock()
        self._connected = True

    def read_new_samples(self):
        """Drains and resets the underlying vector_sink -- returns only
        whatever arrived since the last call (or since connect(), the
        first time), not the whole run's history. Without the reset()
        half of this, .data() would keep returning an ever-growing list
        for as long as the simulation runs, and memory would grow
        without bound for a probe left open a while."""
        data = list(self._sink.data())
        self._sink.reset()
        return data

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
