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
python run.py --reocr        # force re-OCR (e.g. after installing Tesseract)
python run.py --csv          # also export a flat CSV
```

The first run opens a browser — **log into Facebook once** and the session is
saved to `.fb-profile/` (gitignored). A full sweep is long, so it commits after
every query: interrupt with Ctrl-C and pick up with `--resume`.

**Start with `--pilot`.** Check the results in the dashboard before committing
hours to a full sweep.

### Sweep speed

OCR dominates runtime — a 10-query pilot took 54 minutes, almost all of it on
3,789 creatives. Two things keep the full sweep to an overnight job rather than
a two-day one, and both are already the default:

- **OCR results are cached per creative.** The same ad surfaces under many
  search terms; its images are only ever read once. Use `--reocr` to override.
- **`max_images_per_ad: 2`** captures the hero plus the first carousel card,
  which is where prices almost always sit. Raise it in `config.yaml` for a
  deeper pass over a small target list.

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

## Publishing a shareable copy

`report_site.py` builds a self-contained static site from the archive — the same
data and filters as the dashboard, but no server, no database, and a permanent
URL. Mission Control itself cannot be hosted on a static platform: it is a Node
server build, depends on the native better-sqlite3 addon, and serves creatives
off local disk.

```bash
python report_site.py                  # build out/netlify/ (one thumbnail per ad)
python report_site.py --images all     # every creative, larger upload
python report_site.py --images none    # text only, ~2 MB
open out/netlify/public/index.html     # preview with no server
```

Deploy to Netlify, gated by a password:

```bash
npm install -g netlify-cli && netlify login   # once
cd out/netlify
netlify deploy --prod                          # prints the site URL
netlify env:set SITE_PASSWORD 'your-password'
netlify deploy --prod                          # redeploy so the gate applies
```

The password is enforced by a Netlify **edge function**, which runs before static
assets are served — so it covers the images too, not just the page. It **fails
closed**: with no `SITE_PASSWORD` set the site returns 503 rather than publishing
the archive. Verify after deploying:

```bash
curl -sI https://your-site.netlify.app | head -1                 # expect 401
curl -sI -u :'your-password' https://your-site.netlify.app | head -1   # expect 200
```

Two things to keep in mind. The site is a **point-in-time snapshot** — re-run the
generator and redeploy to refresh it. And it republishes advertisers' creative
images, so keep the password on it rather than sharing the URL openly.

## Market rates

The **Market** tab answers "what does a 6.6kW system actually go for, and where
does each advertiser sit against that". Ads are grouped into standard Australian
configurations (3/5/6.6/8/10/13.2/15/20kW+, 5/10/13.5/16/20/27/30kWh+) using
contiguous tolerance bands, so a 9.4kW array counts as the 10kW class rather than
falling through a gap.

Three rules keep the figures honest:

- **Rebate bases are never blended.** A pre-rebate and a post-rebate price
  describe different things, so each gets its own median, sample size and spread.
  Every advertiser is measured against the median for *its own* basis.
- **Suspect parses are excluded** from all statistics, and the number excluded is
  shown rather than quietly dropped.
- **Medians from fewer than 5 ads are labelled indicative.**

Price, capacity and size resolve through the `ad_market` view in `schema.sql`,
which prefers the LLM's reading of an ad over the regex (the regex takes the
largest figure present, which is how rebate amounts and "scalable up to 42kWh"
became prices and capacities). The dashboard and the static site both query that
view, and `test_market.py` asserts the two produce identical numbers.

Basis is only known for ads the enrichment pass has processed, so run
`--enrich-only` before relying on the pre/post split.

## Competitor watchlist

`watchlist.yaml` names the advertisers worth tracking individually, classified by
what they actually sell:

```yaml
installers:    [PSC Energy, Solaray Energy, RACV Solar]
manufacturers: [SunPower, JinkoSolar, WINAICO, Clenergy]
platforms:     [SolarQuotes, Solar Analytics, Brighte]
```

The type is not cosmetic. **Only installers sell an installed system to a
homeowner**, so only their prices belong in the Market tab's medians. A panel
maker's brand ad ($22,000 for premium-tier panels) or a lead-gen site's teaser
($2,000 "quotes from") would wreck a $/kW median while looking perfectly
plausible in the data.

```bash
python run.py --watchlist-sync              # match + classify, no API cost
python run.py --enrich-submit --watchlist   # enrich only these advertisers
python run.py --enrich-fetch
```

`--watchlist-sync` matches the yaml names against the archive and prints what
each one resolved to, with ad counts — and **names anything that matched
nothing**. A global brand running no AU ads is expected; a missing local
competitor means the sweep has a gap worth investigating before you spend
anything.

Matching is loose enough to bridge Instagram handles and Facebook Page names
(`racv_solar` → `RACV Solar`, `JinkoSolar` → `JinkoSolar Australia`) but aligns
on whole words, so a short brand name cannot swallow a longer unrelated one —
`Brighte` does not match `Brighter Solar Solutions`.

**Important nuance in the exclusion:** the medians exclude *known* non-installers
rather than including *only known* installers. Barely a dozen of 700+ advertisers
are classified, so the latter would collapse the market to a handful. Excluded
ads stay fully browsable — they are just kept out of the price statistics, and
the count excluded is stated on the Market tab rather than dropped silently.

In the dashboard: a **Watchlist only** toggle under the Advertiser filter, and a
colour-coded type badge on each ad card so a manufacturer's brand ad is never
mistaken for a competitor's offer.

## Bulk enrichment (thousands of ads)

`--enrich-only` runs synchronously, one request at a time — fine for a few
hundred ads, wrong for a full sweep (a 25k-ad archive means ~3,100 requests;
sequentially that's most of a day, machine awake throughout). For anything
past a few hundred pending ads, use the Message Batches API instead:

```bash
python run.py --enrich-submit          # seconds — submits everything pending
# ... batch runs server-side, usually well within an hour, up to 24h ...
python run.py --enrich-fetch           # collect results once it has finished
```

Half the price of the synchronous path, and the machine can sleep in between —
no `caffeinate` needed for this step. `--enrich-submit` refuses to run while a
batch is still open, so ads are never double-submitted; anything from a failed
request or a malformed response is simply still missing an extraction and gets
picked up automatically the next time you submit. Model comes from
`config.yaml` (`enrich.model`, default `claude-sonnet-5` — bulk extraction
from ad copy is mostly transcription, and a stronger model buys little here).

### Auditing the extraction — `claude-fable-5`

Bulk extraction is cheap and mostly right, but it is worth adversarially
checking rather than trusting blindly — a wrong `price_basis` corrupts the
Market tab's pre/post-rebate split silently. After a bulk pass:

```bash
python run.py --enrich-audit           # checks 200 ads by default
python run.py --enrich-audit 500       # or a specific sample size
```

Fable 5 is given each ad's copy alongside its stored extraction and asked to
find mistakes, specifically: a finance repayment or rebate amount read as the
price, an inverted rebate basis, kW/kWh confusion, or a fabricated brand. The
sample is weighted toward priced ads (what the Market tab actually reads),
spread across rebate basis, and skews toward catalogue/dynamic ads, since their
recovered copy is the least reliable input the extraction saw.

Verdicts are stored separately from the extraction itself (`enrich_audits`,
never overwriting `ad_offers`) and reported as a per-field error rate:

```
  field error rates (of ads where that field was populated):
    price_basis             23/200  (12%) ⚠ re-run this slice
    brand                    4/180  ( 2%)
```

**The rule the report states explicitly: any field wrong more than 5% of the
time means re-running that slice on a stronger model** (clear the relevant
`ad_offers` rows, then `--enrich-submit` again); under 5%, the bulk pass is
trustworthy as-is.

## Coverage: why keyword sweeps are not enough

Ad Library keyword search matches ad **text**. An advertiser running video or
brand ads — "a clean, green future", "new innovations from All Energy" — matches
none of the product keywords, so no keyword pass will ever return them. NRG Solar
had 2 ads captured against ~41 in the library for exactly this reason.

**`--advertisers` is the completeness mechanism.** It opens each discovered Page's
full ad list (`view_all_page_id`) and captures everything, regardless of wording.
Run it after a keyword sweep has discovered who the advertisers are:

```bash
python run.py --sweeps                             # sweep history and status
caffeinate -i python run.py --advertisers          # start (3-5 min per Page)
caffeinate -i python run.py --advertisers --resume # continue on later nights
```

`caffeinate` keeps the Mac awake; without it a sleeping laptop silently stretches
an overnight run across days. `--resume` matches the mode you ask for, so an
abandoned pilot cannot hijack an advertiser sweep.

Before committing hours, check how many Pages can actually be swept — a Page
without an id is skipped:

```bash
sqlite3 ../.data/solar-ads.db \
  "SELECT COUNT(*) advertisers, SUM(page_id != '') sweepable FROM advertisers;"
```

Two related honesty features. A query that stops because it hit `max_scrolls`
rather than running out of results is recorded as `truncated` and reported in the
sweep summary — silent truncation looks exactly like complete coverage. And
**catalogue ads** whose body is an unrendered template (`{{product.name}}`) are
flagged `is_dynamic`; their copy is recovered from the carousel cards, and any
that never resolve to a real price stay out of the market statistics.

## Tests

```bash
python test_normalize.py   # parsing, rebate maths, state inference (17)
python test_store.py       # archive round-trip, history, resume (8)
python test_site.py        # site generator, auth gate, escaping (8)
python test_market.py      # configuration buckets, basis separation (9)
python test_coverage.py    # catalogue ads, truncation, migration (8)
python test_enrich_batch.py # batch submit/fetch, Fable 5 audit (10)
python test_watchlist.py   # matching precision, type-aware medians (11)
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
| `report_site.py` | Build a deployable static site from the archive |
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
