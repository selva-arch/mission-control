# Nearmap integration notes

Verified endpoints, class IDs and gotchas for the solar/roof detection + imagery
used by `pipeline/nearmap.py`. Sourced from Nearmap developer docs and Nearmap's
official SDK (`nearmap/nmaipy`).

## Auth
- JSON APIs (AI Feature, Coverage): header `Authorization: Apikey <KEY>`
  (the scheme word is literally `Apikey` — capital A, not `Bearer`).
- Tile API: query param `?apikey=<KEY>` (param name case-insensitive).
- Keep the key in `config.yaml` (gitignored). IP/referrer restrictions are set on
  the **API App**, not the key, and take ~1h to propagate.

## Detect existing solar
`GET https://api.nearmap.com/ai/features/v4/features.json?polygon=<lon,lat,...>&packs=solar,roof_char`
- `features.json` takes a **polygon AOI**, not a bare point. We build a ~25 m
  square around the address point (use a real parcel/footprint polygon when you
  have one — a square can clip a neighbour's panels).
- Solar is **discrete features**, not a boolean. Filter `features[]` by `classId`:
  - **PV Solar Panel** `3680e1b8-8ae1-5a15-8ec7-820078ef3298` (SDK-verified). Includes ground-mount.
  - **Solar Hot Water** `c1143023-135b-54fd-9a07-8de0ff55de51` — **exclude** from PV.
  - **Roof** `c08255a4-ba9f-562b-932c-ff76f2faeeeb` (verify via `classes.json`).
- `has_solar = any PV feature with confidence ≥ 0.5`; area = sum of
  `clippedAreaSqm` (inside the parcel, not `areaSqm`). No native kW — derive from area.
- Date pin: `&since=YYYY-MM-DD&until=YYYY-MM-DD`.

## Roof sizing
Same call with `packs=roof_char`; sum `clippedAreaSqm` of Roof features. Material/
shape are in each roof feature's `components[]`. `usable ≈ roof_area × 0.5–0.7`;
`system_kw ≈ usable × ~0.18–0.20 kW/m²` (calibrate).

## Imagery (mail asset)
`GET /tiles/v3/Vert/{z}/{x}/{y}.jpg?apikey=KEY` (Vert = ortho; not Panorama).
Stitch an N×N XYZ grid (Web Mercator) and crop to the roof — `pipeline/nearmap.py:NearmapTiles`.
For date consistency pin a survey: resolve a `surveyId` via the Coverage API
(`/coverage/v2/point/{lon},{lat}` — **lon,lat order**) then use
`/tiles/v3/surveys/{surveyId}/Vert/...`.

## Whole-suburb scale
Real-time per-property calls are great for validation and limited runs. For a full
suburb (thousands of parcels) Nearmap steers you to a **bulk AI Offline export**
(parcel rollups `.csv` + vector `.gpkg`) to avoid rate-limit throttling — order via
MapBrowser or your account manager. Both real-time and offline draw the same AI
export-credit balance.

## ⚠ Licensing — confirm before any mail-out
Nearmap's default subscription is **internal-use only**. Putting Nearmap imagery
(or renders derived from it) on flyers mailed to homeowners is **external
distribution** and needs explicit external-use rights in your Order Form / MSA,
plus a mandatory **Nearmap attribution + copyright notice** on the printed Output.
Confirm with your account manager / support@nearmap.com first.

## Run order
1. `python nearmap_check.py` — verify key, packs, classes, coverage, one live lookup.
2. `python run_area_scan.py --suburb "Dulwich, SA" --limit 50` — solar/no-solar CSV.
3. Scale up / move to AI Offline; feed no-solar rows to the flyer batch.

## Open items to confirm with Nearmap
- Exact pack codes on your account (`packs.json`) and the Roof class UUID (`classes.json`).
- Rate limits + AI credit cost per parcel before scaling to thousands.
- External-use / distribution rights + required attribution wording.
- Whether a Properties/parcel API is available to get true parcel polygons.

Sources: developer.nearmap.com (ai-api, tile-api, coverage-api), github.com/nearmap/nmaipy,
help.nearmap.com (AI pack solar panels, AI Offline), nearmap.com/legal (MSA, copyright).
