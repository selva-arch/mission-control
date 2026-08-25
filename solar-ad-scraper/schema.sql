-- Solar Ad Intelligence — archive schema
--
-- Design rule: nothing is overwritten. A sweep records what it saw at that
-- moment; the tables that change over time (activity, price, variant count)
-- get one row PER SWEEP so "when did this advertiser move their price?" is
-- answerable later. Identity tables (ads, advertisers) upsert, but only ever
-- widen first_seen / last_seen.

-- One row per collection run.
CREATE TABLE IF NOT EXISTS sweeps (
    sweep_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    INTEGER NOT NULL DEFAULT (unixepoch()),
    finished_at   INTEGER,
    config_hash   TEXT,             -- so we know which config produced this data
    mode          TEXT,             -- pilot | full | advertiser
    targets_total INTEGER DEFAULT 0,
    targets_done  INTEGER DEFAULT 0,
    ads_seen      INTEGER DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'running',  -- running | done | aborted
    notes         TEXT
);

-- Individual search/advertiser queries within a sweep. Drives resumability:
-- run.py --resume skips any target already marked done.
CREATE TABLE IF NOT EXISTS sweep_targets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sweep_id     INTEGER NOT NULL REFERENCES sweeps(sweep_id),
    target_key   TEXT NOT NULL,     -- stable id, e.g. "kw:solar battery:SA"
    kind         TEXT NOT NULL,     -- keyword | advertiser
    query        TEXT,              -- search term or page id
    state        TEXT,              -- state scope of this query, '' if national
    category     TEXT,              -- battery | solar | ev_charger | heat_pump | mixed
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed
    ads_found    INTEGER DEFAULT 0,
    coverage     TEXT,              -- exhausted | truncated
    error        TEXT,
    started_at   INTEGER,
    finished_at  INTEGER,
    UNIQUE (sweep_id, target_key)
);

-- Advertiser (Facebook Page) identity.
CREATE TABLE IF NOT EXISTS advertisers (
    page_id           TEXT PRIMARY KEY,
    page_name         TEXT,
    page_url          TEXT,
    page_categories   TEXT,          -- JSON array
    home_state        TEXT,          -- inferred; see ad_states for per-ad signals
    home_state_source TEXT,          -- how we concluded it
    website           TEXT,
    first_seen        INTEGER NOT NULL DEFAULT (unixepoch()),
    last_seen         INTEGER NOT NULL DEFAULT (unixepoch())
);

-- Ad identity + creative copy. Stable fields only.
CREATE TABLE IF NOT EXISTS ads (
    ad_archive_id       TEXT PRIMARY KEY,
    page_id             TEXT REFERENCES advertisers(page_id),
    page_name           TEXT,          -- denormalised: some payloads carry name but no id
    title               TEXT,
    body_text           TEXT,
    caption             TEXT,
    link_description    TEXT,
    cta_type            TEXT,
    cta_text            TEXT,
    link_url            TEXT,
    display_format      TEXT,          -- IMAGE | VIDEO | DCO | CAROUSEL ...
    publisher_platforms TEXT,          -- JSON array: facebook, instagram, ...
    collation_id        TEXT,          -- variant-group id (the Ad Library's "ad set" analogue)
    start_date          INTEGER,
    end_date            INTEGER,
    ocr_text            TEXT,          -- concatenated OCR across creatives
    is_dynamic          INTEGER NOT NULL DEFAULT 0,  -- catalogue ad: body is a template
    first_seen          INTEGER NOT NULL DEFAULT (unixepoch()),
    last_seen           INTEGER NOT NULL DEFAULT (unixepoch())
);

-- Per-sweep observation of the volatile fields. THIS is the history.
CREATE TABLE IF NOT EXISTS ad_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_archive_id   TEXT NOT NULL REFERENCES ads(ad_archive_id),
    sweep_id        INTEGER NOT NULL REFERENCES sweeps(sweep_id),
    is_active       INTEGER,           -- 1 | 0 | NULL unknown
    collation_count INTEGER,           -- how many near-identical variants live
    days_running    INTEGER,
    observed_at     INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE (ad_archive_id, sweep_id)
);

-- Downloaded creatives, deduped by content hash.
CREATE TABLE IF NOT EXISTS creatives (
    sha1          TEXT PRIMARY KEY,
    ad_archive_id TEXT NOT NULL REFERENCES ads(ad_archive_id),
    kind          TEXT NOT NULL DEFAULT 'image',  -- image | video_poster
    source_url    TEXT,
    local_path    TEXT,              -- relative to the creatives root
    ocr_text      TEXT,
    width         INTEGER,
    height        INTEGER,
    bytes         INTEGER,
    first_seen    INTEGER NOT NULL DEFAULT (unixepoch())
);

-- State inference. MANY rows per ad — one per signal that fired, each with its
-- own confidence and the evidence string that triggered it. Deliberately not
-- collapsed to a single guess: the dashboard decides how much to trust.
CREATE TABLE IF NOT EXISTS ad_states (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_archive_id TEXT NOT NULL REFERENCES ads(ad_archive_id),
    state         TEXT NOT NULL,     -- NSW VIC QLD SA WA TAS NT ACT | NATIONAL
    signal        TEXT NOT NULL,     -- state_token | city | scheme | advertiser | area_code | query | llm
    confidence    TEXT NOT NULL,     -- high | medium | low
    evidence      TEXT,              -- the matched substring, for auditing
    inferred_at   INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE (ad_archive_id, state, signal, evidence)
);

-- LLM-extracted structured offer fields. Versioned so re-running a newer
-- prompt does not destroy earlier extractions.
CREATE TABLE IF NOT EXISTS ad_offers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_archive_id    TEXT NOT NULL REFERENCES ads(ad_archive_id),
    enrich_version   TEXT NOT NULL,
    model            TEXT,
    product_category TEXT,           -- battery | solar | solar_battery | ev_charger | heat_pump | other
    brand            TEXT,
    model_name       TEXT,
    price_aud        REAL,
    price_basis      TEXT,           -- post_rebate | pre_rebate | unknown
    capacity_kwh     REAL,
    system_kw        REAL,
    offer_type       TEXT,           -- discount | financing | bundle | free_upgrade | quote_only
    finance_terms    TEXT,
    claimed_rebates  TEXT,           -- JSON array
    states_mentioned TEXT,           -- JSON array
    cities_mentioned TEXT,           -- JSON array
    urgency_tactics  TEXT,           -- JSON array
    warranty_years   REAL,
    install_included INTEGER,
    cta              TEXT,
    raw_json         TEXT,
    created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE (ad_archive_id, enrich_version)
);

-- Price/capacity readings per sweep, from regex+OCR and/or the LLM. Both are
-- stored rather than one overwriting the other, so disagreement is measurable.
CREATE TABLE IF NOT EXISTS price_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_archive_id   TEXT NOT NULL REFERENCES ads(ad_archive_id),
    sweep_id        INTEGER REFERENCES sweeps(sweep_id),
    source          TEXT NOT NULL,   -- regex | llm
    price_aud       REAL,
    capacity_kwh    REAL,
    system_kw       REAL,
    dollars_per_kwh REAL,
    dollars_per_kw  REAL,
    est_rebate_aud  REAL,
    basis           TEXT,            -- post_rebate | pre_rebate | unknown
    flag            TEXT,            -- check-parse when outside plausible bands
    all_prices      TEXT,            -- JSON array of every figure found
    all_kwh         TEXT,
    all_kw          TEXT,
    observed_at     INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE (ad_archive_id, sweep_id, source)
);

-- Which query surfaced which ad. Feeds the low-confidence 'query' state signal
-- and shows keyword coverage.
CREATE TABLE IF NOT EXISTS ad_targets (
    ad_archive_id TEXT NOT NULL REFERENCES ads(ad_archive_id),
    sweep_id      INTEGER NOT NULL REFERENCES sweeps(sweep_id),
    target_key    TEXT NOT NULL,
    PRIMARY KEY (ad_archive_id, sweep_id, target_key)
);

CREATE INDEX IF NOT EXISTS idx_ads_page          ON ads(page_id);
CREATE INDEX IF NOT EXISTS idx_ads_start         ON ads(start_date);
CREATE INDEX IF NOT EXISTS idx_snapshots_sweep   ON ad_snapshots(sweep_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_ad      ON ad_snapshots(ad_archive_id);
CREATE INDEX IF NOT EXISTS idx_states_ad         ON ad_states(ad_archive_id);
CREATE INDEX IF NOT EXISTS idx_states_state      ON ad_states(state, confidence);
CREATE INDEX IF NOT EXISTS idx_creatives_ad      ON creatives(ad_archive_id);
CREATE INDEX IF NOT EXISTS idx_prices_ad         ON price_observations(ad_archive_id);
CREATE INDEX IF NOT EXISTS idx_prices_sweep      ON price_observations(sweep_id);
CREATE INDEX IF NOT EXISTS idx_offers_ad         ON ad_offers(ad_archive_id);
CREATE INDEX IF NOT EXISTS idx_targets_sweep     ON sweep_targets(sweep_id, status);

-- One row per submitted Message Batch. Recovery is by absence (an ad with no
-- ad_offers row is simply still pending) rather than a custom_id->ad mapping,
-- so this table only needs enough to know whether a batch is still open and
-- what model it used.
CREATE TABLE IF NOT EXISTS enrich_batches (
    batch_id      TEXT PRIMARY KEY,
    model         TEXT,
    submitted_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    fetched_at    INTEGER,
    request_count INTEGER,
    status        TEXT NOT NULL DEFAULT 'submitted',  -- submitted | done
    succeeded     INTEGER,
    errored       INTEGER
);

-- Fable 5 audit verdicts on the extractions in ad_offers. A separate table
-- rather than another ad_offers row: an audit verdict (confirmed/wrong + which
-- fields) is a different shape from an offer extraction, not a competing
-- reading of the same fields.
CREATE TABLE IF NOT EXISTS enrich_audits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_archive_id  TEXT NOT NULL REFERENCES ads(ad_archive_id),
    audit_version  TEXT NOT NULL,
    model          TEXT,
    verdict        TEXT NOT NULL,      -- confirmed | wrong
    wrong_fields   TEXT,               -- JSON array, empty when confirmed
    note           TEXT,
    created_at     INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE (ad_archive_id, audit_version)
);

-- Advertiser classification, populated by `run.py --watchlist-sync` from
-- watchlist.yaml.
--
-- page_name holds the EXACT string as stored on ads, not the loose name typed
-- into the yaml. The fuzzy matching happens once in Python, where it can be
-- reported and reviewed; every join after that is a fast exact equality.
CREATE TABLE IF NOT EXISTS advertiser_types (
    page_name       TEXT PRIMARY KEY,
    label           TEXT,               -- the watchlist.yaml entry that matched
    advertiser_type TEXT NOT NULL,      -- installer | manufacturer | platform
    on_watchlist    INTEGER NOT NULL DEFAULT 1,
    synced_at       INTEGER NOT NULL DEFAULT (unixepoch())
);

-- ---------------------------------------------------------------------------
-- ad_market: the single definition of "what does this ad actually offer".
--
-- The dashboard is TypeScript and the shareable site is Python. Resolving the
-- price twice, once per language, guarantees they eventually disagree — and two
-- views of one archive that quietly contradict each other are worse than having
-- only one. Both query this view instead.
--
-- DROP then CREATE, not CREATE IF NOT EXISTS: store.connect() runs this schema
-- on every open, and IF NOT EXISTS would keep a stale definition forever on
-- archives that already have the view.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS ad_market;
CREATE VIEW ad_market AS
WITH latest_price AS (
    SELECT p.* FROM price_observations p
     WHERE p.source = 'regex'
       AND p.id = (SELECT MAX(id) FROM price_observations
                    WHERE ad_archive_id = p.ad_archive_id AND source = 'regex')
),
latest_offer AS (
    SELECT o.* FROM ad_offers o
     WHERE o.id = (SELECT MAX(id) FROM ad_offers
                    WHERE ad_archive_id = o.ad_archive_id)
),
latest_snap AS (
    SELECT s.* FROM ad_snapshots s
     WHERE s.id = (SELECT MAX(id) FROM ad_snapshots
                    WHERE ad_archive_id = s.ad_archive_id)
),
resolved AS (
    SELECT
        a.ad_archive_id,
        a.page_id,
        a.page_name,
        a.start_date,
        -- Prefer what the model read out of the ad over what the regex guessed.
        -- The regex takes max() of every figure present, which is why rebate
        -- amounts and "scalable up to 42kWh" ended up as prices and capacities.
        COALESCE(o.price_aud,    p.price_aud)    AS price,
        COALESCE(o.capacity_kwh, p.capacity_kwh) AS kwh,
        COALESCE(o.system_kw,    p.system_kw)    AS kw,
        CASE WHEN o.price_aud IS NOT NULL THEN 'llm' ELSE 'regex' END AS price_source,
        COALESCE(NULLIF(o.price_basis, ''), 'unknown') AS basis,
        COALESCE(NULLIF(o.product_category, ''), '')   AS category,
        COALESCE(NULLIF(o.brand, ''), '')              AS brand,
        COALESCE(NULLIF(o.offer_type, ''), '')         AS offer_type,
        o.finance_terms,
        o.urgency_tactics,
        a.is_dynamic,
        -- Suspect when the flagged regex reading still stands, or when a
        -- catalogue ad's copy never resolved to anything real. An LLM value
        -- supersedes both, because the flag described the old read.
        CASE WHEN o.price_aud IS NULL
                  AND (COALESCE(p.flag, '') != '' OR a.is_dynamic = 1)
             THEN 1 ELSE 0 END AS suspect,
        s.is_active,
        s.days_running,
        s.collation_count,
        -- Unclassified advertisers are 'unknown', NOT excluded. Only ~10 of 700+
        -- are typed, so treating unclassified as non-installer would collapse
        -- the market to a handful of advertisers.
        COALESCE(t.advertiser_type, 'unknown') AS advertiser_type,
        COALESCE(t.on_watchlist, 0)            AS on_watchlist
      FROM ads a
      LEFT JOIN latest_price p ON p.ad_archive_id = a.ad_archive_id
      LEFT JOIN latest_offer o ON o.ad_archive_id = a.ad_archive_id
      LEFT JOIN latest_snap  s ON s.ad_archive_id = a.ad_archive_id
      LEFT JOIN advertiser_types t ON t.page_name = a.page_name
)
SELECT
    r.*,
    -- Standard Australian residential configurations. Bands are contiguous so
    -- every real system maps to the size a customer would call it — a 9.4kW
    -- array is sold as "10kW". Gaps would silently drop ads from the market
    -- medians, which matters most when the sample is thin.
    --
    -- 6.6kW (a 5kW inverter at 133% oversizing) and 13.5kWh (Powerwall) are the
    -- dominant labels in this market. Outside the residential range the size is
    -- left NULL rather than forced into a bucket it does not belong in.
    CASE
        WHEN r.kw IS NULL OR r.kw < 2.0 OR r.kw > 40.0 THEN NULL
        WHEN r.kw <  4.0 THEN '3kW'
        WHEN r.kw <  6.0 THEN '5kW'
        WHEN r.kw <= 7.2 THEN '6.6kW'
        WHEN r.kw <= 9.0 THEN '8kW'
        WHEN r.kw <= 11.5 THEN '10kW'
        WHEN r.kw <= 14.0 THEN '13.2kW'
        WHEN r.kw <= 17.0 THEN '15kW'
        ELSE '20kW+'
    END AS config_kw,
    CASE
        WHEN r.kwh IS NULL OR r.kwh < 3.0 OR r.kwh > 60.0 THEN NULL
        WHEN r.kwh <  7.0 THEN '5kWh'
        WHEN r.kwh <= 11.5 THEN '10kWh'
        WHEN r.kwh <= 14.9 THEN '13.5kWh'
        WHEN r.kwh <= 17.5 THEN '16kWh'
        WHEN r.kwh <= 23.0 THEN '20kWh'
        WHEN r.kwh <= 29.0 THEN '27kWh'
        ELSE '30kWh+'
    END AS config_kwh,
    CASE WHEN r.price IS NOT NULL AND r.kw  IS NOT NULL AND r.kw  > 0
         THEN ROUND(r.price / r.kw, 1) END  AS per_kw,
    CASE WHEN r.price IS NOT NULL AND r.kwh IS NOT NULL AND r.kwh > 0
         THEN ROUND(r.price / r.kwh, 1) END AS per_kwh
  FROM resolved r;
