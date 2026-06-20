"""
Per-house printable flyer: rendered "with solar" image + indicative savings + CTA.

Produces self-contained HTML (image embedded as base64). Convert to PDF with
WeasyPrint or headless Chrome for printing/mailing.
"""

from __future__ import annotations

import base64
from pathlib import Path

from jinja2 import Template

TEMPLATE = Template(r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  @page { size: {{ page }}; margin: 14mm; }
  body { font: 14px/1.5 system-ui, sans-serif; color: #14233b; }
  .brand { font-size: 13px; letter-spacing: .08em; text-transform: uppercase; color: #1a7f37; }
  h1 { font-size: 26px; margin: 6px 0 2px; }
  .addr { color: #57606a; margin-bottom: 14px; }
  img.roof { width: 100%; border-radius: 10px; border: 1px solid #d0d7de; }
  .cap { font-size: 11px; color: #8a93a0; margin-top: 4px; }
  .stats { display: flex; gap: 14px; margin: 18px 0; }
  .stat { flex: 1; background: #f6f8fa; border-radius: 10px; padding: 12px 14px; }
  .stat .n { font-size: 22px; font-weight: 700; }
  .stat .l { font-size: 12px; color: #57606a; }
  .cta { background: #1a7f37; color: #fff; border-radius: 10px; padding: 14px 18px;
         display: flex; justify-content: space-between; align-items: center; }
  .cta a { color: #fff; font-weight: 700; }
  .fine { font-size: 10px; color: #8a93a0; margin-top: 12px; }
</style></head><body>
  <div class="brand">{{ brand }}</div>
  <h1>See your home with solar</h1>
  <div class="addr">{{ salutation }} &middot; {{ address }}</div>
  <img class="roof" src="data:image/jpeg;base64,{{ image_b64 }}">
  <div class="cap">Indicative visualisation of panels on your roof.</div>
  <div class="stats">
    <div class="stat"><div class="n">{{ system_kw }} kW</div><div class="l">indicative system size</div></div>
    <div class="stat"><div class="n">{{ annual_kwh }}</div><div class="l">kWh / year generation</div></div>
    <div class="stat"><div class="n">${{ annual_saving }}</div><div class="l">est. saving / year</div></div>
  </div>
  <div class="cta"><span>{{ cta }}</span><a href="{{ booking_url }}">{{ phone }}</a></div>
  <div class="fine">Estimates only, based on aerial roof analysis and typical performance;
  actual figures depend on a site assessment, your usage and tariff. {{ brand }}.</div>
</body></html>""")


def render_flyer(ctx: dict, image_jpeg: bytes, cfg: dict, out_path: str) -> str:
    flyer_cfg = cfg.get("flyer", {})
    html = TEMPLATE.render(
        page="A4" if flyer_cfg.get("format", "A4") == "A4" else "99mm 210mm",
        brand=flyer_cfg.get("brand", "Your Solar Co"),
        salutation=cfg.get("mail", {}).get("salutation", "To the Homeowner"),
        address=ctx["address"],
        image_b64=base64.b64encode(image_jpeg).decode(),
        system_kw=ctx.get("system_kw", "?"),
        annual_kwh=ctx.get("annual_kwh", "?"),
        annual_saving=ctx.get("annual_saving", "?"),
        cta=flyer_cfg.get("cta", "Book a free assessment"),
        booking_url=flyer_cfg.get("booking_url", "#"),
        phone=flyer_cfg.get("phone", ""),
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html)
    return out_path
