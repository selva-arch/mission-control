#!/usr/bin/env python3
"""
Validate the whole chain on ONE address before spending postage on a suburb.

  python run_one.py --address "1 Rundle St, Dulwich SA 5065"

Runs: geocode -> imagery -> (detect) -> solar sizing -> render -> savings -> flyer.
Detection is skipped here by default (the point is to see a flyer); use
run_suburb.py for the real detect-and-filter batch.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import requests
import yaml

from pipeline import imagery, render, savings, solar
from pipeline.flyer import render_flyer

HERE = Path(__file__).parent


def load_cfg(path):
    return yaml.safe_load(Path(path).read_text())


def geocode(address: str, key: str):
    r = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                     params={"address": address, "key": key}, timeout=30)
    r.raise_for_status()
    res = r.json().get("results")
    if not res:
        raise SystemExit(f"Could not geocode: {address}")
    loc = res[0]["geometry"]["location"]
    return loc["lat"], loc["lng"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    out = Path(cfg.get("output_dir", "out"))

    lat, lon = geocode(args.address, cfg["google_api_key"])
    print(f"[geocode] {args.address} -> {lat:.6f},{lon:.6f}")

    tile = imagery.build_source(cfg).tile(lat, lon)
    (out / "tiles").mkdir(parents=True, exist_ok=True)
    (out / "tiles" / "one.jpg").write_bytes(tile)
    print(f"[imagery] tile {len(tile)} bytes")

    ins = solar.GoogleSolarAPI(cfg["google_api_key"]).insight(lat, lon)
    if ins:
        print(f"[solar] roof {ins.roof_area_m2} m^2, "
              f"{ins.max_system_kw} kW, {ins.annual_kwh} kWh/yr")
        system_kw, annual_kwh, segs = ins.max_system_kw, ins.annual_kwh, ins.segments
    else:
        print("[solar] no Solar API coverage — falling back to a default 6.6 kW")
        system_kw, annual_kwh, segs = 6.6, None, None

    rendered = render.render_with_solar(tile, segs, cfg)
    a = savings.SolarAssumptions(**{k: v for k, v in cfg.get("tariff", {}).items()
                                    if k in savings.SolarAssumptions.__annotations__})
    s = savings.annual_saving(system_kw, a, annual_kwh)
    print(f"[savings] ~${s['annual_saving_aud']}/yr "
          f"({s['annual_generation_kwh']} kWh/yr)")

    flyer_path = render_flyer(
        {"address": args.address, "system_kw": s["system_kw"],
         "annual_kwh": s["annual_generation_kwh"], "annual_saving": s["annual_saving_aud"]},
        rendered, cfg, str(out / "flyers" / "one.html"))
    print(f"[flyer] {flyer_path}  (open it, or convert to PDF)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
