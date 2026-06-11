#!/usr/bin/env python3
"""
Re-rank an already-scraped solar-ads.csv WITHOUT re-scraping.

The main sweep writes every ad it found; this slices that file by capacity,
price, and ad age so you can find the best deal for *your* use case (e.g. a
10-20 kWh battery for overnight charge/discharge) instead of the raw cheapest
$/kWh, which is always dominated by oversized 30-50 kWh systems.

Examples:
    python report.py --min-kwh 10 --max-kwh 20        # your overnight-arbitrage range
    python report.py --min-kwh 10 --max-kwh 20 --min-days 30   # only stable, long-running ads
    python report.py --top 20                          # top 20 across all sizes
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

HERE = Path(__file__).parent


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-rank scraped solar ads")
    ap.add_argument("--csv", default=str(HERE / "out" / "solar-ads.csv"))
    ap.add_argument("--min-kwh", type=float, default=0)
    ap.add_argument("--max-kwh", type=float, default=1e9)
    ap.add_argument("--max-per-kwh", type=float, default=1e9,
                    help="drop deals above this $/usable kWh")
    ap.add_argument("--min-days", type=int, default=0,
                    help="only ads running at least this many days")
    ap.add_argument("--include-flagged", action="store_true",
                    help="keep rows flagged as suspect parses")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    with open(args.csv) as f:
        rows = list(csv.DictReader(f))

    kept = []
    for r in rows:
        dpk = to_float(r.get("dollars_per_kwh"))
        kwh = to_float(r.get("capacity_kwh"))
        days = to_float(r.get("days_running"))
        if dpk is None or kwh is None:
            continue
        if r.get("flag") and not args.include_flagged:
            continue
        if not (args.min_kwh <= kwh <= args.max_kwh):
            continue
        if dpk > args.max_per_kwh:
            continue
        if days is not None and days < args.min_days:
            continue
        kept.append(r)

    kept.sort(key=lambda r: to_float(r["dollars_per_kwh"]))

    print(f"\n{len(kept)} ads match "
          f"({args.min_kwh:g}-{args.max_kwh:g} kWh"
          f"{', ' + str(args.min_days) + 'd+' if args.min_days else ''}). "
          f"Top {min(args.top, len(kept))} by $/usable kWh:\n")
    print(f"{'$/kWh':>7}  {'kWh':>5}  {'price':>8}  {'rebate':>7}  "
          f"{'days':>4}  {'copies':>6}  advertiser")
    print("-" * 90)
    for r in kept[: args.top]:
        print(f"{to_float(r['dollars_per_kwh']):>7.0f}  "
              f"{to_float(r['capacity_kwh']):>5.1f}  "
              f"${r['price_aud']:>7}  "
              f"${r.get('est_rebate_aud') or '?':>6}  "
              f"{r.get('days_running') or '?':>4}  "
              f"{r.get('copies_running') or '-':>6}  "
              f"{r['page_name'][:34]}")
        print(f"{'':>7}  {'':>5}  {'':>8}  {'':>7}  {'':>4}  {'':>6}  "
              f"{r.get('library_url', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
