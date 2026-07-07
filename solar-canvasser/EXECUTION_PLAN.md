# Solar Canvasser — MVP Execution Plan

Goal: type a suburb → per-address Nearmap imagery → detect roofs with **no** solar →
superimpose panels on the photo → per-address brochure PDF for direct mail / door-knocking.
Operator: non-developer solar installer, Adelaide SA, running on a Mac.

This plan replaces guesswork with a verified current state (code audit + live API checks,
2026-07-07) and sequences the remaining work into gated phases.

---

## 1. Current state — truth table

Live API state (verified from the operator's machine):

| API | Status |
|---|---|
| Nearmap Coverage | ✅ HTTP 200 — survey 2026-03-03, 6.6 cm, Adelaide |
| Nearmap Tile (Vert) | ✅ HTTP 200 |
| Nearmap AI Feature | ❌ HTTP 403 "AI Feature API general access" — entitlement requested |

Code audit (per stage):

| Stage | File | Status | Notes |
|---|---|---|---|
| Geocode (single addr) | `pipeline/addresses.py` `geocode()` | ✅ working | Nominatim, used by `run_address.py` |
| Suburb address list | `pipeline/addresses.py` `fetch_addresses()` | ⚠️ works, incomplete | OSM Overpass: state dropped from query (matches same-named suburbs in other states), misses untagged houses, collapses units. Replace with G-NAF (P5) |
| Imagery | `pipeline/imagery.py` + `nearmap.py` | ✅ working (Nearmap) | Failed sub-tiles silently skipped → black regions; Google path unverified |
| Detection (interim) | `pipeline/detect.py` `vision_model` | ✅ working, default | Anthropic/OpenAI vision on the tile; the ONLY functioning auto-detector today. Media type hardcoded `image/jpeg` (fine for Nearmap) |
| Detection (target) | `pipeline/nearmap.py` `solar()` | ❌ 403-blocked | Parsing correct; dead until entitlement. `run_area_scan.py` is dead with it |
| Roof segments / sizing | `pipeline/solar.py` (Google Solar) | ⚠️ unverified, optional | `requiredQuality:HIGH` 404s many AU roofs; without it every house gets flat 6.6 kW default |
| **Panel render** | `pipeline/render.py` | ❌ **stub — the weak link** | Flat blue 6×4 grid at hardcoded (0.29w, 0.34h), skew only with Google segments (which the working path never has). `mode:ai` raises `NotImplementedError` |
| Savings | `pipeline/savings.py` | ✅ working | Independent of `solar-ad-scraper/usage_model.py` despite docs claiming reuse |
| Flyer | `pipeline/flyer.py` | ✅ HTML / ⚠️ PDF | PDF deps commented out in requirements.txt; **no Nearmap attribution** (NEARMAP.md says mandatory) |
| Mail CSV | `pipeline/mail.py` | ✅ working | |
| Orchestrators | `run_address.py` ✅ · `run_suburb.py` ✅ (vision) · `run_one.py` ❌ Google-gated · `run_area_scan.py` ❌ 403 | | |

Key framing the docs don't state: there are **two** "has-solar?" paths. The Nearmap AI
Feature path is blocked; the `vision_model` path works today and is already the config
default. Everything in this plan runs on the vision path until the entitlement lands.

---

## 2. Phases

### P0 — Entitlements (owner: Selva) — IN FLIGHT
- Email Nearmap rep: (a) enable AI Feature API (solar attribute), (b) confirm
  external-use/print licence for imagery-derived brochure graphics. Draft sent.
- **GATE**: nothing may be printed/mailed until (b) is confirmed. Internal demos fine.

### P1 — Grandview Grove demo (owner: Claude, this session)
Prove the product with zero new pipeline code:
- Operator captures street-block screenshots in Nearmap MapBrowser (address labels ON).
- Claude vision-analyses blocks → per-address verdicts with confidence
  (result 2026-07-07, 4 blocks: ~26 clear no-solar leads, 13 with solar, 10 uncertain, 2 under construction).
- Leads verified to full postal form against property records; uncertain roofs → separate re-shoot list.
- Finding from verification: **Grandview Grove straddles the Dulwich / Toorak Gardens
  boundary at Warwick Ave** (Nos 1–14 Dulwich, 16+ Toorak Gardens; both 5065). Suburb
  attribution must come from G-NAF `LOCALITY_NAME` (P5), never assumed from the search
  suburb — a wrong suburb line on a letter to this demographic reads as junk mail.
- One hero brochure PDF (42 Grandview Grove, Toorak Gardens) with panels composited on the real roof.

### P2 — One-command pipeline (owner: Claude, next build session)
`python run_suburb.py --suburb "Dulwich, SA"` end to end on the vision detector:
- Fix `fetch_addresses()` state bug; wire detect → render → flyer → mail CSV with
  per-address error isolation (one bad house must not kill the batch).
- Enable PDF output (uncomment WeasyPrint or Playwright dep, pick one, test on Mac).
- Add mandatory Nearmap attribution line to the flyer template.
- Fix the 10 stale-doc items from the audit (imagery default, README run list,
  primary key = Nearmap, confidence-threshold spread 0.5/0.6/0.7 → one value in config).

### P3 — Detection calibration (owner: Claude + Selva)
- ~50-roof labelled set from Dulwich tiles; Selva eyeballs, Claude runs the detector.
- Run as a **dynamic Workflow**: fan-out per-roof labelling agents → adversarial
  verify pass on disagreements → confusion matrix → tune vision prompt + threshold.
- Exit criteria: false-negative rate (existing solar called "no solar") < 5% —
  mailing "get solar" to a solar home is the embarrassing failure mode.
- When Nearmap AI entitlement lands: A/B the AI Feature API against the calibrated
  vision detector on the same 50 roofs; switch `backend` in config if it wins.

### P4 — Hybrid render upgrade (owner: Claude)
Replaces the stub composite — the single biggest believability win.
- **Batch (deterministic, $0/image)**: OpenCV/Pillow compositor on the ortho tile —
  Canny + HoughLinesP for ridge azimuth → roof mask + minAreaRect placement → realistic
  pre-rendered panel tile (dark cells, silver frame) rotated to ridge, scaled to real
  panel size (~1.7×1.0 m ≈ 26×15 px at 6.6 cm/px) → drop shadow from sun direction,
  brightness matching, edge feather, ~92–96% opacity. No hallucination risk. ~2–4 days.
- **Premium (AI, later)**: masked **gpt-image-1 edits** (~$0.04/image) reusing the CV
  roof mask so nothing outside the roof changes. Avoid non-masked editors for
  "your home" imagery — hallucinated structural changes are an advertising liability.
  (Gemini 2.5 Flash Image is cheaper at ~$0.02 batch but mask-free; it also sunsets Oct 2026.)
- QA workflow on every batch: sample N renders → parallel judge agents score
  believability/registration → failures re-rendered or dropped to text-only flyer.

### P5 — Address completeness (owner: Claude)
- Load **G-NAF Core** (data.gov.au, quarterly, pipe-separated flat file): filter
  `LOCALITY_NAME == suburb` in pandas/sqlite → number, street, lat/long. Near-complete
  AU coverage; replaces Overpass as primary (keep OSM as fallback).
- AOI per property: fixed ~40 m box around the G-NAF point (matches current Nearmap
  footprint). Skip paid SA cadastre (~$250 min from Land Services SA) unless tight
  blocks demand parcel boundaries.
- **Licence landmine**: G-NAF EULA (CC BY 4.0-based) forbids compiling mail lists
  unless addresses are verified against a secondary deliverability source
  (e.g. Australia Post AMAS/PAF). Add that verification step to the mail CSV export
  before anything is posted. Attribution line required.

### P6 — Dulwich pilot (owner: Selva, gated)
- Full suburb run (~1,200 addresses est.): scan → leads → brochures.
- QA workflow spot-check (30 random brochures, parallel judges) before print.
- Small print run (100–200) to top-scoring leads (pool flag first — high usage).
- **GATES**: P0 print licence confirmed · P3 exit criteria met · G-NAF mail-list
  verification done (P5).

---

## 3. How Fable 5 + dynamic workflows are used

| Phase | Workflow shape |
|---|---|
| P1 demo | Single-context vision analysis (done in chat) |
| P3 calibration | Fan-out per-roof label agents → adversarial verify on disagreements → loop-until-dry on new failure modes |
| P4 render QA | Pipeline: render → parallel believability judges → auto-retry failures |
| P6 pilot | Fan-out per-address processing in batches; completeness critic checks no address dropped silently; final judge panel on print candidates |

Principle: deterministic code does the pixels and the maths; agents do perception
(labelling, QA judging) and get adversarially verified before their output gates money.

---

## 4. Costs (per Dulwich-scale suburb, ~1,200 addresses)

| Item | Est. |
|---|---|
| Nearmap tiles | Existing subscription credits (~40 m footprint per address) |
| Vision detection (Claude/OpenAI) | ~$0.005–0.01/roof → ~$10 |
| Batch render (CV) | $0 |
| Premium AI renders (top ~100 leads) | ~$4 at $0.04/image |
| G-NAF | Free (CC BY, attribution) |
| Print+post (100–200 brochures) | dominant cost — quote locally |

---

## 5. Verification per phase

- **P1**: brochure PDF opens; panels sit on the correct roof; every lead carries a
  verified suburb (Dulwich or Toorak Gardens, 5065); uncertain roofs only in verify list.
- **P2**: `python run_suburb.py --suburb "Dulwich, SA" --limit 20` produces 20 flyer
  PDFs + mail.csv with zero manual steps; `nearmap_check.py` all green except AI step.
- **P3**: confusion matrix on the 50-roof set; FN < 5%.
- **P4**: side-by-side old/new composite on 10 roofs; judge-panel pass rate > 90%.
- **P5**: G-NAF Dulwich count vs OSM count (expect several× more); spot-check 20
  addresses against Nearmap labels.
- **P6**: 30-brochure spot check passes; licence + verification gates ticked.
