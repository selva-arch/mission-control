#!/usr/bin/env python3
"""
Model battery savings from your own smart-meter interval CSV + your tariff.

Reads a NEM12-style "Consumption" interval export (Amount Used, From, To) and:
  - reconstructs your current time-of-use bill,
  - simulates grid-charged battery arbitrage (charge in the cheap window,
    discharge across the peak window) for a range of battery sizes,
  - reports charge times and payback against a battery price.

Your CSV stays local — nothing here is committed or uploaded.

    python usage_model.py --csv ~/Downloads/my-electricity.csv
    python usage_model.py --csv my.csv --price 3708 --usable 16   # payback for a deal

Tariff (rates + the clock hours each period covers) lives in config.yaml under
`tariff:`. Defaults match an SA Power Networks solar-sponge time-of-use plan.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).parent

DEFAULT_TARIFF = {
    "supply_per_day": 0.78958,
    "rates": {"peak": 0.36410, "shoulder": 0.17732, "offpeak": 0.21329},
    "hours": {
        "peak": [6, 7, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23],
        "offpeak": [0, 1, 2, 3, 4, 5],
        "shoulder": [10, 11, 12, 13, 14, 15],
    },
    "round_trip_efficiency": 0.90,
    "battery_sizes_usable": [10, 13.5, 16, 20, 27],
}


def load_tariff(config_path: Path) -> dict:
    if config_path.exists():
        cfg = yaml.safe_load(config_path.read_text()) or {}
        if cfg.get("tariff"):
            return {**DEFAULT_TARIFF, **cfg["tariff"]}
    return DEFAULT_TARIFF


def hour_period_map(hours: dict) -> dict[int, str]:
    m = {}
    for period, hrs in hours.items():
        for h in hrs:
            m[h] = period
    return m


def load_days(csv_path: Path, hmap: dict[int, str]) -> dict:
    """Return {date: {period: kWh, '_byhour': {h: kWh}}}."""
    day = defaultdict(lambda: defaultdict(float))
    byhour = defaultdict(float)
    for r in csv.DictReader(open(csv_path)):
        if (r.get("Usage Type") or "").strip() != "Consumption":
            continue
        dt = datetime.fromisoformat(r["From (date/time)"])
        amt = float(r["Amount Used"])
        day[dt.date()][hmap.get(dt.hour, "peak")] += amt
        byhour[dt.hour] += amt
    return day, byhour


def money(x):
    return f"${x:,.0f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Battery savings model from interval data")
    ap.add_argument("--csv", required=True, help="smart-meter interval CSV")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--price", type=float, help="battery price for payback calc")
    ap.add_argument("--usable", type=float, help="usable kWh for the --price battery")
    args = ap.parse_args()

    T = load_tariff(Path(args.config))
    R = T["rates"]
    eta = T["round_trip_efficiency"]
    hmap = hour_period_map(T["hours"])
    morning_peak = sorted(h for h in T["hours"]["peak"] if h < 12)
    evening_peak = sorted(h for h in T["hours"]["peak"] if h >= 12)

    day, byhour = load_days(Path(args.csv), hmap)
    dates = sorted(day)
    ndays = len(dates)
    yrs = ndays / 365.25
    cheapest = min(R, key=R.get)

    # ---- Current bill ----
    tot = defaultdict(float)
    for d in dates:
        for p, k in day[d].items():
            tot[p] += k
    energy = sum(tot.values())
    ecost = sum(tot[p] * R[p] for p in R)
    supply = T["supply_per_day"] * ndays

    print(f"=== CURRENT BILL  ({ndays} days / {yrs:.2f} yr, {energy/ndays:.1f} kWh/day) ===")
    for p in ("peak", "shoulder", "offpeak"):
        print(f"  {p:9}: {tot[p]:8,.0f} kWh ({tot[p]/energy*100:4.1f}%)  {money(tot[p]*R[p]):>9}/2yr"
              f"  = {money(tot[p]*R[p]/yrs)}/yr")
    print(f"  supply   : {money(supply/yrs):>27}/yr")
    print(f"  TOTAL    : {money((ecost+supply)/yrs):>27}/yr")
    print(f"  -> peak spend is {money(tot['peak']*R['peak']/yrs)}/yr (the target)\n")

    # ---- Battery simulation: smart split (overnight->morning, midday->evening) ----
    print(f"=== BATTERY SAVINGS (charge cheap, discharge peak; {eta*100:.0f}% round-trip) ===")
    print(f"  charge in '{cheapest}' window @ {R[cheapest]*100:.1f}c, discharge peak @ {R['peak']*100:.1f}c")
    print(f"\n{'usable':>6} {'shift/day':>9} {'cycles':>7} {'conservative':>13} {'smart split':>12} {'full-cover':>10}")
    rows = []
    for U in T["battery_sizes_usable"]:
        deliv = full = 0
        for d in dates:
            P = day[d]["peak"]
            deliv += min(U, P)
            if U >= P:
                full += 1
        rows.append((U, deliv, full))

    # Smart-split savings (needs per-day per-hour) computed in a focused pass.
    smart = compute_smart(Path(args.csv), hmap, R, eta, morning_peak, evening_peak,
                          T["battery_sizes_usable"])
    for (U, deliv, full) in rows:
        cons = deliv * (R["peak"] - R[cheapest] / eta)
        print(f"{U:>6} {deliv/ndays:>9.1f} {deliv/ndays/U:>7.2f} "
              f"{money(cons/yrs):>13} {money(smart[U]/yrs):>12} {full/ndays*100:>9.0f}%")

    # ---- Charge time ----
    print(f"\n=== CHARGE TIME (empty->full) — shoulder window is "
          f"{len(T['hours']['shoulder'])}h ===")
    for k in sorted(set(int(x) for x in T["battery_sizes_usable"])):
        print(f"  {k} kWh: " + " / ".join(f"{k/kw:.1f}h@{kw}kW" for kw in (3.3, 5, 7.5)))

    # ---- Payback ----
    if args.price and args.usable:
        U = args.usable
        s = smart.get(U) or interp_smart(smart, U)
        annual = s / yrs
        print(f"\n=== PAYBACK ===")
        print(f"  {args.usable} kWh @ {money(args.price)}  ->  ~{money(annual)}/yr saved"
              f"  ->  payback ~{args.price/annual:.1f} years")
    return 0


def compute_smart(csv_path, hmap, R, eta, morning_peak, evening_peak, sizes):
    """Smart 2-window strategy: cover morning peak from overnight (off-peak)
    charge and evening peak from midday (shoulder) charge — ~1.x cycles/day."""
    per = defaultdict(lambda: defaultdict(float))  # date -> hour -> kWh
    for r in csv.DictReader(open(csv_path)):
        if (r.get("Usage Type") or "").strip() != "Consumption":
            continue
        dt = datetime.fromisoformat(r["From (date/time)"])
        per[dt.date()][dt.hour] += float(r["Amount Used"])
    out = {}
    for U in sizes:
        save = 0.0
        for d, hrs in per.items():
            morn = sum(hrs.get(h, 0) for h in morning_peak)
            eve = sum(hrs.get(h, 0) for h in evening_peak)
            dm = min(U, morn)   # morning delivered, charged at off-peak
            de = min(U, eve)    # evening delivered, charged at shoulder
            save += dm * (R["peak"] - R["offpeak"] / eta)
            save += de * (R["peak"] - R["shoulder"] / eta)
        out[U] = save
    return out


def interp_smart(smart, U):
    xs = sorted(smart)
    if U <= xs[0]:
        return smart[xs[0]] * U / xs[0]
    for a, b in zip(xs, xs[1:]):
        if a <= U <= b:
            t = (U - a) / (b - a)
            return smart[a] + t * (smart[b] - smart[a])
    return smart[xs[-1]]


if __name__ == "__main__":
    raise SystemExit(main())
