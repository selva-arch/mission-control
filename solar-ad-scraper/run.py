#!/usr/bin/env python3
"""
Solar Ad Intelligence — sweep the Meta Ad Library (Australia) for solar,
battery, EV-charger and heat-pump ads; archive every ad, creative and offer
into SQLite; infer which states each ad targets; and rank offers by value.

Usage:
    python run.py --pilot              # small sweep to validate the pipeline
    python run.py                      # full sweep (hours; resumable)
    python run.py --resume             # continue the last unfinished sweep
    python run.py --advertisers        # second stage: all ads per known Page
    python run.py --enrich-only        # LLM pass over already-collected ads
    python run.py --keyword "Sungrow"  # one ad-hoc search
    python run.py --csv                # also export a flat CSV

First run opens a browser; log into Facebook once and the session is reused.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

import enrich
import extract
import normalize
import scraper
import states as states_mod
import store
import targets as targets_mod

HERE = Path(__file__).parent
DATA_DIR = HERE.parent / ".data"
CREATIVES_DIR = DATA_DIR / "solar-ads" / "creatives"


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def config_hash(cfg: dict) -> str:
    return hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def download_creative(url: str, dest_dir: Path) -> tuple[Path | None, str]:
    """Fetch one creative, named by content hash. Returns (path, sha1).

    Facebook CDN URLs expire, so the local copy is the only durable archive.
    Hashing the URL keeps the filename stable across runs so re-sweeps skip
    files already on disk.
    """
    import requests

    dest_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha1(url.encode()).hexdigest()
    dest = dest_dir / f"{sha[:16]}.jpg"
    if dest.exists():
        return dest, sha
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest, sha
    except Exception:
        return None, sha


def process_ad(conn, cfg: dict, sweep_id: int, ad, target,
               reocr: bool = False) -> None:
    """Persist one ad: identity, creatives+OCR, prices, and state signals."""
    category = (target["category"] if target else "") or "mixed"

    store.upsert_advertiser(conn, ad.page_id, ad.page_name)
    store.upsert_ad(conn, ad)
    store.record_snapshot(conn, sweep_id, ad)
    if target:
        store.link_ad_target(conn, sweep_id, ad.ad_archive_id, target["target_key"])

    # --- creatives + OCR ---------------------------------------------------
    if cfg.get("ocr_enabled", True):
        ocr_chunks: list[str] = []
        cap = cfg.get("max_images_per_ad", 2)
        images = ad.image_urls[:cap]
        posters = ad.video_poster_urls[:max(0, cap - len(images))]
        for url, kind in [(u, "image") for u in images] + \
                         [(u, "video_poster") for u in posters]:
            local, sha = download_creative(url, CREATIVES_DIR)
            if not local:
                continue
            # The same ad surfaces under many search terms, and OCR is the most
            # expensive step in a sweep. Reuse text we already extracted for
            # this exact creative unless a re-OCR was explicitly asked for
            # (e.g. Tesseract was installed after an earlier sweep ran).
            text = None if reocr else store.get_creative_ocr(conn, sha)
            if text is None:
                text = extract.ocr_image(str(local))
            if text:
                ocr_chunks.append(text)
            store.upsert_creative(
                conn, sha, ad.ad_archive_id, kind=kind, source_url=url,
                local_path=local.name, ocr_text=text,
                size_bytes=local.stat().st_size if local.exists() else None,
            )
        if ocr_chunks:
            ad.ocr_text = "\n".join(ocr_chunks)
            store.upsert_ad(conn, ad)  # write OCR text back onto the ad

    text = ad.all_text
    # A per-advertiser sweep returns everything a Page runs, so the search
    # category is meaningless there — classify from the copy instead.
    if category == "mixed":
        category = extract.classify_category(text)

    # --- prices ------------------------------------------------------------
    rebate = normalize.RebateModel(**(cfg.get("rebate") or {}))
    prices = extract.extract_prices(text, category)
    caps = extract.extract_capacities(text)
    kws = extract.extract_system_sizes(text)
    cmp = normalize.compare(prices, caps, rebate)

    dpk = cmp.dollars_per_kwh
    price = cmp.price
    dollars_per_kw = None
    if price and kws:
        dollars_per_kw = round(price / max(kws), 1)

    flag = ""
    lo, hi = extract.DOLLARS_PER_KWH_BAND
    if dpk and not (lo <= dpk <= hi):
        flag = "check-parse"
    klo, khi = extract.DOLLARS_PER_KW_BAND
    if dollars_per_kw and not (klo <= dollars_per_kw <= khi):
        flag = flag or "check-parse-kw"

    store.record_price(
        conn, ad.ad_archive_id, sweep_id, "regex",
        price_aud=price, capacity_kwh=cmp.capacity_kwh,
        system_kw=max(kws) if kws else None,
        dollars_per_kwh=dpk, dollars_per_kw=dollars_per_kw,
        est_rebate_aud=cmp.est_rebate, flag=flag,
        all_prices=prices, all_kwh=caps, all_kw=kws,
    )

    # --- state inference ---------------------------------------------------
    row = conn.execute(
        "SELECT home_state FROM advertisers WHERE page_id = ?", (ad.page_id,)
    ).fetchone() if ad.page_id else None
    signals = states_mod.resolve(states_mod.infer(
        text,
        query_state=(target["state"] if target else "") or "",
        advertiser_state=(row["home_state"] if row and row["home_state"] else ""),
    ))
    if signals:
        store.record_states(conn, ad.ad_archive_id, signals)


def run_sweep(conn, cfg: dict, sweep_id: int, target_rows: list,
              reocr: bool = False) -> None:
    """Drive the browser through every pending target, committing as we go.

    Each target is committed on completion so an interrupted sweep resumes
    from the last finished query rather than starting over.
    """
    total = len(target_rows)
    with scraper.Session(
        country=cfg.get("country", "AU"),
        headless=cfg.get("headless", False),
        active_status=cfg.get("active_status", "all"),
    ) as session:
        for i, target in enumerate(target_rows, 1):
            label = target["query"]
            print(f"[{i}/{total}] {target['kind']}: {label} "
                  f"({target['state'] or 'national'})")
            store.start_target(conn, sweep_id, target["target_key"])
            try:
                if target["kind"] == "advertiser":
                    bodies = session.fetch_page(
                        target["query"], cfg.get("max_scrolls", 25))
                else:
                    bodies = session.fetch_keyword(
                        target["query"], cfg.get("max_scrolls", 25))
                ads = extract.parse_ad_nodes(bodies)
                for ad in ads.values():
                    process_ad(conn, cfg, sweep_id, ad, target, reocr=reocr)
                conn.commit()
                coverage = getattr(session, "last_scroll_outcome", "")
                store.finish_target(conn, sweep_id, target["target_key"], len(ads),
                                    coverage=coverage)
                note = ""
                if coverage == "truncated":
                    note = (f"  ← hit the {cfg.get('max_scrolls', 25)}-scroll limit; "
                            f"more ads exist for this query")
                print(f"      {len(ads)} ads{note}")
            except KeyboardInterrupt:
                conn.commit()
                print("\n[abort] interrupted — re-run with --resume to continue")
                raise
            except Exception as e:
                conn.commit()
                store.finish_target(conn, sweep_id, target["target_key"],
                                    0, status="failed", error=str(e))
                print(f"      ! failed: {e}")


def sync_watchlist(conn, path: Path, verbose: bool = True) -> dict:
    """Match watchlist.yaml entries against the archive and classify them.

    Returns a summary dict. The report is the point of this command: it runs
    before any money is spent on enrichment, and an entry that matched nothing
    is named rather than silently skipped — a global brand may run no AU ads at
    all, but a missing local competitor means the sweep has a gap.
    """
    if not path.exists():
        print(f"[watchlist] {path} not found")
        return {}

    with open(path) as f:
        spec = yaml.safe_load(f) or {}

    # Section name -> the singular type stored per advertiser.
    types = {"installers": "installer", "manufacturers": "manufacturer",
             "platforms": "platform"}

    store.clear_watchlist(conn)
    matched, unmatched = {}, []
    for section, advertiser_type in types.items():
        for label in spec.get(section) or []:
            names = store.match_advertiser_names(conn, label)
            if not names:
                unmatched.append((label, advertiser_type))
                continue
            for name in names:
                store.set_advertiser_type(conn, name, label, advertiser_type)
            matched[label] = (advertiser_type, names)
    conn.commit()

    if verbose:
        _print_watchlist_report(conn, matched, unmatched)
    return {"matched": matched, "unmatched": unmatched}


def _print_watchlist_report(conn, matched: dict, unmatched: list) -> None:
    rows = store.watchlist_summary(conn)
    by_label: dict = {}
    for r in rows:
        by_label.setdefault(r["label"], []).append(r)

    print("=" * 68)
    order = ["installer", "manufacturer", "platform"]
    headings = {
        "installer": "INSTALLERS — competitors; their prices feed the market medians",
        "manufacturer": "MANUFACTURERS — suppliers; excluded from price medians",
        "platform": "PLATFORMS — not selling systems; excluded from price medians",
    }
    for kind in order:
        labels = [lbl for lbl, (t, _) in matched.items() if t == kind]
        if not labels:
            continue
        print(f"\n  {headings[kind]}")
        for label in sorted(labels):
            for r in by_label.get(label, []):
                pending = r["ads"] - r["enriched"]
                print(f"    {label:<18} → {r['page_name'][:34]:<34} "
                      f"{r['ads']:>5} ads, {pending:>5} pending enrichment")

    if unmatched:
        print(f"\n  NOT FOUND IN THE ARCHIVE ({len(unmatched)}):")
        for label, kind in unmatched:
            note = ("expected — global brands often run no AU ads"
                    if kind in ("manufacturer", "platform")
                    else "worth checking — a local competitor should be here")
            print(f"    {label:<18} ({kind}) — {note}")

    total_ads = sum(r["ads"] for r in rows)
    pending = sum(r["ads"] - r["enriched"] for r in rows)
    print(f"\n  {len(rows)} advertiser pages matched, {total_ads} ads, "
          f"{pending} pending enrichment")
    print(f"  Next: python run.py --enrich-submit --watchlist")
    print("=" * 68)


def export_csv(conn, out_path: Path) -> int:
    """Flat export of the newest reading per ad, best value first."""
    rows = conn.execute("""
        SELECT a.ad_archive_id, a.page_name, a.title, a.body_text, a.link_url,
               p.price_aud, p.capacity_kwh, p.system_kw, p.dollars_per_kwh,
               p.dollars_per_kw, p.est_rebate_aud, p.flag,
               s.is_active, s.collation_count, s.days_running,
               (SELECT GROUP_CONCAT(DISTINCT state) FROM ad_states
                 WHERE ad_archive_id = a.ad_archive_id
                   AND confidence = 'high')                    AS states_high,
               (SELECT product_category FROM ad_offers
                 WHERE ad_archive_id = a.ad_archive_id
                 ORDER BY id DESC LIMIT 1)                     AS category
        FROM ads a
        LEFT JOIN price_observations p
               ON p.ad_archive_id = a.ad_archive_id AND p.source = 'regex'
              AND p.id = (SELECT MAX(id) FROM price_observations
                           WHERE ad_archive_id = a.ad_archive_id AND source='regex')
        LEFT JOIN ad_snapshots s
               ON s.ad_archive_id = a.ad_archive_id
              AND s.id = (SELECT MAX(id) FROM ad_snapshots
                           WHERE ad_archive_id = a.ad_archive_id)
        ORDER BY (p.dollars_per_kwh IS NULL), p.dollars_per_kwh
    """).fetchall()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        if not rows:
            f.write("no data\n")
            return 0
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        for r in rows:
            w.writerow(dict(r))
    return len(rows)


def print_summary(conn, sweep_id: int) -> None:
    def scalar(sql, args=()):
        return conn.execute(sql, args).fetchone()[0]

    print("\n" + "=" * 60)
    print(f"  ads archived      : {scalar('SELECT COUNT(*) FROM ads')}")
    print(f"  advertisers       : {scalar('SELECT COUNT(*) FROM advertisers')}")
    print(f"  creatives stored  : {scalar('SELECT COUNT(*) FROM creatives')}")
    print(f"  seen this sweep   : "
          f"{scalar('SELECT COUNT(*) FROM ad_snapshots WHERE sweep_id=?', (sweep_id,))}")

    truncated = conn.execute(
        "SELECT COUNT(*) FROM sweep_targets WHERE sweep_id = ? AND coverage = 'truncated'",
        (sweep_id,)).fetchone()[0]
    if truncated:
        print(f"\n  ⚠ {truncated} queries hit the scroll limit — those results are "
              f"partial.\n    Raise max_scrolls in config.yaml and re-run to go deeper.")

    print("\n  high-confidence state signals:")
    for r in conn.execute(
        "SELECT state, COUNT(DISTINCT ad_archive_id) n FROM ad_states "
        "WHERE confidence='high' GROUP BY state ORDER BY n DESC"
    ):
        print(f"    {r['state']:<9} {r['n']}")

    # Latest reading per ad only — otherwise an ad seen in N sweeps appears
    # N times in the ranking.
    top = conn.execute("""
        SELECT a.page_name, p.dollars_per_kwh, p.capacity_kwh, p.price_aud
        FROM price_observations p JOIN ads a USING (ad_archive_id)
        WHERE p.id = (SELECT MAX(id) FROM price_observations
                       WHERE ad_archive_id = p.ad_archive_id AND source = p.source)
          AND p.source = 'regex'
          AND p.dollars_per_kwh IS NOT NULL AND COALESCE(p.flag,'') = ''
        ORDER BY p.dollars_per_kwh LIMIT 5
    """).fetchall()
    if top:
        print("\n  best $/usable kWh:")
        for r in top:
            print(f"    {r['dollars_per_kwh']:>7} $/kWh  {r['capacity_kwh']:>6} kWh  "
                  f"${r['price_aud']:<8} {(r['page_name'] or '')[:32]}")
    print("=" * 60)


def main() -> int:
    ap = argparse.ArgumentParser(description="Meta Ad Library solar sweep")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--keyword", help="run a single ad-hoc keyword search")
    ap.add_argument("--pilot", action="store_true",
                    help="small validation sweep (a few keywords, 2 states)")
    ap.add_argument("--resume", action="store_true",
                    help="continue the most recent unfinished sweep")
    ap.add_argument("--advertisers", action="store_true",
                    help="sweep every ad from each discovered advertiser Page")
    ap.add_argument("--enrich-only", action="store_true",
                    help="run the LLM extraction synchronously, no scraping "
                         "(fine for a few hundred ads; use --enrich-submit "
                         "for a full sweep)")
    ap.add_argument("--enrich-submit", action="store_true",
                    help="submit every pending ad as one Message Batch "
                         "(async, ~50%% cheaper, no need to keep this machine "
                         "running — collect later with --enrich-fetch)")
    ap.add_argument("--enrich-fetch", action="store_true",
                    help="collect results for the open batch, if it has finished")
    ap.add_argument("--enrich-audit", nargs="?", type=int, const=-1, default=None,
                    metavar="N",
                    help="adversarially check a sample of extractions on "
                         "claude-fable-5 (default from config.yaml, else 200)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip the LLM extraction step")
    ap.add_argument("--watchlist-sync", action="store_true",
                    help="match watchlist.yaml against the archive and classify "
                         "advertisers, then exit (no API cost)")
    ap.add_argument("--watchlist", action="store_true",
                    help="restrict enrichment to watchlist advertisers only")
    ap.add_argument("--sweeps", action="store_true",
                    help="list sweeps and their status, then exit")
    ap.add_argument("--reocr", action="store_true",
                    help="re-run OCR on creatives already processed "
                         "(use after installing Tesseract)")
    ap.add_argument("--csv", action="store_true", help="also write a flat CSV")
    ap.add_argument("--db", default=None, help="override database path")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    conn = store.connect(args.db)
    enrich_cfg = cfg.get("enrich") or {}
    model = enrich_cfg.get("model", enrich.DEFAULT_MODEL)
    audit_model = enrich_cfg.get("audit_model", "claude-fable-5")
    audit_sample = enrich_cfg.get("audit_sample", 200)

    if args.watchlist_sync:
        sync_watchlist(conn, HERE / "watchlist.yaml")
        conn.close()
        return 0

    if args.sweeps:
        rows = conn.execute(
            "SELECT sweep_id, mode, status, targets_done, targets_total, ads_seen, "
            "       started_at FROM sweeps ORDER BY sweep_id DESC LIMIT 20").fetchall()
        print(f"{'id':>4}  {'mode':<10} {'status':<9} {'progress':<12} {'ads':>7}  started")
        for r in rows:
            when = datetime.fromtimestamp(r["started_at"]).strftime("%Y-%m-%d %H:%M")
            prog = f"{r['targets_done']}/{r['targets_total']}"
            print(f"{r['sweep_id']:>4}  {r['mode']:<10} {r['status']:<9} {prog:<12} "
                  f"{r['ads_seen']:>7}  {when}")
        print("\nA sweep left at 'running' was interrupted; --resume picks up the "
              "newest one matching the mode you ask for.")
        conn.close()
        return 0

    # --- enrichment paths ---------------------------------------------------
    if args.enrich_only:
        n = enrich.enrich_pending(conn, store, model=model,
                                  watchlist_only=args.watchlist)
        print(f"[enrich] {n} ads enriched")
        conn.close()
        return 0

    if args.enrich_submit:
        enrich.submit_batch(conn, store, model=model,
                            watchlist_only=args.watchlist)
        conn.close()
        return 0

    if args.enrich_fetch:
        enrich.fetch_batch(conn, store)
        conn.close()
        return 0

    if args.enrich_audit is not None:
        n = audit_sample if args.enrich_audit == -1 else args.enrich_audit
        enrich.run_audit(conn, store, n=n, model=audit_model,
                         watchlist_only=args.watchlist)
        conn.close()
        return 0

    # --- pick or create the sweep -----------------------------------------
    mode = "pilot" if args.pilot else ("advertiser" if args.advertisers else "full")
    sweep_id = None
    if args.resume:
        # Match the mode being run. Without this an abandoned sweep left marked
        # running — an interrupted pilot, say — would be resumed instead of the
        # advertiser sweep actually being asked for.
        sweep_id = store.latest_unfinished_sweep(conn, mode)
        if sweep_id:
            print(f"[resume] continuing sweep {sweep_id}")
        else:
            print("[resume] no unfinished sweep found — starting a new one")
    if sweep_id is None:
        sweep_id = store.begin_sweep(conn, config_hash(cfg), mode)

    # --- build the target list --------------------------------------------
    if args.keyword:
        plan = [{"key": f"kw:{args.keyword}", "kind": "keyword",
                 "query": args.keyword, "state": "", "category": "mixed"}]
    elif args.advertisers:
        pages = conn.execute(
            "SELECT page_id, page_name FROM advertisers WHERE page_id != '' "
            "ORDER BY last_seen DESC"
        ).fetchall()
        plan = targets_mod.advertiser_targets([(r["page_id"], r["page_name"])
                                               for r in pages])
        if not plan:
            print("No advertisers known yet — run a keyword sweep first.")
            conn.close()
            return 1
    else:
        plan = targets_mod.keyword_targets(pilot=args.pilot)

    store.register_targets(conn, sweep_id, plan)
    pending = store.pending_targets(conn, sweep_id)
    print(f"[plan] {targets_mod.summarise(plan)}")
    print(f"[plan] {len(pending)} still to do\n")

    started = time.time()
    aborted = False
    try:
        run_sweep(conn, cfg, sweep_id, pending, reocr=args.reocr)
    except KeyboardInterrupt:
        aborted = True

    store.finish_sweep(conn, sweep_id, "aborted" if aborted else "done")

    if not aborted and not args.no_enrich:
        enrich.enrich_pending(conn, store, model=model)

    print_summary(conn, sweep_id)
    print(f"\n[time] {int(time.time() - started)}s")

    if args.csv:
        out = HERE / cfg.get("output", "out/solar-ads.csv")
        print(f"[write] {export_csv(conn, out)} rows -> {out}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
