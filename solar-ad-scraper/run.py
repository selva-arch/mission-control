#!/usr/bin/env python3
"""
Solar Ad Scraper — sweep the Meta Ad Library (Australia) for solar/home battery
ads, pull prices out of the ad text AND the creative images, normalise to
$ per usable kWh after the federal rebate, and write a sorted CSV.

Usage:
    python run.py                  # uses config.yaml
    python run.py --config my.yaml
    python run.py --keyword "Sungrow battery"   # one-off single search

First run opens a browser; log into Facebook once and the session is reused.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

import yaml

import extract
import normalize
import scraper

HERE = Path(__file__).parent


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def download_image(url: str, dest_dir: Path) -> Path | None:
    import requests

    dest_dir.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".jpg"
    dest = dest_dir / name
    if dest.exists():
        return dest
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Meta Ad Library solar battery sweep")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--keyword", help="run a single keyword instead of config list")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    keywords = [args.keyword] if args.keyword else cfg["keywords"]
    country = cfg.get("country", "AU")
    rebate = normalize.RebateModel(**(cfg.get("rebate") or {}))

    # 1. Scrape each keyword and merge ad nodes (deduped by archive id).
    all_ads: dict[str, extract.Ad] = {}
    for kw in keywords:
        print(f"[scrape] {kw!r} ({country}) ...")
        try:
            bodies = scraper.search(
                kw, country,
                max_scrolls=cfg.get("max_scrolls", 12),
                headless=cfg.get("headless", False),
                active_status=cfg.get("active_status", "active"),
            )
        except Exception as e:
            print(f"  ! scrape failed for {kw!r}: {e}")
            continue
        ads = extract.parse_ad_nodes(bodies)
        print(f"  found {len(ads)} ads")
        for aid, ad in ads.items():
            all_ads.setdefault(aid, ad)

    print(f"\n[total] {len(all_ads)} unique ads")

    # 2. OCR creatives + extract price/capacity signals.
    img_dir = HERE / "images"
    rows = []
    for ad in all_ads.values():
        if cfg.get("ocr_enabled", True) and ad.image_urls:
            # OCR every creative (capped), not just the first — the price often
            # sits on a later card, while the first image is a lifestyle shot.
            texts = []
            for url in ad.image_urls[: cfg.get("max_images_per_ad", 4)]:
                local = download_image(url, img_dir)
                if local:
                    texts.append(extract.ocr_image(str(local)))
            ad.ocr_text = "\n".join(t for t in texts if t)

        prices = extract.extract_prices(ad.all_text)
        caps = extract.extract_capacities(ad.all_text)
        cmp = normalize.compare(prices, caps, rebate)
        score = normalize.deal_score(cmp, ad.days_running)

        # Real home batteries land roughly $150-$1500 / usable kWh installed.
        # Anything outside that is almost certainly a bad price/capacity pairing.
        flag = ""
        if cmp.dollars_per_kwh and not (150 <= cmp.dollars_per_kwh <= 1500):
            flag = "check-parse"

        rows.append({
            "page_name": ad.page_name,
            "price_aud": cmp.price or "",
            "capacity_kwh": cmp.capacity_kwh or "",
            "dollars_per_kwh": cmp.dollars_per_kwh or "",
            "flag": flag,
            "est_rebate_aud": cmp.est_rebate or "",
            "price_if_pre_rebate": cmp.price_if_pre_rebate or "",
            "deal_score": score if score != float("inf") else "",
            "status": ad.status,
            "ended": ad.end_date.date().isoformat() if ad.end_date else "",
            "days_running": ad.days_running if ad.days_running is not None else "",
            "copies_running": ad.collation_count or "",
            "all_prices_found": ",".join(str(p) for p in prices),
            "all_kwh_found": ",".join(str(c) for c in caps),
            "landing_url": ad.link_url,
            "library_url": ad.library_url,
        })

    # 3. Sort best-deal-first (lowest $/kWh-derived score). Suspect parses sink
    #    below clean ones; ads with no usable price go last.
    rows.sort(key=lambda r: (r["deal_score"] == "", r["flag"] != "", r["deal_score"] or 0))

    out = HERE / cfg.get("output", "out/solar-ads.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["page_name"])
        w.writeheader()
        w.writerows(rows)

    print(f"[write] {len(rows)} rows -> {out}")
    priced = [r for r in rows if r["dollars_per_kwh"] and not r["flag"]]
    if priced:
        print("\nTop 5 by $/usable kWh:")
        for r in priced[:5]:
            print(f"  {r['dollars_per_kwh']:>7} $/kWh  "
                  f"{r['capacity_kwh']:>6} kWh  ${r['price_aud']:<7} "
                  f"{r['page_name'][:32]:32}  ({r['days_running']}d)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
