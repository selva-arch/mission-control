#!/usr/bin/env python3
"""
Scan a suburb with Nearmap AI and work out which houses do / don't have solar.

  python run_area_scan.py --suburb "Dulwich, SA" [--limit 50]

For each address it queries the Nearmap AI Feature API for existing PV panels
and roof area, then writes out/solar-scan.csv:
    address, lat, lon, has_solar, panel_count, pv_area_sqm, roof_area_sqm,
    confidence, survey_date, error
and prints a summary (how many are no-solar TARGETS).

This is the targeting pass — the no-solar rows feed the flyer batch. For a whole
suburb at scale, Nearmap recommends a bulk "AI Offline" export instead of
thousands of real-time calls (see ARCHITECTURE.md); this real-time path is ideal
for validation and for limited/incremental runs.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from pipeline import addresses
from pipeline.nearmap import NearmapClient

HERE = Path(__file__).parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suburb")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    suburb = args.suburb or cfg["target_suburb"]
    out = Path(cfg.get("output_dir", "out"))
    nm = cfg["nearmap"]

    client = NearmapClient(
        nm["api_key"], packs=nm.get("packs", "solar,roof_char"),
        ai_path=nm.get("ai_path", "/ai/features/v4/features.json"),
        min_confidence=cfg.get("detect", {}).get("min_confidence", 0.5),
        aoi_size_m=nm.get("aoi_size_m", 25))

    addrs = addresses.fetch_addresses(suburb, limit=args.limit)
    print(f"[addresses] {len(addrs)} in {suburb}")
    if not addrs:
        print("  (none — check the suburb name / OSM coverage, or supply addresses)")
        return 1

    rows = []

    def scan(ad):
        res = client.solar(ad.lat, ad.lon)
        return {
            "address": ad.full, "lat": ad.lat, "lon": ad.lon,
            "has_solar": res.has_solar, "panel_count": res.panel_count,
            "pv_area_sqm": res.pv_area_sqm, "roof_area_sqm": res.roof_area_sqm,
            "confidence": res.confidence, "survey_date": res.survey_date,
            "error": res.error,
        }

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(scan, ad): ad for ad in addrs}
        for f in as_completed(futs):
            rows.append(f.result())
            done += 1
            if done % 25 == 0 or done == len(addrs):
                print(f"  scanned {done}/{len(addrs)}")

    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "solar-scan.csv"
    cols = ["address", "lat", "lon", "has_solar", "panel_count", "pv_area_sqm",
            "roof_area_sqm", "confidence", "survey_date", "error"]
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if not r["error"]]
    has = [r for r in ok if r["has_solar"]]
    targets = [r for r in ok if not r["has_solar"]]
    errs = [r for r in rows if r["error"]]
    print(f"\n[summary] {len(rows)} scanned")
    print(f"  already have solar : {len(has)}")
    print(f"  NO solar (targets) : {len(targets)}")
    print(f"  errors / no cover  : {len(errs)}")
    if ok:
        print(f"  solar penetration  : {len(has)/len(ok)*100:.0f}%")
    print(f"[write] {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
