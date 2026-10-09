"""
Step 6 — modulation-aware UI, built on core.modulation_middleware's
generalized BackscatterMiddleware instead of the OOK-only
MultiTagOokMiddleware. Same 2-tag fixed pipeline shape and Setup-vs-Live
field split as before (see git history / multi_tag_middleware.py for
that reasoning) -- the new piece is a modulation dropdown that picks
OOK/BPSK/FM0/Miller. Analog is left out of the dropdown entirely rather
than offered and made to fail: BackscatterMiddleware raises
NotImplementedError for it (different payload-source shape, see that
module's docstring), so there's nothing useful to select yet.

Payload length is no longer fixed at 5 bits -- BackscatterMiddleware
derives it from whatever you type, as long as both tags' payloads are
the same length. Typing mismatched lengths raises a clear assertion
error on Start rather than silently producing a garbled decode, same
guarantee as before.

SI Isolation's default changes with the modulation, not just cosmetics:
OOK's magnitude/envelope detector has essentially zero tolerance for
leftover self-interference cancellation residual (validated this
project: 1-25dB all identically broken, 100dB works), while the
phase-based schemes tolerate the same residual fine around 25dB.
Switching the dropdown resets the field to that modulation's validated
default; you can still type over it before Start.

Miller adds one more basic field (Miller order M: 2/4/8) that doesn't
exist for the other modulations -- shown only when Miller is selected,
and passed through via BackscatterMiddleware's extra_tag_overrides.

Run from the project root, venv active:
    python3 ui/main_window.py
"""
import sys

sys.path.insert(0, "blocks")
sys.path.insert(0, ".")

from PyQt6 import QtWidgets, QtCore

from core.modulation_middleware import BackscatterMiddleware, SI_ISOLATION_DEFAULTS
import core.registry as registry

# Analog needs a different payload-source shape (Signal_In/float, not
# Data_In/byte) that BackscatterMiddleware doesn't build yet -- see its
# module docstring. Left out of the dropdown rather than offered and
# made to raise NotImplementedError on Start.
MODULATIONS = [m for m in registry.MODULATION_MAP if m != "Analog"]

# SI_ISOLATION_DEFAULTS now lives in core.modulation_middleware (see its
# own comment there for why: OOK's envelope detector needs the leakage
# residual driven far lower than the phase-based schemes require, because
# it has no way to absorb any leftover the way a linear detector + DC
# blocker combo can) -- imported here instead of kept as a second copy,
# so build_fixed_topology() and this UI can't silently drift apart on
# what "the right default for OOK" means.


def parse_payload(text):
    """'1,0,1,1,0' -> [1,0,1,1,0]"""
    return [int(b.strip()) for b in text.split(",") if b.strip() != ""]


class TagFields(QtWidgets.QGroupBox):
    """One tag's setup fields: slot ID + payload bits."""

    def __init__(self, title, default_slot_id, default_payload):
        super().__init__(title)
        form = QtWidgets.QFormLayout(self)

        self.slot_id = QtWidgets.QSpinBox()
        self.slot_id.setRange(0, 63)
        self.slot_id.setValue(default_slot_id)
        form.addRow("Slot ID", self.slot_id)

        self.payload = QtWidgets.QLineEdit(default_payload)
        form.addRow("Payload (bits, comma-separated)", self.payload)

    def to_spec(self):
        return {"slot_id": self.slot_id.value(), "payload": parse_payload(self.payload.text())}

    def set_enabled(self, enabled):
        self.slot_id.setEnabled(enabled)
        self.payload.setEnabled(enabled)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Backscatter Simulator")
        self.mw = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)

        setup_row = QtWidgets.QHBoxLayout()

        # --- Modulation choice: only editable before Start ---
        self.mod_group = QtWidgets.QGroupBox("Modulation (setup)")
        mod_form = QtWidgets.QFormLayout(self.mod_group)
        self.modulation = QtWidgets.QComboBox()
        self.modulation.addItems(MODULATIONS)
        self.modulation.currentTextChanged.connect(self.on_modulation_changed)
        mod_form.addRow("Scheme", self.modulation)

        self.miller_m_label = QtWidgets.QLabel("Miller Order (M)")
        self.miller_m = QtWidgets.QComboBox()
        self.miller_m.addItems(["2", "4", "8"])
        mod_form.addRow(self.miller_m_label, self.miller_m)
        setup_row.addWidget(self.mod_group)

        # --- Channel/RF setup: only editable before Start ---
        self.rf_group = QtWidgets.QGroupBox("Channel / RF (setup)")
        rf_form = QtWidgets.QFormLayout(self.rf_group)
        self.distance_setup = QtWidgets.QDoubleSpinBox()
        self.distance_setup.setRange(0.05, 100.0)
        self.distance_setup.setValue(2.0)
        rf_form.addRow("Distance (m) — return path", self.distance_setup)
        self.si_isolation = QtWidgets.QDoubleSpinBox()
        self.si_isolation.setRange(0.0, 150.0)
        self.si_isolation.setValue(SI_ISOLATION_DEFAULTS[MODULATIONS[0]])
        rf_form.addRow("SI Isolation (dB)", self.si_isolation)
        setup_row.addWidget(self.rf_group)

        self.tag0_fields = TagFields("Tag 0 (setup)", 0, "1,0,1,1,0")
        self.tag1_fields = TagFields("Tag 1 (setup)", 1, "1,1,0,0,1")
        setup_row.addWidget(self.tag0_fields)
        setup_row.addWidget(self.tag1_fields)

        outer.addLayout(setup_row)

        # --- Live panel: only enabled WHILE running ---
        self.live_group = QtWidgets.QGroupBox("Live controls (real-time)")
        live_form = QtWidgets.QFormLayout(self.live_group)
        self.distance_live = QtWidgets.QDoubleSpinBox()
        self.distance_live.setRange(0.05, 100.0)
        self.distance_live.setValue(2.0)
        self.distance_live.setEnabled(False)
        self.distance_live.valueChanged.connect(self.on_live_distance_changed)
        live_form.addRow("Distance (m) — return, live", self.distance_live)
        outer.addWidget(self.live_group)

        # --- Start/Stop ---
        button_row = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton("Start")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setEnabled(False)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        outer.addLayout(button_row)

        # --- Packet table ---
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Timestamp", "Bits", "Hex", "ASCII"])
        self.table.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.table)

        self.start_button.clicked.connect(self.on_start)
        self.stop_button.clicked.connect(self.on_stop)

        self.timer = QtCore.QTimer()
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.poll_packets)

        self.on_modulation_changed(self.modulation.currentText())

    def on_modulation_changed(self, modulation):
        is_miller = modulation == "Miller"
        self.miller_m_label.setVisible(is_miller)
        self.miller_m.setVisible(is_miller)
        self.si_isolation.setValue(SI_ISOLATION_DEFAULTS.get(modulation, 25.0))

    def _set_setup_enabled(self, enabled):
        self.mod_group.setEnabled(enabled)
        self.rf_group.setEnabled(enabled)
        self.tag0_fields.set_enabled(enabled)
        self.tag1_fields.set_enabled(enabled)

    def on_start(self):
        modulation = self.modulation.currentText()
        tag_specs = [self.tag0_fields.to_spec(), self.tag1_fields.to_spec()]
        distance = self.distance_setup.value()
        si_isolation_db = self.si_isolation.value()

        extra_tag_overrides = {}
        if modulation == "Miller":
            extra_tag_overrides["m"] = int(self.miller_m.currentText())

        try:
            self.mw = BackscatterMiddleware(
                modulation, tag_specs, distance=distance,
                si_isolation_db=si_isolation_db,
                extra_tag_overrides=extra_tag_overrides,
            )
        except (AssertionError, ValueError, NotImplementedError) as e:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(e))
            return

        self.mw.start()

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._set_setup_enabled(False)

        # sync the live control to whatever setup used, then enable it
        self.distance_live.blockSignals(True)
        self.distance_live.setValue(distance)
        self.distance_live.blockSignals(False)
        self.distance_live.setEnabled(True)

        self.timer.start()

    def on_stop(self):
        self.timer.stop()
        if self.mw is not None:
            self.mw.stop()
            self.mw = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._set_setup_enabled(True)
        self.distance_live.setEnabled(False)

    def on_live_distance_changed(self, value):
        if self.mw is not None:
            self.mw.set_distance(value)

    def poll_packets(self):
        if self.mw is None:
            return
        for pkt in self.mw.get_packets():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"{pkt['timestamp']:.3f}"))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(pkt['bits'])))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(pkt['hex']))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(pkt['ascii']))
            self.table.scrollToBottom()

    def closeEvent(self, event):
        self.on_stop()
        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.resize(900, 550)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
