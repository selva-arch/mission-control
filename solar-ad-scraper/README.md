# Solar Ad Intelligence

Sweep the **Meta (Facebook/Instagram) Ad Library** for Australian solar,
battery, EV-charger and hot-water advertising; archive every ad, creative and
offer into SQLite; infer which **states** each ad targets; and browse it all in
the Mission Control dashboard at **`/solar-ads`**.

Built to answer: *"Who is advertising what, where in Australia, at what price —
and what changed since last week?"*

## Why it works this way

- The **official Ad Library API only returns commercial ads for the EU/UK.**
  Everywhere else it serves political and social-issue ads only. Australian
  solar ads exist **only in the web Ad Library**, so this drives a real browser
  rather than calling an API.
- Ad cards use obfuscated, ever-changing CSS, so we don't scrape the DOM. We
  capture the **GraphQL JSON** the page fetches, which is far more stable.
- Many ads put the price *inside the graphic*, so each creative is downloaded
  and **OCR'd** as well as its text being read.
- Nothing is overwritten. Every sweep writes a new **snapshot** row per ad, so
  a long-running ad whose price suddenly moves is visible as a change.

## ⚠️ State data is inferred, not reported

**Meta publishes no geographic targeting, impressions or spend for Australian
commercial ads.** That data exists only for political/social-issue ads and for
EU ads under the DSA. Every state figure here is therefore *derived*:

| Signal | Confidence | Example |
|---|---|---|
| State name or uppercase abbreviation in copy | high | "NSW", "South Australia" |
| City / regional centre | high | "Adelaide" → SA, "Geelong" → VIC |
| State rebate scheme or distributor | high | "Battery Booster" → QLD, "Ausgrid" → NSW |
| Advertiser's home state | medium | Page transparency / landing page |
| Phone area code | medium | 08 → SA **and** WA **and** NT (ambiguous) |
| The state-scoped search that surfaced the ad | low | delivery evidence only |

Each signal is stored as its own row in `ad_states` with the evidence string
that triggered it, so attribution is auditable and re-weightable without
re-scraping. An ad citing four or more states is flagged `NATIONAL` and
excluded from per-state totals — otherwise one national installer would inflate
every state's numbers.

There are also **no "ad sets"** in the Ad Library: campaign structure and
budgets are private to the advertiser. The public equivalent is the
**collation group** — a cluster of near-identical variants running together,
stored as `collation_id` / `collation_count`.

## Setup

Requires Python 3.10+ and the Tesseract OCR engine.

```bash
cd solar-ad-scraper
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

# Tesseract (for reading prices inside images):
#   macOS:          brew install tesseract
#   Ubuntu/Debian:  sudo apt-get install tesseract-ocr
# (Without it the tool still runs — it just skips image OCR.)

# Optional: structured extraction of ad copy
export ANTHROPIC_API_KEY=sk-...
```

## Run

```bash
python run.py --pilot        # ~10 min validation sweep — do this first
python run.py                # full sweep: 208 targets, several hours
python run.py --resume       # continue an interrupted sweep
python run.py --advertisers  # stage 2: every ad from each discovered Page
python run.py --enrich-only  # LLM pass over already-collected ads
python run.py --csv          # also export a flat CSV
```

The first run opens a browser — **log into Facebook once** and the session is
saved to `.fb-profile/` (gitignored). A full sweep is long, so it commits after
every query: interrupt with Ctrl-C and pick up with `--resume`.

**Start with `--pilot`.** Check the results in the dashboard before committing
hours to a full sweep.

## What gets collected

Per ad: advertiser and Page id, full body copy, title, caption, CTA, landing
URL, display format, publisher platforms, variant-group id and count, start/end
dates, every creative image and video poster frame (stored locally, since
Facebook CDN URLs expire), OCR text, detected price / kWh / kW, `$ per usable
kWh` and `$ per kW`, estimated federal rebate, and state signals.

With `ANTHROPIC_API_KEY` set, an LLM pass adds: product category, brand and
model, price basis (pre/post rebate), offer type, financing terms, claimed
rebates, urgency tactics, warranty, and install inclusion. Extractions are
cached by `(ad, enrich_version)`, so re-runs cost nothing.

## Dashboard

The archive lives at `.data/solar-ads.db`, separate from `mission-control.db`
so a long sweep can never interfere with the app. The panel opens it
**read-only**.

Open Mission Control and visit **`/solar-ads`** (requires Full interface mode —
`Settings → interface mode`, like the other non-essential panels). Tabs:
Overview (state breakdown, median $/kWh by state, product mix, signal mix),
Ads (filterable, with creative thumbnails), Advertisers, and Sweeps.

## Tests

```bash
python test_normalize.py   # parsing, rebate maths, state inference (17)
python test_store.py       # archive round-trip, history, resume (7)
```

## Files

| File | Role |
|------|------|
| `run.py` | Orchestrator: plan → scrape → OCR → prices → states → enrich |
| `targets.py` | Keyword × category × state matrix, and per-advertiser targets |
| `scraper.py` | Playwright session, GraphQL capture, pacing |
| `extract.py` | GraphQL → Ad records; price/kWh/kW parsing; category classifier |
| `states.py` | Multi-signal state inference |
| `normalize.py` | Rebate model, $/kWh, deal scoring |
| `enrich.py` | LLM structured extraction of offer fields |
| `store.py` | SQLite persistence, sweep lifecycle, history |
| `schema.sql` | Archive schema |
| `report_html.py` | Standalone HTML report (offline sharing) |
| `usage_model.py` | Battery savings model from interval meter data |

## Caveats / fair use

- The Ad Library is public, but automated access sits in a grey area of Meta's
  Terms. This paces itself (jittered delays, one browser session, capped
  volume) and is meant for **competitive research**, not bulk harvesting.
  Don't republish the archive.
- Creatives remain the advertisers' copyright — internal analysis only.
- Detected price/capacity are best-effort heuristics over messy copy and OCR.
  Rows flagged `check-parse` fall outside plausible bands. Treat the archive as
  a **shortlist to verify**, not a quote.
- State figures are inferred (see above) and must be presented as such.
- Selectors and GraphQL shapes change; if results come back empty, the JSON
  shape in `extract.py` may need a tweak.
