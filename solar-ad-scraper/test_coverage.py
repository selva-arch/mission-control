"""Tests for the coverage gaps the NRG Solar case exposed.

Run: python test_coverage.py
"""

import os
import tempfile

import extract
import report_site
import store


def _db():
    path = tempfile.mktemp(suffix=".db")
    conn = store.connect(path)
    return conn, path, store.begin_sweep(conn, "t", "pilot")


# --- Catalogue / dynamic ads ----------------------------------------------

def test_placeholder_body_is_detected():
    assert extract.is_placeholder("{{product.name}} — {{product.brand}}")
    assert extract.is_placeholder("{{product.name}}")
    # Real copy that merely contains braces is not a placeholder.
    assert not extract.is_placeholder("Save {{now}} on a 13.5kWh battery for $8,990")
    assert not extract.is_placeholder("13.5kWh battery $8,990")
    assert not extract.is_placeholder("")


def test_catalogue_ad_falls_back_to_card_text():
    """The real offer in a dynamic ad lives in the per-product cards."""
    import json
    payload = json.dumps({"data": {"results": [{
        "ad_archive_id": "900", "is_active": True,
        "snapshot": {
            "page_name": "NRG Solar", "page_id": "pg1",
            "body": {"text": "{{product.name}} — {{product.brand}}"},
            "cards": [
                {"title": "Sungrow 13.5kWh", "body": "Installed from $8,990"},
                {"title": "{{product.name}}", "body": "{{product.price}}"},
            ],
        },
    }]}})
    ad = list(extract.parse_ad_nodes([payload]).values())[0]
    assert ad.is_dynamic is True
    assert "8,990" in ad.body_text, ad.body_text
    # Placeholder cards contribute nothing.
    assert "{{" not in ad.body_text
    # And the recovered copy is now parseable.
    assert extract.extract_prices(ad.all_text, "battery") == [8990]


def test_normal_ad_is_not_flagged_dynamic():
    import json
    payload = json.dumps({"data": {"results": [{
        "ad_archive_id": "901", "is_active": True,
        "snapshot": {"page_name": "Normal Co", "page_id": "pg2",
                     "body": {"text": "13.5kWh battery $8,990 installed"}},
    }]}})
    ad = list(extract.parse_ad_nodes([payload]).values())[0]
    assert ad.is_dynamic is False
    assert ad.body_text == "13.5kWh battery $8,990 installed"


def test_unresolved_catalogue_ad_stays_out_of_market_stats():
    """A placeholder that never resolved must not reach a median."""
    conn, path, sid = _db()
    for i, p in enumerate([4000, 4500, 5000, 5500, 6000]):
        a = extract.Ad(ad_archive_id=f"ok{i}", page_id="p", page_name=f"Real{i}")
        store.upsert_ad(conn, a); store.record_snapshot(conn, sid, a)
        store.record_price(conn, f"ok{i}", sid, "regex", price_aud=p, system_kw=6.6)
        store.record_offer(conn, f"ok{i}", "v1", "t",
                           {"price_aud": p, "system_kw": 6.6,
                            "price_basis": "post_rebate"})
    ghost = extract.Ad(ad_archive_id="dyn", page_id="p9", page_name="Catalogue Co",
                       body_text="{{product.name}}", is_dynamic=True)
    store.upsert_ad(conn, ghost); store.record_snapshot(conn, sid, ghost)
    store.record_price(conn, "dyn", sid, "regex", price_aud=99000, system_kw=6.6)
    conn.commit()

    row = conn.execute("SELECT suspect FROM ad_market WHERE ad_archive_id='dyn'").fetchone()
    assert row["suspect"] == 1, "unresolved catalogue ad should be suspect"
    m = report_site.load_market(conn, "kw")
    cfg = next(c for c in m["configs"] if c["config"] == "6.6kW")
    assert cfg["post"]["price"]["max"] == 6000, "the 99,000 placeholder leaked in"
    assert cfg["total"] == 5
    conn.close(); os.unlink(path)


def test_catalogue_ad_with_a_real_price_is_kept():
    """Flagging must not discard a dynamic ad whose offer did resolve."""
    conn, path, sid = _db()
    a = extract.Ad(ad_archive_id="dyn2", page_id="p", page_name="Catalogue Co",
                   body_text="Sungrow 13.5kWh installed from $8,990", is_dynamic=True)
    store.upsert_ad(conn, a); store.record_snapshot(conn, sid, a)
    store.record_price(conn, "dyn2", sid, "regex", price_aud=8990, capacity_kwh=13.5)
    store.record_offer(conn, "dyn2", "v1", "t",
                       {"price_aud": 8990, "capacity_kwh": 13.5})
    conn.commit()
    row = conn.execute("SELECT suspect, price FROM ad_market "
                       "WHERE ad_archive_id='dyn2'").fetchone()
    assert row["suspect"] == 0 and row["price"] == 8990
    conn.close(); os.unlink(path)


# --- Scroll coverage -------------------------------------------------------

def test_scroll_coverage_is_recorded_per_target():
    conn, path, sid = _db()
    store.register_targets(conn, sid, [
        {"key": "kw:a", "kind": "keyword", "query": "a", "state": "", "category": "battery"},
        {"key": "kw:b", "kind": "keyword", "query": "b", "state": "", "category": "battery"},
    ])
    store.finish_target(conn, sid, "kw:a", 120, coverage="exhausted")
    store.finish_target(conn, sid, "kw:b", 297, coverage="truncated")
    got = {r["target_key"]: r["coverage"] for r in
           conn.execute("SELECT target_key, coverage FROM sweep_targets "
                        "WHERE sweep_id = ?", (sid,))}
    assert got == {"kw:a": "exhausted", "kw:b": "truncated"}
    conn.close(); os.unlink(path)


# --- Resume ----------------------------------------------------------------

def test_resume_matches_the_requested_mode():
    """An abandoned pilot must not hijack an advertiser sweep's resume."""
    conn, path, _ = _db()
    stale = store.begin_sweep(conn, "cfg", "pilot")      # left running
    adv = store.begin_sweep(conn, "cfg", "advertiser")   # the one we want
    assert store.latest_unfinished_sweep(conn, "advertiser") == adv
    assert store.latest_unfinished_sweep(conn, "pilot") == stale
    # Without a mode the newest still wins, preserving the old behaviour.
    assert store.latest_unfinished_sweep(conn) == adv
    conn.close(); os.unlink(path)


def test_added_columns_migrate_an_existing_archive():
    """A months-old archive must gain new columns without a rebuild.

    Rather than fabricate an old schema, build a current one and strip the two
    columns back off — that exercises the real migration path.
    """
    import sqlite3
    path = tempfile.mktemp(suffix=".db")
    conn = store.connect(path)
    conn.close()

    raw = sqlite3.connect(path)
    # The view depends on ads.is_dynamic, so it goes first; store.connect()
    # rebuilds it on the next open.
    raw.executescript("""
        DROP VIEW IF EXISTS ad_market;
        ALTER TABLE ads DROP COLUMN is_dynamic;
        ALTER TABLE sweep_targets DROP COLUMN coverage;
    """)
    raw.commit()
    assert "is_dynamic" not in {r[1] for r in raw.execute("PRAGMA table_info(ads)")}
    raw.close()

    conn = store.connect(path)
    ads_cols = {r[1] for r in conn.execute("PRAGMA table_info(ads)")}
    tgt_cols = {r[1] for r in conn.execute("PRAGMA table_info(sweep_targets)")}
    assert "is_dynamic" in ads_cols, ads_cols
    assert "coverage" in tgt_cols, tgt_cols
    # The view must come back too, or every market query breaks.
    assert conn.execute("SELECT COUNT(*) FROM ad_market").fetchone()[0] == 0
    conn.close(); os.unlink(path)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except AssertionError:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
