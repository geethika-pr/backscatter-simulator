"""
Regression check for "Tag Loss (dB)" (calibration-plan item 5): a UI-side
tag setting that scales Gamma0/Gamma1/structural scattering by
10**(-loss/20) before they reach the GNU Radio block (scene_to_topology)
and wherever the rings/BER plot read tag gammas. Not a registry Param, so
the block never receives an unknown argument.

Needs the project venv (gnuradio + PyQt6), like the other ui/test_*.py.
From the project root:
    python3 ui/test_tag_loss.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import core.ber_theory as bt  # noqa: E402
import ui.canvas_window as cw  # noqa: E402


class _FakeCanvas:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _Edge:
    def __init__(self, role, a, b, cp):
        self.role, self.from_node, self.to_node, self.channel_params = role, a, b, cp


class _Lookup:
    _resolve_link_params_for_tag = cw.CanvasWindow._resolve_link_params_for_tag
    _resolve_decode_snr_db_for_tag = cw.CanvasWindow._resolve_decode_snr_db_for_tag
    _resolve_decision_snr_for_tag = cw.CanvasWindow._resolve_decision_snr_for_tag


def main():
    # 1. helper scales magnitudes + structural scattering only, never mutates input
    p = {"gamma0_mag": 0.8, "gamma0_phase_deg": 0.0, "gamma1_mag": 0.8, "gamma1_phase_deg": 180.0,
         "as_real": 0.05, "as_imag": 0.02, "p_th": 0.5, "bit_rate": 10000}
    q = cw._apply_tag_loss(p, 6.0)
    f = 10 ** (-6 / 20)
    for k in ("gamma0_mag", "gamma1_mag", "as_real", "as_imag"):
        assert abs(q[k] - p[k] * f) < 1e-12, k
    for k in ("gamma0_phase_deg", "gamma1_phase_deg", "p_th", "bit_rate"):
        assert q[k] == p[k], k
    assert p["gamma0_mag"] == 0.8
    assert cw._apply_tag_loss(p, 0.0) == p and cw._apply_tag_loss(p, None) == p

    # 2. modulation shape kept: BPSK still equal and opposite
    g0 = bt.gamma_from_polar(q["gamma0_mag"], q["gamma0_phase_deg"])
    g1 = bt.gamma_from_polar(q["gamma1_mag"], q["gamma1_phase_deg"])
    assert abs(abs(g0) - abs(g1)) < 1e-12 and abs(g0 + g1) < 1e-12

    # 3. real NodeItems: tags default 0.0, everything else None
    tag = cw.NodeItem("tag1", "tag_bpsk", _FakeCanvas(), QtCore.QPointF(0, 0))
    snd = cw.NodeItem("sender1", "sender", _FakeCanvas(), QtCore.QPointF(0, 0))
    rx = cw.NodeItem("rx1", "rx_bpsk", _FakeCanvas(), QtCore.QPointF(0, 0))
    assert tag.tag_loss_db == 0.0 and snd.tag_loss_db is None

    # 4. ring SNR and BER-plot SNR both drop by exactly the loss
    ch = lambda: {"distance": 1.5, "freq": 915e6, "g_reader_dbi": 6.0, "g_tag_dbi": 2.0,  # noqa: E731
                  "noise_volt_c1": 0.01, "path_loss_exp": 2.3}
    lk = _Lookup()
    lk.edges = [_Edge("forward", snd, tag, ch()), _Edge("return", tag, rx, ch())]
    ring0 = lk._resolve_decode_snr_db_for_tag(tag, rx)
    e0, _ = lk._resolve_decision_snr_for_tag(tag, rx)
    tag.tag_loss_db = 7.5
    ring1 = lk._resolve_decode_snr_db_for_tag(tag, rx)
    e1, _ = lk._resolve_decision_snr_for_tag(tag, rx)
    assert abs((ring0 - ring1) - 7.5) < 1e-9 and abs((e0 - e1) - 7.5) < 1e-9

    print("test_tag_loss: all checks passed")


if __name__ == "__main__":
    main()
