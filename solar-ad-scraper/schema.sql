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
