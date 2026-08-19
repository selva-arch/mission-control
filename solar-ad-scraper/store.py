"""
SQLite persistence for the ad archive.

Why a separate database from Mission Control's own `.data/mission-control.db`:
this archive grows to gigabytes of creatives and snapshot rows, and a sweep is
a long-running writer. Keeping it in its own file means a scrape can never
block or corrupt the app's operational schema, and the dashboard can open it
read-only.

Everything here is idempotent. Re-running a sweep over ads already in the
database updates `last_seen` and adds a new snapshot row, but never destroys
what an earlier sweep recorded.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_DB = HERE.parent / ".data" / "solar-ads.db"
SCHEMA = HERE / "schema.sql"


def _json(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the archive database with the schema applied."""
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Same posture as src/lib/db.ts: WAL so the dashboard can read while a
    # sweep writes, and a busy timeout so concurrent access waits rather than
    # erroring out mid-sweep.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA.read_text())
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Sweep lifecycle
# ---------------------------------------------------------------------------

def begin_sweep(conn, config_hash: str = "", mode: str = "full") -> int:
    cur = conn.execute(
        "INSERT INTO sweeps (config_hash, mode) VALUES (?, ?)",
        (config_hash, mode),
    )
    conn.commit()
    return cur.lastrowid


def finish_sweep(conn, sweep_id: int, status: str = "done", notes: str = "") -> None:
    conn.execute(
        "UPDATE sweeps SET finished_at = ?, status = ?, notes = ?, "
        "ads_seen = (SELECT COUNT(*) FROM ad_snapshots WHERE sweep_id = ?), "
        "targets_done = (SELECT COUNT(*) FROM sweep_targets "
        "                WHERE sweep_id = ? AND status = 'done') "
        "WHERE sweep_id = ?",
        (int(time.time()), status, notes, sweep_id, sweep_id, sweep_id),
    )
    conn.commit()


def latest_unfinished_sweep(conn, mode: str | None = None) -> int | None:
    """Find a sweep to resume: the newest one still marked running."""
    sql = "SELECT sweep_id FROM sweeps WHERE status = 'running'"
    args: list = []
    if mode:
        sql += " AND mode = ?"
        args.append(mode)
    sql += " ORDER BY sweep_id DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    return row["sweep_id"] if row else None


def register_targets(conn, sweep_id: int, targets: list[dict]) -> None:
    """Record the planned work for a sweep. Safe to call again on resume —
    existing rows (and their done/failed status) are preserved."""
    conn.executemany(
        "INSERT OR IGNORE INTO sweep_targets "
        "(sweep_id, target_key, kind, query, state, category) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (sweep_id, t["key"], t["kind"], t.get("query", ""),
             t.get("state", ""), t.get("category", ""))
            for t in targets
        ],
    )
    conn.execute(
        "UPDATE sweeps SET targets_total = "
        "(SELECT COUNT(*) FROM sweep_targets WHERE sweep_id = ?) WHERE sweep_id = ?",
        (sweep_id, sweep_id),
    )
    conn.commit()


def pending_targets(conn, sweep_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sweep_targets WHERE sweep_id = ? AND status != 'done' "
        "ORDER BY id",
        (sweep_id,),
    ).fetchall()


def start_target(conn, sweep_id: int, target_key: str) -> None:
    conn.execute(
        "UPDATE sweep_targets SET started_at = ? WHERE sweep_id = ? AND target_key = ?",
        (int(time.time()), sweep_id, target_key),
    )
    conn.commit()


def finish_target(conn, sweep_id: int, target_key: str, ads_found: int = 0,
                  status: str = "done", error: str = "") -> None:
    conn.execute(
        "UPDATE sweep_targets SET status = ?, ads_found = ?, error = ?, "
        "finished_at = ? WHERE sweep_id = ? AND target_key = ?",
        (status, ads_found, error, int(time.time()), sweep_id, target_key),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Identity upserts
# ---------------------------------------------------------------------------

def upsert_advertiser(conn, page_id: str, page_name: str = "", page_url: str = "",
                      page_categories=None, website: str = "") -> None:
    if not page_id:
        return
    now = int(time.time())
    conn.execute(
        "INSERT INTO advertisers (page_id, page_name, page_url, page_categories, "
        "website, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(page_id) DO UPDATE SET "
        "  page_name       = COALESCE(NULLIF(excluded.page_name, ''), page_name), "
        "  page_url        = COALESCE(NULLIF(excluded.page_url, ''), page_url), "
        "  page_categories = COALESCE(excluded.page_categories, page_categories), "
        "  website         = COALESCE(NULLIF(excluded.website, ''), website), "
        "  last_seen       = excluded.last_seen",
        (page_id, page_name, page_url, _json(page_categories), website, now, now),
    )


def set_advertiser_state(conn, page_id: str, state: str, source: str) -> None:
    conn.execute(
        "UPDATE advertisers SET home_state = ?, home_state_source = ? WHERE page_id = ?",
        (state, source, page_id),
    )


def upsert_ad(conn, ad) -> None:
    """Insert or widen an ad record from an extract.Ad instance.

    Text fields use COALESCE(NULLIF(...)) so a later sweep that happens to
    return a sparser payload cannot blank out copy we already captured.
    """
    now = int(time.time())
    conn.execute(
        "INSERT INTO ads (ad_archive_id, page_id, page_name, title, body_text, "
        " caption, link_description, cta_type, cta_text, link_url, display_format, "
        " publisher_platforms, collation_id, start_date, end_date, ocr_text, "
        " first_seen, last_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(ad_archive_id) DO UPDATE SET "
        "  page_id             = COALESCE(NULLIF(excluded.page_id, ''), page_id), "
        "  page_name           = COALESCE(NULLIF(excluded.page_name, ''), page_name), "
        "  title               = COALESCE(NULLIF(excluded.title, ''), title), "
        "  body_text           = COALESCE(NULLIF(excluded.body_text, ''), body_text), "
        "  caption             = COALESCE(NULLIF(excluded.caption, ''), caption), "
        "  link_description    = COALESCE(NULLIF(excluded.link_description, ''), link_description), "
        "  cta_type            = COALESCE(NULLIF(excluded.cta_type, ''), cta_type), "
        "  cta_text            = COALESCE(NULLIF(excluded.cta_text, ''), cta_text), "
        "  link_url            = COALESCE(NULLIF(excluded.link_url, ''), link_url), "
        "  display_format      = COALESCE(NULLIF(excluded.display_format, ''), display_format), "
        "  publisher_platforms = COALESCE(excluded.publisher_platforms, publisher_platforms), "
        "  collation_id        = COALESCE(NULLIF(excluded.collation_id, ''), collation_id), "
        "  start_date          = COALESCE(excluded.start_date, start_date), "
        "  end_date            = COALESCE(excluded.end_date, end_date), "
        "  ocr_text            = COALESCE(NULLIF(excluded.ocr_text, ''), ocr_text), "
        "  last_seen           = excluded.last_seen",
        (
            ad.ad_archive_id, ad.page_id, ad.page_name, ad.title, ad.body_text,
            ad.caption, ad.link_description, ad.cta_type, ad.cta_text,
            ad.link_url, ad.display_format, _json(ad.publisher_platforms),
            ad.collation_id,
            int(ad.start_date.timestamp()) if ad.start_date else None,
            int(ad.end_date.timestamp()) if ad.end_date else None,
            ad.ocr_text, now, now,
        ),
    )


def record_snapshot(conn, sweep_id: int, ad) -> None:
    """One row per ad per sweep — the history spine."""
    conn.execute(
        "INSERT INTO ad_snapshots (ad_archive_id, sweep_id, is_active, "
        " collation_count, days_running) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(ad_archive_id, sweep_id) DO UPDATE SET "
        "  is_active = excluded.is_active, "
        "  collation_count = excluded.collation_count, "
        "  days_running = excluded.days_running",
        (
            ad.ad_archive_id, sweep_id,
            None if ad.is_active is None else int(bool(ad.is_active)),
            ad.collation_count, ad.days_running,
        ),
    )


def link_ad_target(conn, sweep_id: int, ad_archive_id: str, target_key: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO ad_targets (ad_archive_id, sweep_id, target_key) "
        "VALUES (?, ?, ?)",
        (ad_archive_id, sweep_id, target_key),
    )


def upsert_creative(conn, sha1: str, ad_archive_id: str, kind: str = "image",
                    source_url: str = "", local_path: str = "", ocr_text: str = "",
                    width: int | None = None, height: int | None = None,
                    size_bytes: int | None = None) -> None:
    conn.execute(
        "INSERT INTO creatives (sha1, ad_archive_id, kind, source_url, local_path, "
        " ocr_text, width, height, bytes) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(sha1) DO UPDATE SET "
        "  ocr_text = COALESCE(NULLIF(excluded.ocr_text, ''), ocr_text), "
        "  local_path = COALESCE(NULLIF(excluded.local_path, ''), local_path)",
        (sha1, ad_archive_id, kind, source_url, local_path, ocr_text,
         width, height, size_bytes),
    )


def record_states(conn, ad_archive_id: str, signals: list) -> None:
    """Persist every state signal that fired for an ad (see states.py)."""
    conn.executemany(
        "INSERT OR IGNORE INTO ad_states (ad_archive_id, state, signal, "
        " confidence, evidence) VALUES (?, ?, ?, ?, ?)",
        [(ad_archive_id, s.state, s.signal, s.confidence, s.evidence) for s in signals],
    )


def record_price(conn, ad_archive_id: str, sweep_id: int, source: str, *,
                 price_aud=None, capacity_kwh=None, system_kw=None,
                 dollars_per_kwh=None, dollars_per_kw=None, est_rebate_aud=None,
                 basis: str = "unknown", flag: str = "",
                 all_prices=None, all_kwh=None, all_kw=None) -> None:
    conn.execute(
        "INSERT INTO price_observations (ad_archive_id, sweep_id, source, price_aud, "
        " capacity_kwh, system_kw, dollars_per_kwh, dollars_per_kw, est_rebate_aud, "
        " basis, flag, all_prices, all_kwh, all_kw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(ad_archive_id, sweep_id, source) DO UPDATE SET "
        "  price_aud = excluded.price_aud, capacity_kwh = excluded.capacity_kwh, "
        "  system_kw = excluded.system_kw, dollars_per_kwh = excluded.dollars_per_kwh, "
        "  dollars_per_kw = excluded.dollars_per_kw, est_rebate_aud = excluded.est_rebate_aud, "
        "  basis = excluded.basis, flag = excluded.flag, all_prices = excluded.all_prices, "
        "  all_kwh = excluded.all_kwh, all_kw = excluded.all_kw",
        (ad_archive_id, sweep_id, source, price_aud, capacity_kwh, system_kw,
         dollars_per_kwh, dollars_per_kw, est_rebate_aud, basis, flag,
         _json(all_prices), _json(all_kwh), _json(all_kw)),
    )


def record_offer(conn, ad_archive_id: str, enrich_version: str, model: str,
                 data: dict) -> None:
    """Store one LLM extraction. Keyed by (ad, version) so re-running a newer
    prompt adds a row rather than destroying the earlier extraction."""
    conn.execute(
        "INSERT INTO ad_offers (ad_archive_id, enrich_version, model, product_category, "
        " brand, model_name, price_aud, price_basis, capacity_kwh, system_kw, offer_type, "
        " finance_terms, claimed_rebates, states_mentioned, cities_mentioned, "
        " urgency_tactics, warranty_years, install_included, cta, raw_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(ad_archive_id, enrich_version) DO UPDATE SET "
        "  model = excluded.model, product_category = excluded.product_category, "
        "  brand = excluded.brand, model_name = excluded.model_name, "
        "  price_aud = excluded.price_aud, price_basis = excluded.price_basis, "
        "  capacity_kwh = excluded.capacity_kwh, system_kw = excluded.system_kw, "
        "  offer_type = excluded.offer_type, finance_terms = excluded.finance_terms, "
        "  claimed_rebates = excluded.claimed_rebates, states_mentioned = excluded.states_mentioned, "
        "  cities_mentioned = excluded.cities_mentioned, urgency_tactics = excluded.urgency_tactics, "
        "  warranty_years = excluded.warranty_years, install_included = excluded.install_included, "
        "  cta = excluded.cta, raw_json = excluded.raw_json",
        (
            ad_archive_id, enrich_version, model,
            data.get("product_category"), data.get("brand"), data.get("model_name"),
            data.get("price_aud"), data.get("price_basis"), data.get("capacity_kwh"),
            data.get("system_kw"), data.get("offer_type"), data.get("finance_terms"),
            _json(data.get("claimed_rebates")), _json(data.get("states_mentioned")),
            _json(data.get("cities_mentioned")), _json(data.get("urgency_tactics")),
            data.get("warranty_years"),
            None if data.get("install_included") is None else int(bool(data.get("install_included"))),
            data.get("cta"), _json(data),
        ),
    )


def ads_needing_enrichment(conn, enrich_version: str, limit: int | None = None) -> list[sqlite3.Row]:
    sql = (
        "SELECT a.ad_archive_id, a.page_name, a.title, a.body_text, a.caption, "
        "       a.link_description, a.cta_text, a.ocr_text "
        "FROM ads a LEFT JOIN ad_offers o "
        "  ON o.ad_archive_id = a.ad_archive_id AND o.enrich_version = ? "
        "WHERE o.id IS NULL"
    )
    args: list = [enrich_version]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return conn.execute(sql, args).fetchall()
