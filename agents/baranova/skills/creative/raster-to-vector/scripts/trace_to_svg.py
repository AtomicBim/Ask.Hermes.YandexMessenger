#!/usr/bin/env python3
"""Raster -> SVG auto-trace. Pillow + numpy + potrace (tatarize/potrace port).
Usage: edit SRC / OUTSVG / OUTPNG below, then:
  P=$(ls -d /opt/hermes/tools/python-3.14.9*/bin/python | head -1)
  "$P" -m pip install --quiet numpy potracer Pillow pymupdf
  "$P" trace_to_svg.py
"""
import numpy as np, potrace
from PIL import Image, ImageFilter

SRC = "input.png"
OUTSVG = "out.svg"
OUTPNG = "preview.png"   # transparent-bg preview (mask); render SVG separately to verify

SE = 11        # top-hat structuring element size: slightly > line thickness
THRESH = 7     # detail > THRESH = ink
MEDIAN = 5     # median filter size to drop pores/hair specks
TURD = 22      # potrace turdsize (speckle suppression; also erodes tiny detail)
LEFT_G = 45    # left glare zone width to blank (measure column profile first)
RIGHT_G = 3    # right edge blank width


def build_svg(path, w, h):
    d = []
    for curve in path.curves:
        sp = curve.start_point
        parts = ["M%g,%g" % (sp.x, sp.y)]
        for seg in curve.segments:
            e = seg.end_point
            if seg.is_corner:
                parts.append("L%g,%g L%g,%g" % (seg.c.x, seg.c.y, e.x, e.y))
            else:
                parts.append("C%g,%g %g,%g %g,%g"
                             % (seg.c1.x, seg.c1.y, seg.c2.x, seg.c2.y, e.x, e.y))
        parts.append("Z")
        d.append(" ".join(parts))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d">\n<path d="%s" fill="#000" '
            'fill-rule="evenodd" stroke="none"/>\n</svg>\n'
            % (w, h, w, h, " ".join(d)))


def main():
    g = np.asarray(Image.open(SRC).convert("L")).astype(np.float32)
    gi = Image.fromarray(g.astype(np.uint8))
    # white top-hat: opening (erode then dilate) then subtract
    opened = gi.filter(ImageFilter.MinFilter(SE)).filter(ImageFilter.MaxFilter(SE))
    detail = g - np.asarray(opened).astype(np.float32)
    detail[:, :LEFT_G] = -1e3
    detail[:, -RIGHT_G:] = -1e3
    if MEDIAN:
        detail = np.asarray(Image.fromarray(
            np.clip(detail + 128, 0, 255).astype(np.uint8)).filter(
            ImageFilter.MedianFilter(MEDIAN))).astype(np.float32) - 128
    mask = detail > THRESH
    # potrace inverts internally: pass ~mask as bool, never float+blacklevel.
    path = potrace.Bitmap(np.asarray(~mask, dtype=bool)).trace(turdsize=TURD)
    h, w = mask.shape
    open(OUTSVG, "w").write(build_svg(path, w, h))
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[mask, 3] = 255
    Image.fromarray(rgba, "RGBA").save(OUTPNG)
    print("wrote", OUTSVG, OUTPNG)


def verify():
    """Render the actual SVG to confirm it is correct (mask preview is not enough)."""
    import pymupdf
    pymupdf.open(OUTSVG)[0].get_pixmap(dpi=150).save("render_check.png")
    print("wrote render_check.png")


if __name__ == "__main__":
    main()
    verify()
