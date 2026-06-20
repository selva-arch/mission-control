"""
Render solar panels onto a roof image.

MVP: composite a panel texture onto the roof, skewed toward the dominant roof
segment's azimuth so it reads as believable rather than pasted-on. Upgrade path:
AI inpainting for photorealism (`render.mode: ai`).

This is a deliberately simple, dependency-light placeholder so the end-to-end
chain runs today; swap in a better renderer without touching the pipeline.
"""

from __future__ import annotations

import io

from PIL import Image


def render_with_solar(tile_png: bytes, segments: list | None, cfg: dict) -> bytes:
    mode = cfg.get("render", {}).get("mode", "composite")
    if mode == "ai":
        return _render_ai(tile_png, segments, cfg)
    return _render_composite(tile_png, segments, cfg)


def _render_composite(tile_png: bytes, segments: list | None, cfg: dict) -> bytes:
    base = Image.open(io.BytesIO(tile_png)).convert("RGBA")
    w, h = base.size

    # Panel array sized to a central portion of the roof.
    pw, ph = int(w * 0.42), int(h * 0.30)
    panels = _panel_grid(pw, ph, cols=6, rows=4)

    # Skew toward the dominant roof segment azimuth for a believable angle.
    if segments:
        az = (segments[0] or {}).get("azimuth") or 0
        skew = max(-0.25, min(0.25, ((az % 180) - 90) / 360))
        panels = panels.transform(
            (pw, ph), Image.AFFINE, (1, skew, 0, 0, 1, 0), resample=Image.BICUBIC
        )

    base.alpha_composite(panels, (int(w * 0.29), int(h * 0.34)))
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _panel_grid(w: int, h: int, cols: int, rows: int) -> Image.Image:
    """A dark-blue panel grid with thin gaps — stand-in for a real panel texture."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    gx, gy = w / cols, h / rows
    for c in range(cols):
        for r in range(rows):
            x0, y0 = c * gx + 2, r * gy + 2
            x1, y1 = (c + 1) * gx - 2, (r + 1) * gy - 2
            d.rectangle([x0, y0, x1, y1], fill=(18, 28, 64, 235),
                        outline=(120, 140, 190, 255), width=1)
    return img


def _render_ai(tile_png: bytes, segments, cfg: dict) -> bytes:
    raise NotImplementedError(
        "Wire to an image-edit/inpaint API: prompt 'add realistic rooftop solar "
        "panels to this roof', return edited JPEG bytes."
    )
