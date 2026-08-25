"""Tests for the competitor watchlist and type-aware market statistics.

Run: python test_watchlist.py
"""

import os
import tempfile

import extract
import report_site
import store

INSTALLERS = ["PSC Energy", "Solaray Energy", "RACV Solar"]
MANUFACTURERS = ["SunPower", "JinkoSolar", "WINAICO", "Clenergy"]
PLATFORMS = ["SolarQuotes", "Solar Analytics", "Brighte"]


def _db():
    path = tempfile.mktemp(suffix=".db")
    conn = store.connect(path)
    return conn, path, store.begin_sweep(conn, "t", "advertiser")


def _ad(conn, sid, aid, adv, price=None, kw=None, basis=None, enriched=True):
    a = extract.Ad(ad_archive_id=aid, page_id="pg" + adv[:6].replace(" ", ""),
                   page_name=adv, body_text="6.6kW solar system", is_active=True)
    store.upsert_ad(conn, a)
    store.record_snapshot(conn, sid, a)
    if price is not None:
        store.record_price(conn, aid, sid, "regex", price_aud=price, system_kw=kw)
    if enriched:
        store.record_offer(conn, aid, "v1", "t", {
            "price_aud": price, "system_kw": kw, "price_basis": basis,
            "product_category": "solar"})


# --- Matching ------------------------------------------------------------

def test_watchlist_names_match_their_page_names():
    conn, path, sid = _db()
    for i, n in enumerate(["PSC Energy", "Solaray Energy", "RACV Solar",
                           "SunPower", "JinkoSolar Australia", "WINAICO Australia",
                           "Clenergy", "SolarQuotes", "Solar Analytics", "Brighte"]):
        _ad(conn, sid, f"a{i}", n)
    conn.commit()
    for label in INSTALLERS + MANUFACTURERS + PLATFORMS:
        assert store.match_advertiser_names(conn, label), f"{label} matched nothing"
    conn.close(); os.unlink(path)


def test_short_brand_name_does_not_swallow_a_longer_unrelated_one():
    """'Brighte' is a substring of 'Brighter Solar Solutions' but a different
    company. Word-boundary alignment is what prevents the false match."""
    conn, path, sid = _db()
    _ad(conn, sid, "a1", "Brighter Solar Solutions")
    _ad(conn, sid, "a2", "Brighte")
    conn.commit()
    assert store.match_advertiser_names(conn, "Brighte") == ["Brighte"]
    conn.close(); os.unlink(path)


def test_similar_names_are_not_confused():
    conn, path, sid = _db()
    for i, n in enumerate(["Solar Energy Group", "Solaris Power", "Energy Australia",
                           "Analytics Co", "Quotes R Us", "Clean Energy Council",
                           "NRG Solar"]):
        _ad(conn, sid, f"a{i}", n)
    conn.commit()
    for label in ["Solaray Energy", "Solar Analytics", "SolarQuotes",
                  "Clenergy", "RACV Solar"]:
        assert store.match_advertiser_names(conn, label) == [], \
            f"{label} wrongly matched {store.match_advertiser_names(conn, label)}"
    conn.close(); os.unlink(path)


def test_instagram_handles_resolve_to_page_names():
    """The watchlist is seeded from Instagram; the archive stores Page names."""
    conn, path, sid = _db()
    _ad(conn, sid, "a1", "Solaray Energy")
    _ad(conn, sid, "a2", "RACV Solar")
    conn.commit()
    assert store.match_advertiser_names(conn, "solarayenergy") == ["Solaray Energy"]
    assert store.match_advertiser_names(conn, "racv_solar") == ["RACV Solar"]
    conn.close(); os.unlink(path)


def test_partial_page_names_match():
    """'JinkoSolar' should find 'JinkoSolar Australia'."""
    conn, path, sid = _db()
    _ad(conn, sid, "a1", "JinkoSolar Australia")
    conn.commit()
    assert store.match_advertiser_names(conn, "JinkoSolar") == ["JinkoSolar Australia"]
    conn.close(); os.unlink(path)


# --- Market statistics ---------------------------------------------------

def _market_archive():
    """Three classified installers, two manufacturers, one platform, and five
    unclassified installers — the real shape, where most advertisers are untyped."""
    conn, path, sid = _db()
    for i, (adv, p) in enumerate(zip(INSTALLERS, [4200, 4600, 5100])):
        _ad(conn, sid, f"i{i}", adv, price=p, kw=6.6, basis="post_rebate")
        store.set_advertiser_type(conn, adv, adv, "installer")
    _ad(conn, sid, "m0", "SunPower", price=19500, kw=6.6, basis="post_rebate")
    store.set_advertiser_type(conn, "SunPower", "SunPower", "manufacturer")
    _ad(conn, sid, "m1", "JinkoSolar Australia", price=22000, kw=6.6, basis="post_rebate")
    store.set_advertiser_type(conn, "JinkoSolar Australia", "JinkoSolar", "manufacturer")
    _ad(conn, sid, "p0", "SolarQuotes", price=2000, kw=6.6, basis="post_rebate")
    store.set_advertiser_type(conn, "SolarQuotes", "SolarQuotes", "platform")
    for i, p in enumerate([4400, 4700, 4900, 5000, 5300]):
        _ad(conn, sid, f"u{i}", f"Local Solar {i}", price=p, kw=6.6, basis="post_rebate")
    conn.commit()
    return conn, path


def test_manufacturers_and_platforms_are_excluded_from_medians():
    conn, path = _market_archive()
    m = report_site.load_market(conn, "kw")
    s = next(c for c in m["configs"] if c["config"] == "6.6kW")["post"]["price"]
    assert s["max"] == 5300, f"a manufacturer price leaked in (max={s['max']})"
    assert s["min"] == 4200, f"a platform teaser leaked in (min={s['min']})"
    conn.close(); os.unlink(path)


def test_unclassified_advertisers_stay_in_the_medians():
    """The collapse-to-three failure mode: filtering to KNOWN installers would
    discard the ~700 untyped advertisers who make up the actual market."""
    conn, path = _market_archive()
    m = report_site.load_market(conn, "kw")
    s = next(c for c in m["configs"] if c["config"] == "6.6kW")["post"]["price"]
    assert s["n"] == 8, f"expected 3 classified + 5 unclassified installers, got {s['n']}"
    assert s["median"] == 4800
    conn.close(); os.unlink(path)


def test_exclusion_count_is_reported_not_silent():
    conn, path = _market_archive()
    m = report_site.load_market(conn, "kw")
    assert m["coverage"]["nonInstaller"] == 3
    conn.close(); os.unlink(path)


def test_excluded_advertisers_are_still_in_the_archive():
    """Excluded from the medians, not from the data — you still want to see
    what the brands and platforms are pushing."""
    conn, path = _market_archive()
    n = conn.execute("SELECT COUNT(*) FROM ad_market "
                     "WHERE advertiser_type IN ('manufacturer','platform')").fetchone()[0]
    assert n == 3
    conn.close(); os.unlink(path)


# --- Targeted enrichment -------------------------------------------------

def test_watchlist_only_selects_just_those_advertisers():
    conn, path, sid = _db()
    for i in range(4):
        _ad(conn, sid, f"w{i}", "PSC Energy", enriched=False)
    store.set_advertiser_type(conn, "PSC Energy", "PSC Energy", "installer")
    for i in range(6):
        _ad(conn, sid, f"o{i}", f"Random Co {i}", enriched=False)
    conn.commit()

    all_pending = store.ads_needing_enrichment(conn, "v1")
    watch_pending = store.ads_needing_enrichment(conn, "v1", watchlist_only=True)
    assert len(all_pending) == 10
    assert len(watch_pending) == 4
    assert {r["page_name"] for r in watch_pending} == {"PSC Energy"}
    conn.close(); os.unlink(path)


def test_resync_removes_entries_deleted_from_the_yaml():
    conn, path, sid = _db()
    _ad(conn, sid, "a1", "PSC Energy")
    store.set_advertiser_type(conn, "PSC Energy", "PSC Energy", "installer")
    conn.commit()
    assert len(store.watchlist_summary(conn)) == 1
    store.clear_watchlist(conn)
    conn.commit()
    assert len(store.watchlist_summary(conn)) == 0
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
