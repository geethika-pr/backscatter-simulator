"""
30-second check of the ONE assumption core/ber_theory.py makes about
GNU Radio: that channels.channel_model(noise_voltage=v) adds complex noise
whose TOTAL power is v**2, split evenly between I and Q (v**2/2 each).

Needs your GNU Radio venv. From the project root:
    python3 Test/check_noise_convention.py

Expected output: total ~= 1.00, I ~= 0.50, Q ~= 0.50 (ratios of v**2).
If total comes out ~2.0 instead, tell Claude -- the BER plot's SNR axis
would then need a -3 dB correction.
"""
import numpy as np
from gnuradio import blocks, channels, gr

V = 0.1
N = 400_000

tb = gr.top_block()
src = blocks.null_source(gr.sizeof_gr_complex)
head = blocks.head(gr.sizeof_gr_complex, N)
ch = channels.channel_model(noise_voltage=V, frequency_offset=0.0, epsilon=1.0,
                            taps=[1.0], noise_seed=0, block_tags=False)
sink = blocks.vector_sink_c()
tb.connect(src, head, ch, sink)
tb.run()

x = np.array(sink.data())
v2 = V * V
print(f"samples: {len(x)}")
print(f"total noise power / v^2 = {np.mean(np.abs(x) ** 2) / v2:.3f}   (expect ~1.00)")
print(f"I power / v^2           = {np.var(x.real) / v2:.3f}   (expect ~0.50)")
print(f"Q power / v^2           = {np.var(x.imag) / v2:.3f}   (expect ~0.50)")
