Drop node icons in here. Each file name must exactly match a node's
type_name:

    sender.png
    tag_ook.png
    tag_bpsk.png
    tag_fm0.png
    tag_miller.png
    rx_ook.png
    rx_bpsk.png
    rx_fm0.png
    rx_miller.png

One exception: the combined "reader" block (sender + rx shown/wired as
one monostatic unit -- see CanvasWindow.add_reader()) is, under the
hood, an ordinary 'sender' NodeItem -- but it uses its OWN icon file,
separate from a plain standalone sender's:

    reader.png

Drop a reader.png in here to give the combined-reader block a distinct
look from a plain sender node. If reader.png is missing, a reader falls
back to sender.png (or the plain colored rectangle, if that's missing
too) -- same "nothing breaks if you don't add one" rule as every other
icon here.

Notes:
- PNG, transparent background. The shape (circle, rounded square,
  whatever) must already be baked into the image via its own
  transparency -- the code draws the pixmap as-is, it does not mask or
  crop it into a shape.
- Roughly square source images work best; a few hundred px per side is
  plenty (it gets scaled down to fit the node).
- A type with no matching file here just keeps the plain colored
  rectangle it always had -- nothing breaks, you can add icons for some
  types and not others.
- No restart needed to pick up new files on the NEXT node you drop onto
  the canvas, but a node already on the canvas needs to be re-added
  (or the canvas reloaded) to pick up an icon added after it was placed.
