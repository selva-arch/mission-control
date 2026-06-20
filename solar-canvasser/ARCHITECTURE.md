# Architecture

## Goal

For a target suburb, identify dwellings **without** rooftop solar, generate a
personalised "your house with solar" flyer (rendered image + estimated savings),
and prepare it for direct mail — for a solar installer's own lead generation.

## Data flow

```
                    ┌─────────────────────────────────────────────┐
 suburb name ──▶ addresses.py ──▶ [ {address, lat, lon}, ... ]     │
                    │                                              │
                    ▼                                              │
              for each address:                                   │
                    │                                              │
        imagery.py  ▼  (ImagerySource — swappable)                 │
            roof tile (PNG, top-down)                              │
                    │                                              │
        detect.py   ▼                                             │
            has_panels?  ──yes──▶ skip (already a customer's roof) │
                    │ no                                           │
        solar.py    ▼  (Google Solar API)                         │
            roof_area_m², max_kw, annual_kwh, roof_segments        │
                    │                                              │
        render.py   ▼                                             │
            "with solar" image (panels composited on roof)        │
                    │                                              │
        savings.py  ▼  (reuses tariff model)                      │
            est_annual_saving_$                                    │
                    │                                              │
        flyer.py    ▼                                             │
            flyer.html ──▶ flyer.pdf                              │
                    │                                              │
        mail.py     ▼                                             │
            row in mail-merge.csv (address, asset path)           │
                    └──────────────────────────────────────────────┘
```

## Components

### addresses.py — suburb → address list
- **MVP:** OpenStreetMap **Overpass API** (no key). Query buildings + `addr:*`
  tags within the suburb boundary relation. Returns address + rooftop centroid.
- **Production:** **G-NAF** (Geocoded National Address File, free open data from
  Geoscape) for complete, authoritative AU addresses + coordinates. Overpass
  misses unmapped addresses; G-NAF is the gold source for a real mail-out.

### imagery.py — aerial tile per roof  *(swappable — the key abstraction)*
- `ImagerySource` interface → `tile(lat, lon) -> PNG bytes`.
- **GoogleStaticImagery** (default): Maps Static API, `maptype=satellite`,
  high zoom (~20–21), centred on the rooftop.
- **Why swappable:** Google's terms restrict imagery in printed mail. Keep
  Google for the cheap *detection* pass; swap a **LicensedImagery** (Nearmap
  commercial, or drone/owned capture) for the *printed* flyer image only.

### detect.py — has-panels classifier
- **MVP (zero training):** call a multimodal **vision model** with the tile and
  a tight prompt ("Are there solar PV panels on this roof? yes/no + confidence").
  Good enough to triage a suburb; cheap per call.
- **Scale:** fine-tune a CNN (DeepSolar / a YOLO segmentation head) on labelled
  AU rooftops for speed, cost, and offline batch runs.
- Output: `{has_panels: bool, confidence: float}`. Mail only `has_panels == False`
  above a confidence threshold; route low-confidence to a human queue (hybrid).

### solar.py — roof sizing + generation potential
- **Google Solar API** `buildingInsights:findClosest` → roof area, max panel
  count, per-segment azimuth/pitch, and modelled annual kWh for panel configs.
- Note: the Solar API does **not** report existing panels — that's `detect.py`'s
  job. Solar API is used for sizing, the realistic panel layout (drives render),
  and the generation figure that feeds savings.
- Fallback where Solar API lacks coverage: roof area from imagery/footprint →
  kW via ~6 m²/panel, 440 W/panel → annual kWh via local specific yield
  (~1,400 kWh/kW/yr for Adelaide).

### render.py — panels on the roof
- **MVP:** composite a panel texture/grid onto the roof polygon using the
  segment geometry from Solar API (correct azimuth → believable perspective).
- **Upgrade:** AI image-edit (inpaint panels) for photorealism.
- Output: a "before → after" or single "with solar" image for the flyer.

### savings.py — estimated $ saved / year
- Reuses the tariff approach from the battery model: annual generation × the mix
  of self-consumption (offsets usage at your retail rate) + export (feed-in
  tariff). Honest, indicative, clearly labelled — see Compliance.

### flyer.py — printable per-house asset
- Jinja2 HTML template → PDF (WeasyPrint or headless Chrome). Includes the
  rendered roof, the address, indicative system size + $ saving, and the CTA
  (QR → booking page / phone). DL or A4.

### mail.py — addressing + post
- No resident name required: address "To the Homeowner / The Resident".
- Emits a mail-merge CSV (address, flyer path) for Australia Post addressed mail
  or a mail house. Supports Australia Post **unaddressed** (cheaper, whole-route)
  vs **addressed** (target only no-solar homes — usually better ROI here).

## Cost model (≈1,400 Dulwich dwellings)

| Item | Per address | Suburb |
|------|-------------|--------|
| Geocoding + Static imagery | ~$0.005 | ~$7 |
| Solar API building insights | ~$0.01–0.10 | ~$15–140 |
| Vision detection | ~$0.003–0.01 | ~$5–15 |
| **Tech subtotal** | **~$0.05–0.15** | **~$70–210** |
| Print + postage | ~$0.60–1.10 | **~$850–1,550** |

Postage dominates ~10:1. With ~40% of SA roofs already solar, detection that
correctly skips them cuts the postage bill ~40% — the highest-leverage accuracy
target in the whole system.

## Compliance (Australia)

- **Direct mail is permitted.** The Spam Act 2003 covers *electronic* messages
  only; physical mail is out of scope.
- **Imagery licensing** is the real constraint — see imagery.py. Don't print
  Google/Nearmap imagery in mailers without the right licence.
- **Australian Consumer Law:** savings figures must be honest and clearly
  "indicative / subject to a site assessment". No guaranteed-dollar claims.
- **Privacy Act:** mailing to an address without a name and without storing
  personal info keeps exposure minimal. If you later capture responses/leads,
  add a privacy policy and consent.

## Build order (recommended)

1. `run_one.py` end-to-end on one Dulwich address (validate every stage cheaply).
2. Detection accuracy pass on ~50 hand-labelled Dulwich roofs (tune threshold).
3. Batch `run_suburb.py`, human-review low-confidence, generate flyers + CSV.
4. Small test mail-out (50–100 homes), measure response, then scale.
