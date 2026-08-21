"""Tests for the static site generator.

Run: python test_site.py
"""

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import report_site
import store

FIXTURE_ADS = [
    ("501", "Adelaide Co", "p1", "Adelaide 13.5kWh battery $8,990. Home Battery Scheme."),
    ("502", "National Co", "p2", "We install across NSW, VIC, QLD, SA, WA and TAS."),
    ("503", "Quote Only", "p3", "Free consultation, no price given."),
]


def _archive():
    """Build a small archive on disk and return (conn, db_path)."""
    path = tempfile.mktemp(suffix=".db")
    conn = store.connect(path)
    sid = store.begin_sweep(conn, "t", "pilot")
    import extract
    for aid, name, pid, body in FIXTURE_ADS:
        ad = extract.Ad(ad_archive_id=aid, page_id=pid, page_name=name,
                        body_text=body, is_active=True, collation_count=2)
        store.upsert_ad(conn, ad)
        store.record_snapshot(conn, sid, ad)
        prices = extract.extract_prices(body, "battery")
        caps = extract.extract_capacities(body)
        if prices and caps:
            store.record_price(conn, aid, sid, "regex", price_aud=prices[0],
                               capacity_kwh=caps[0],
                               dollars_per_kwh=round(prices[0] / caps[0], 1))
        import states as st
        sigs = st.resolve(st.infer(body))
        if sigs:
            store.record_states(conn, aid, sigs)
    store.finish_sweep(conn, sid)
    conn.commit()
    return conn, path


def _build(images="none"):
    conn, db = _archive()
    conn.close()
    out = Path(tempfile.mkdtemp())
    res = report_site.build(db, out, images)
    return res, out, db


def test_emits_every_deploy_file():
    res, out, db = _build()
    for rel in ["public/index.html", "netlify.toml", "netlify/edge-functions/auth.ts"]:
        assert (out / rel).exists(), f"missing {rel}"
    shutil.rmtree(out); os.unlink(db)


def test_ad_count_matches_the_archive():
    """The site must not silently drop or duplicate ads."""
    conn, db = _archive()
    expected = conn.execute("SELECT COUNT(*) FROM ads").fetchone()[0]
    conn.close()
    out = Path(tempfile.mkdtemp())
    res = report_site.build(db, out, "none")
    assert res["ads"] == expected, f"{res['ads']} in site vs {expected} in archive"
    html = (out / "public" / "index.html").read_text()
    payload = json.loads(re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', html, re.S
    ).group(1).replace("<\\/", "</"))
    assert len(payload["ads"]) == expected
    shutil.rmtree(out); os.unlink(db)


def test_national_advertiser_is_not_labelled_a_local_one():
    """The ad citing six states must resolve to NATIONAL, not to one of them."""
    res, out, db = _build()
    html = (out / "public" / "index.html").read_text()
    payload = json.loads(re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', html, re.S
    ).group(1).replace("<\\/", "</"))
    ads = {a["id"]: a for a in payload["ads"]}
    assert ads["502"]["state"] == "NATIONAL"
    # And the ad with no location language must claim no state at all.
    assert ads["503"]["state"] == ""
    shutil.rmtree(out); os.unlink(db)


def test_inferred_state_caveat_is_always_present():
    """A shared link must never present inferred geography as reported fact."""
    res, out, db = _build()
    html = (out / "public" / "index.html").read_text()
    assert "inferred, not reported" in html
    assert "no geographic targeting" in html
    shutil.rmtree(out); os.unlink(db)


def test_payload_cannot_break_out_of_its_script_tag():
    """Ad copy containing </script> must not terminate the embedded JSON."""
    path = tempfile.mktemp(suffix=".db")
    conn = store.connect(path)
    import extract
    sid = store.begin_sweep(conn, "t", "pilot")
    ad = extract.Ad(ad_archive_id="600", page_id="p", page_name="XSS Co",
                    body_text="</script><script>alert(1)</script> 10kWh $5,000")
    store.upsert_ad(conn, ad)
    store.record_snapshot(conn, sid, ad)
    store.finish_sweep(conn, sid)
    conn.commit(); conn.close()

    out = Path(tempfile.mkdtemp())
    report_site.build(path, out, "none")
    html = (out / "public" / "index.html").read_text()
    body_start = html.index('<script id="payload"')
    body_end = html.index("</script>", body_start)
    embedded = html[body_start:body_end]
    assert "</script>" not in embedded.split(">", 1)[1], "payload escaped its script tag"
    shutil.rmtree(out); os.unlink(path)


def test_edge_function_fails_closed_and_covers_every_path():
    res, out, db = _build()
    auth = (out / "netlify" / "edge-functions" / "auth.ts").read_text()
    # Without a password the site must refuse to serve, not serve openly.
    assert "if (!expected)" in auth and "503" in auth
    # The gate has to cover assets, not just the HTML page.
    assert 'path: "/*"' in auth
    assert "401" in auth and "WWW-Authenticate" in auth
    shutil.rmtree(out); os.unlink(db)


def test_images_none_leaves_no_broken_references():
    res, out, db = _build(images="none")
    html = (out / "public" / "index.html").read_text()
    payload = json.loads(re.search(
        r'<script id="payload" type="application/json">(.*?)</script>', html, re.S
    ).group(1).replace("<\\/", "</"))
    assert all(not a["sha"] for a in payload["ads"]), "image refs left with no images"
    assert res["images"] == 0
    shutil.rmtree(out); os.unlink(db)


def test_noindex_is_set():
    """Even behind a password, the page should not invite indexing."""
    res, out, db = _build()
    html = (out / "public" / "index.html").read_text()
    assert 'name="robots"' in html and "noindex" in html
    assert "X-Robots-Tag" in (out / "netlify.toml").read_text()
    shutil.rmtree(out); os.unlink(db)


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
