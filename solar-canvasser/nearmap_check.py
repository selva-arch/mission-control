#!/usr/bin/env python3
"""
One-command Nearmap sanity check — run this FIRST, before a full scan.

  python nearmap_check.py

Confirms, against your account:
  1. the key authenticates,
  2. the 'solar' (and roof_char) AI packs are enabled,
  3. the Solar Panel + Roof class IDs exist (and match what the code uses),
  4. coverage exists over the target suburb (latest survey date),
  5. a real per-property solar lookup returns data.

If any step fails it tells you what to ask your Nearmap account manager.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from pipeline import addresses
from pipeline.nearmap import NearmapClient, PV_PANEL_ID, ROOF_ID

HERE = Path(__file__).parent


def main() -> int:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    nm = cfg["nearmap"]
    client = NearmapClient(nm["api_key"], packs=nm.get("packs", "solar,roof_char"),
                           ai_path=nm.get("ai_path", "/ai/features/v4/features.json"),
                           aoi_size_m=nm.get("aoi_size_m", 25))

    print("1. Auth + packs ...")
    try:
        packs = client.packs_available()
        names = [p.get("code") or p.get("name") for p in (packs if isinstance(packs, list)
                 else packs.get("packs", []))]
        print(f"   packs available: {names}")
        if not any("solar" in str(n).lower() for n in names):
            print("   ⚠ no 'solar' pack — ask your account manager to enable the Solar Panels AI pack")
    except Exception as e:
        print(f"   ✗ packs.json failed: {e}\n   → key/auth or entitlement issue")
        return 1

    print("2. Classes (verify Solar Panel + Roof IDs) ...")
    try:
        classes = client.classes()
        items = classes if isinstance(classes, list) else classes.get("classes", [])
        ids = {c.get("id") or c.get("classId"): c.get("description") for c in items}
        print(f"   Solar Panel id present: {PV_PANEL_ID in ids}  "
              f"({ids.get(PV_PANEL_ID, 'NOT FOUND')})")
        print(f"   Roof id present:        {ROOF_ID in ids}  "
              f"({ids.get(ROOF_ID, 'NOT FOUND — update ROOF_ID in nearmap.py')})")
    except Exception as e:
        print(f"   ✗ classes.json failed: {e}")

    print("3. Coverage over the suburb ...")
    addrs = addresses.fetch_addresses(cfg["target_suburb"], limit=1)
    if not addrs:
        print("   ⚠ no addresses from OSM for this suburb — try a known lat/lon manually")
        return 1
    a = addrs[0]
    print(f"   test address: {a.full}  ({a.lat:.5f},{a.lon:.5f})")
    try:
        cov = client.coverage(a.lat, a.lon)
        surveys = cov.get("surveys", [])
        latest = surveys[0] if surveys else {}
        print(f"   surveys: {len(surveys)}, latest capture: "
              f"{latest.get('captureDate') or latest.get('lastPhotoTime', '?')}")
    except Exception as e:
        print(f"   ✗ coverage failed: {e}")

    print("4. Live solar lookup on the test property ...")
    res = client.solar(a.lat, a.lon)
    if res.error:
        print(f"   ✗ {res.error}")
        return 1
    print(f"   has_solar={res.has_solar} panels={res.panel_count} "
          f"pv_area={res.pv_area_sqm}m² roof_area={res.roof_area_sqm}m² "
          f"conf={res.confidence} survey={res.survey_date}")
    print("\n✓ Nearmap is wired up. Next:  python run_area_scan.py --suburb \""
          f"{cfg['target_suburb']}\" --limit 50")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
