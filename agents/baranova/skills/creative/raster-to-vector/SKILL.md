---
name: raster-to-vector
description: Use when tracing a raster image to vector SVG offline.
version: 1.0.0
author: hermes
license: MIT
metadata:
  hermes:
    tags: [image, vector, svg, potrace, trace]
    related_skills: []
---

# Raster → Vector (auto-trace to SVG)

## When to Use
When the user asks to vectorize, trace, or convert a raster image (logo, tattoo, line art, drawing, photo) into vector/SVG for reuse in Illustrator, Figma, web, docs, or cutting/engraving.

Translate a bitmap to vector curves entirely offline (no online service) using Pillow + numpy + the pure-Python `potrace` port. Produces an `.svg` with Bézier paths, plus a PNG preview.

## Triage first (honest, before any work)
Tell the user the expected quality class up front — it determines whether auto-trace is even the right tool:

| Source | Auto-trace result |
|---|---|
| Logo, schematic, ЧБ drawing, silhouette, clean line art | ✅ Good, ready to use |
| Fine-line art on textured/soft background (e.g. a tattoo photo) | ⚠️ Stencil / line-art quality, rough edges, noise remnants |
| Photograph with gradient shading | ❌ Posterized mess; auto-trace is wrong. Offer a manual redraw instead. |

Always deliver both the `.svg` and a rendered PNG preview, and state the residual quality limits plainly ("трафарет, не гладкий обвод").

## Environment (captured as FIX, not as failure)
The `execute_code` sandbox interpreter lacks numpy/Pillow; the tools python has them. Use:

```bash
P=$(ls -d /opt/hermes/tools/python-3.14.9*/bin/python | head -1)
"$P" -m pip install --quiet numpy potracer Pillow pymupdf
"$P" script.py
```

`potracer` on PyPI ships the module under the import name `potrace` — write `import potrace`, not `import potracer`.

## Pipeline (known-good)
See `scripts/trace_to_svg.py` for the full runnable version. The steps in order:

1. **Grayscale**, keep as float32.
2. **Isolate thin light lines with a morphological white top-hat**, NOT Gaussian-blur background subtraction:
   ```python
   opened = gi.filter(ImageFilter.MinFilter(se)).filter(ImageFilter.MaxFilter(se))
   detail = g - np.asarray(opened).astype(np.float32)
   ```
   `se` slightly larger than line thickness (~9–11 px). Top-hat extracts thin bright lines and strips *broad* bright regions (glare) regardless of how sharp their gradient is.
3. **Suppress edge-glare columns** by measuring the column brightness profile first, then blanking the whole glare zone — do not guess a fixed offset:
   ```python
   detail[:, :glare_width] = -1e3
   ```
4. Median filter (size ~5) on `detail` to kill pores/hair specks.
5. Threshold `detail > thresh` → boolean mask.
6. **Trace** — pass `~mask` as a bool array:
   ```python
   path = potrace.Bitmap(np.asarray(~mask, dtype=bool)).trace(turdsize=22)
   ```
7. Walk `path.curves` → `curve.segments` to emit `M/L/C/Z` path data; wrap in an `<svg>` with `fill-rule="evenodd"`.
8. **Verify by rendering the actual SVG**, not the mask.

## Pitfalls
- `potrace.Bitmap` **inverts the array internally** (`self.invert()` in `__init__`). Passing a float array with `blacklevel=` thresholds it at `255*blacklevel`, which zeroes the whole mask → the trace becomes a whole-canvas rectangle. Always pass `~mask` as `dtype=bool` with no `blacklevel` argument.
- **The threshold-mask preview PNG is NOT the SVG.** A correct mask still yields a broken SVG if the Bitmap convention is wrong. Render the generated SVG with `pymupdf` (`fitz.open(svg)[0].get_pixmap(...)`) and look at it before declaring success — do not trust pixel counts or the intermediate mask.
- Gaussian-blur background subtraction (`g - GaussianBlur(g)`) leaves thin vertical artifact lines at the sharp edges of a broad glare (the cylindrical-limb highlight). Use the top-hat; suppress the glare zone by width.
- `turdsize` removes speckles but also erodes genuinely small real detail (fine hachure, dots). Balance it against the source's fine features instead of maxing it out.

## Deliverable
Name files per convention `Логичное_название_ДД.ММ.ГГГГ_h-.svg` (+ matching `_h-.png` preview). Send the SVG plus a white-background render PNG (transparent PNG can render unreadable in dark-mode viewers).
