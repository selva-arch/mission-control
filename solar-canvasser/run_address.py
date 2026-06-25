#!/usr/bin/env python3
"""
Single-address brochure on REAL Nearmap imagery — no AI entitlement needed.

For when you're testing access: type an address, get the actual Nearmap roof
image, eyeball whether it has solar yourself, and produce a brochure (real image
+ rendered panels + indicative savings + the imagery capture date).

  python run_address.py --address "12 Stuart Rd, Dulwich SA 5065"
  python run_address.py --address "..." --kw 8         # set system size
  python run_address.py --address "..." --roof-area 160 # estimate size from roof m^2

Uses free OpenStreetMap geocoding + Nearmap Tile/Coverage APIs (both working on
your account). The Nearmap AI auto-detection (run_area_scan.py) lights up once
the AI Feature API is enabled — this path needs only imagery.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

from pipeline import addresses, render, savings
from pipeline.flyer import render_flyer
from pipeline.imagery import build_source
from pipeline.nearmap import NearmapClient

HERE = Path(__file__).parent


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--kw", type=float, default=6.6, help="system size kW (default 6.6)")
    ap.add_argument("--roof-area", type=float, help="roof m^2 -> estimate kW (overrides --kw)")
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    cfg.setdefault("imagery", {})["source"] = "nearmap"   # force Nearmap imagery
    out = Path(cfg.get("output_dir", "out"))
    name = slug(args.address)

    # 1. Address -> lat/lon (free OSM geocoding)
    lat, lon = addresses.geocode(args.address)
    print(f"[geocode] {args.address} -> {lat:.6f},{lon:.6f}")

    # 2. Data point: imagery capture date from Coverage API
    try:
        cov = NearmapClient(cfg["nearmap"]["api_key"]).coverage(lat, lon)
        surveys = cov.get("surveys", [])
        capture = surveys[0].get("captureDate", "?") if surveys else "?"
        print(f"[coverage] latest Nearmap capture: {capture} ({len(surveys)} surveys)")
    except Exception as e:  # noqa: BLE001
        capture = "?"
        print(f"[coverage] (skipped: {e})")

    # 3. Real Nearmap roof image
    tile = build_source(cfg).tile(lat, lon)
    (out / "tiles").mkdir(parents=True, exist_ok=True)
    raw_path = out / "tiles" / f"{name}.jpg"
    raw_path.write_bytes(tile)
    print(f"[imagery] real roof image -> {raw_path}")
    print("          ^ OPEN THIS and check: does the roof already have solar panels?")

    # 4. Size the system (manual / heuristic — AI/Solar API can refine later)
    if args.roof_area:
        # indicative: usable ~30% of roof, ~0.20 kW/m^2 packed
        system_kw = round(args.roof_area * 0.30 * 0.20, 1)
        print(f"[size] from {args.roof_area} m^2 roof -> ~{system_kw} kW (indicative)")
    else:
        system_kw = args.kw
        print(f"[size] {system_kw} kW (default; pass --kw or --roof-area to change)")

    # 5. Render panels on the real roof + estimate savings
    rendered = render.render_with_solar(tile, None, cfg)
    a = savings.SolarAssumptions(**{k: v for k, v in cfg.get("tariff", {}).items()
                                    if k in savings.SolarAssumptions.__annotations__})
    s = savings.annual_saving(system_kw, a)
    print(f"[savings] ~${s['annual_saving_aud']}/yr ({s['annual_generation_kwh']} kWh/yr)")

    # 6. Brochure
    res = render_flyer(
        {"address": args.address, "system_kw": s["system_kw"],
         "annual_kwh": s["annual_generation_kwh"], "annual_saving": s["annual_saving_aud"]},
        rendered, cfg, str(out / "flyers" / f"{name}.html"))
    print(f"[flyer] html: {res['html']}")
    print(f"[flyer] pdf:  {res.get('pdf') or '(install weasyprint for PDF)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
