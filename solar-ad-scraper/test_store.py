"""Archive round-trip tests: identity upserts, history, and isolation.

Run: python test_store.py
"""

import json
import os
import tempfile
import time

import extract
import store

FIXTURE = {
    "data": {"results": [{
        "ad_archive_id": "900", "is_active": True, "collation_count": 3,
        "collation_id": "c900", "start_date": int(time.time()) - 30 * 86400,
        "publisher_platform": ["FACEBOOK", "INSTAGRAM"],
        "snapshot": {
            "page_name": "Test Solar", "page_id": "pg1",
            "body": {"text": "Adelaide 13.5kWh battery $8,990"},
            "title": "Big deal", "caption": "cap", "cta_type": "LEARN_MORE",
            "cta_text": "Get a quote", "link_url": "https://x.test",
            "display_format": "IMAGE", "images": [],
        },
    }]}
}


def _fresh():
    path = tempfile.mktemp(suffix=".db")
    return store.connect(path), path


def test_schema_creates_every_table():
    conn, path = _fresh()
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ["ads", "advertisers", "ad_snapshots", "ad_states", "ad_offers",
              "creatives", "price_observations", "sweeps", "sweep_targets"]:
        assert t in names, f"missing table {t}"
    conn.close(); os.unlink(path)


def test_repeat_sweep_adds_history_not_duplicates():
    """The whole point of the archive: seeing the same ad twice must produce
    one ad row and two observations, so price changes stay visible."""
    conn, path = _fresh()
    ad = list(extract.parse_ad_nodes([json.dumps(FIXTURE)]).values())[0]
    for _ in range(2):
        sid = store.begin_sweep(conn, "t", "pilot")
        store.upsert_advertiser(conn, ad.page_id, ad.page_name)
        store.upsert_ad(conn, ad)
        store.record_snapshot(conn, sid, ad)
        store.record_price(conn, ad.ad_archive_id, sid, "regex", price_aud=8990)
        store.finish_sweep(conn, sid)
    assert conn.execute("SELECT COUNT(*) FROM ads").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ad_snapshots").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 2
    conn.close(); os.unlink(path)


def test_sparse_payload_does_not_erase_captured_copy():
    """A later sweep returning a thinner payload must not blank existing text."""
    conn, path = _fresh()
    ad = list(extract.parse_ad_nodes([json.dumps(FIXTURE)]).values())[0]
    store.upsert_ad(conn, ad)
    sparse = extract.Ad(ad_archive_id="900")  # everything empty
    store.upsert_ad(conn, sparse)
    row = conn.execute("SELECT body_text, title FROM ads WHERE ad_archive_id='900'").fetchone()
    assert "8,990" in row["body_text"] and row["title"] == "Big deal"
    conn.close(); os.unlink(path)


def test_resume_skips_completed_targets():
    conn, path = _fresh()
    sid = store.begin_sweep(conn, "t", "full")
    plan = [{"key": f"kw:k{i}", "kind": "keyword", "query": f"k{i}",
             "state": "", "category": "battery"} for i in range(3)]
    store.register_targets(conn, sid, plan)
    assert len(store.pending_targets(conn, sid)) == 3
    store.finish_target(conn, sid, "kw:k0", 5)
    store.finish_target(conn, sid, "kw:k1", 0, status="failed", error="boom")
    pending = {r["target_key"] for r in store.pending_targets(conn, sid)}
    # Done is skipped; failed is retried on resume.
    assert pending == {"kw:k1", "kw:k2"}
    # Re-registering the same plan must not reset progress.
    store.register_targets(conn, sid, plan)
    assert conn.execute(
        "SELECT status FROM sweep_targets WHERE target_key='kw:k0'"
    ).fetchone()["status"] == "done"
    conn.close(); os.unlink(path)


def test_state_signals_dedupe_across_sweeps():
    conn, path = _fresh()
    ad = list(extract.parse_ad_nodes([json.dumps(FIXTURE)]).values())[0]
    store.upsert_ad(conn, ad)
    import states
    sigs = states.resolve(states.infer(ad.all_text))
    for _ in range(3):
        store.record_states(conn, ad.ad_archive_id, sigs)
    assert conn.execute("SELECT COUNT(*) FROM ad_states").fetchone()[0] == len(sigs)
    conn.close(); os.unlink(path)


def test_enrichment_is_cached_by_version():
    conn, path = _fresh()
    ad = list(extract.parse_ad_nodes([json.dumps(FIXTURE)]).values())[0]
    store.upsert_ad(conn, ad)
    assert len(store.ads_needing_enrichment(conn, "v1")) == 1
    store.record_offer(conn, "900", "v1", "test-model", {"product_category": "battery"})
    assert len(store.ads_needing_enrichment(conn, "v1")) == 0
    # A new prompt version re-queues the ad without destroying the old record.
    assert len(store.ads_needing_enrichment(conn, "v2")) == 1
    conn.close(); os.unlink(path)


def test_archive_path_is_not_the_app_database():
    """A sweep must never be able to touch mission-control.db."""
    assert store.DEFAULT_DB.name == "solar-ads.db"


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
