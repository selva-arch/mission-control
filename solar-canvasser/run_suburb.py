#!/usr/bin/env python3
"""
Whole-suburb batch: addresses -> imagery -> detect -> keep no-solar ->
sizing -> render -> savings -> flyer -> mail CSV.

  python run_suburb.py --suburb "Dulwich, SA" [--limit 50]

Detection requires a wired detect backend (see pipeline/detect.py). Roofs
classified no-solar above detect.min_confidence get a flyer; everything else is
skipped or queued for review. Start with --limit to sanity-check before a full run.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from pipeline import addresses, detect, imagery, render, savings, solar
from pipeline.flyer import render_flyer
from pipeline.mail import write_mail_csv

HERE = Path(__file__).parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suburb")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    suburb = args.suburb or cfg["target_suburb"]
    out = Path(cfg.get("output_dir", "out"))
    min_conf = cfg.get("detect", {}).get("min_confidence", 0.7)

    src = imagery.build_source(cfg)
    default_kw = cfg.get("sizing", {}).get("default_kw", 6.6)
    gkey = cfg.get("google_api_key", "")
    sapi = solar.GoogleSolarAPI(gkey) if gkey and gkey != "YOUR_KEY_HERE" else None
    a = savings.SolarAssumptions(**{k: v for k, v in cfg.get("tariff", {}).items()
                                    if k in savings.SolarAssumptions.__annotations__})

    addrs = addresses.fetch_addresses(suburb, limit=args.limit)
    print(f"[addresses] {len(addrs)} in {suburb}")

    mail_rows, queued, skipped = [], 0, 0
    for i, ad in enumerate(addrs, 1):
        try:
            tile = src.tile(ad.lat, ad.lon)
            det = detect.detect(tile, cfg, lat=ad.lat, lon=ad.lon)
            if det.has_panels:
                skipped += 1
                continue
            if det.confidence < min_conf:
                queued += 1   # send to human review instead of mailing
                continue

            # Sizing: Google Solar API if configured, else a sensible default.
            system_kw, annual_kwh, segs = default_kw, None, None
            if sapi:
                try:
                    ins = sapi.insight(ad.lat, ad.lon)
                    if ins:
                        system_kw, annual_kwh, segs = (
                            ins.max_system_kw, ins.annual_kwh, ins.segments)
                except Exception:  # noqa: BLE001
                    pass

            rendered = render.render_with_solar(tile, segs, cfg)
            s = savings.annual_saving(system_kw, a, annual_kwh)
            res = render_flyer(
                {"address": ad.full, "system_kw": s["system_kw"],
                 "annual_kwh": s["annual_generation_kwh"],
                 "annual_saving": s["annual_saving_aud"]},
                rendered, cfg, str(out / "flyers" / f"{i:05d}.html"))
            mail_rows.append({
                "salutation": cfg.get("mail", {}).get("salutation", "To the Homeowner"),
                "address": ad.full, "suburb": ad.suburb, "postcode": ad.postcode,
                "system_kw": s["system_kw"], "annual_saving": s["annual_saving_aud"],
                "flyer_path": res.get("pdf") or res["html"],
            })
        except NotImplementedError as e:
            raise SystemExit(f"[detect] backend not wired yet: {e}")
        except Exception as e:
            print(f"  ! {ad.full}: {e}")

    csv_path = write_mail_csv(mail_rows, str(out / "mail-merge.csv"))
    print(f"\n[done] mail {len(mail_rows)} | already-solar skipped {skipped} | "
          f"review-queue {queued}")
    print(f"[mail] {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
