"""
Per-house printable flyer: rendered "with solar" image + indicative savings + CTA.

Produces self-contained HTML (image embedded as base64) in either an A4 leaflet
or a DL postcard (99x210mm) layout, then converts to print-ready PDF via
WeasyPrint or headless Chrome (whichever is installed).
"""

from __future__ import annotations

import base64
from pathlib import Path

from jinja2 import Template

# Page geometry per format.
PAGES = {
    "A4": {"size": "A4", "img_h": "300px", "stat_dir": "row", "h1": "26px"},
    "DL": {"size": "99mm 210mm", "img_h": "150px", "stat_dir": "column", "h1": "20px"},
}

TEMPLATE = Template(r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  @page { size: {{ page_size }}; margin: {{ margin }}; }
  * { box-sizing: border-box; }
  body { font: 13px/1.45 system-ui, sans-serif; color: #14233b; margin: 0; }
  .brand { font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: #1a7f37; }
  h1 { font-size: {{ h1 }}; margin: 5px 0 2px; line-height: 1.1; }
  .addr { color: #57606a; margin-bottom: 12px; font-size: 12px; }
  img.roof { width: 100%; height: {{ img_h }}; object-fit: cover; border-radius: 10px; border: 1px solid #d0d7de; }
  .cap { font-size: 10px; color: #8a93a0; margin-top: 4px; }
  .stats { display: flex; flex-direction: {{ stat_dir }}; gap: 10px; margin: 14px 0; }
  .stat { flex: 1; background: #f6f8fa; border-radius: 10px; padding: 10px 12px; }
  .stat .n { font-size: 20px; font-weight: 700; }
  .stat .l { font-size: 11px; color: #57606a; }
  .cta { background: #1a7f37; color: #fff; border-radius: 10px; padding: 12px 16px;
         display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .cta b { font-size: 14px; } .cta a { color: #fff; font-weight: 700; text-decoration: none; }
  .fine { font-size: 9px; color: #8a93a0; margin-top: 10px; }
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
  <div class="cta"><b>{{ cta }}</b><a href="{{ booking_url }}">{{ phone }}</a></div>
  <div class="fine">Estimates only, based on aerial roof analysis and typical performance;
  actual figures depend on a site assessment, your usage and tariff. {{ brand }}.</div>
</body></html>""")


def render_flyer(ctx: dict, image_jpeg: bytes, cfg: dict, out_path: str,
                 to_pdf_too: bool = True) -> dict:
    flyer_cfg = cfg.get("flyer", {})
    fmt = flyer_cfg.get("format", "A4").upper()
    page = PAGES.get(fmt, PAGES["A4"])

    html = TEMPLATE.render(
        page_size=page["size"], margin="14mm" if fmt == "A4" else "8mm",
        img_h=page["img_h"], stat_dir=page["stat_dir"], h1=page["h1"],
        brand=flyer_cfg.get("brand", "Your Solar Co"),
        salutation=cfg.get("mail", {}).get("salutation", "To the Homeowner"),
        address=ctx["address"],
        image_b64=base64.b64encode(image_jpeg).decode(),
        system_kw=ctx.get("system_kw", "?"), annual_kwh=ctx.get("annual_kwh", "?"),
        annual_saving=ctx.get("annual_saving", "?"),
        cta=flyer_cfg.get("cta", "Book a free assessment"),
        booking_url=flyer_cfg.get("booking_url", "#"), phone=flyer_cfg.get("phone", ""),
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    result = {"html": str(out), "pdf": None}
    if to_pdf_too:
        try:
            result["pdf"] = to_pdf(str(out))
        except RuntimeError as e:
            result["pdf_error"] = str(e)
    return result


def to_pdf(html_path: str, pdf_path: str | None = None) -> str:
    """Convert a flyer HTML to print-ready PDF. Tries WeasyPrint, then headless
    Chrome (Playwright). Raises with install hints if neither is available."""
    pdf_path = pdf_path or html_path.rsplit(".", 1)[0] + ".pdf"

    try:
        from weasyprint import HTML
        HTML(html_path).write_pdf(pdf_path)
        return pdf_path
    except ImportError:
        pass
    except Exception:
        pass  # weasyprint present but missing system libs — fall through

    try:
        from playwright.sync_api import sync_playwright
        uri = Path(html_path).resolve().as_uri()
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.goto(uri)
            pg.pdf(path=pdf_path, prefer_css_page_size=True, print_background=True)
            b.close()
        return pdf_path
    except Exception:
        pass

    raise RuntimeError(
        "No PDF backend. Install one:  brew install weasyprint   "
        "(or  pip install playwright && playwright install chromium)"
    )
