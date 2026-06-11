# Solar Ad Scraper

Sweep the **Meta (Facebook/Instagram) Ad Library** for Australian solar / home
battery ads, extract the price points — **including prices baked into the ad
images** — normalise everything to **$ per usable kWh after the federal
rebate**, and rank the offers best-deal-first.

Built for the specific job of: *"I keep seeing battery ads on Instagram — which
one is actually the cheapest, and which ads are quietly running a stable public
price?"*

## Why it works this way

- The **official Ad Library API only returns political / social-issue ads** (and
  broader commercial data inside the EU). Australian commercial battery ads are
  only in the **web Ad Library**, so this tool drives a real browser instead of
  calling an API.
- Ad cards use obfuscated, ever-changing CSS, so we don't scrape the DOM. We
  capture the **GraphQL JSON** the page fetches — much more stable.
- Many ads put the price *inside the graphic* (e.g. the FOX ESS "$5,981"), so we
  download each creative and **OCR** it as well as reading the ad text.
- Your hypothesis — long-running, rarely-updated ads carry the most honest public
  price — is built into the scoring: ads that have run a long time get a small
  ranking bump.

## What you get

A sorted `out/solar-ads.csv` with, per ad: advertiser, detected price, capacity
(kWh), **$/usable kWh**, estimated federal rebate, implied price if the headline
was pre-rebate, a deal score, **days the ad has been running**, number of
near-identical copies live, every price/kWh figure found, the landing-page URL,
and a direct Ad Library link.

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
#   Windows:        https://github.com/UB-Mannheim/tesseract/wiki
# (If Tesseract isn't installed, the tool still runs — it just skips image OCR.)
```

## Run

```bash
python run.py                       # full sweep from config.yaml
python run.py --keyword "Sungrow battery"   # one quick search
```

The first run opens a browser window. **Log into Facebook once** — the session
is saved to `.fb-profile/` (gitignored) and reused after that. Then it searches
each keyword, scrolls to load results, extracts and ranks, and writes the CSV.
It also prints the top 5 by $/usable kWh to the terminal.

Tune `config.yaml`: `keywords`, `max_scrolls` (depth), `headless`, and the
`rebate` tier values (pre-set to the post-1-May-2026 Cheaper Home Batteries
Program structure).

## Reading the results

- **Sort by `dollars_per_kwh`** for the raw best value.
- **`price_if_pre_rebate`** helps you sanity-check whether an advertised price
  already includes the ~30% federal rebate or not — ask the installer to confirm.
- **High `days_running` + an explicit `price_aud`** = the stable public price
  points you're after. Quote-only ads (no price) sink to the bottom.
- Re-run weekly; a long-running ad whose price suddenly moves is your signal.

## Caveats / fair use

- The Ad Library is public, but automated access sits in a grey area of Meta's
  Terms. Keep it gentle (this tool paces itself and is meant for a handful of
  keywords for **personal research**, not bulk harvesting).
- Detected price/capacity are best-effort heuristics over messy ad copy + OCR.
  Treat the CSV as a **shortlist to verify**, not a final quote. Always confirm
  inclusions (inverter, installation, warranty, rebate, grid-charge support)
  with the retailer before buying.
- Selectors/GraphQL shapes can change; if results come back empty, the JSON
  shape in `extract.py` may need a tweak.

## Files

| File | Role |
|------|------|
| `run.py` | CLI orchestrator: scrape → OCR → normalise → CSV |
| `scraper.py` | Playwright browser driver + GraphQL response capture |
| `extract.py` | Parse GraphQL JSON into ads; price/kWh regex + OCR |
| `normalize.py` | Rebate model, $/kWh, deal scoring |
| `config.yaml` | Keywords, depth, rebate tiers, output path |
| `test_normalize.py` | Sanity tests (uses the three screenshot ads) |
