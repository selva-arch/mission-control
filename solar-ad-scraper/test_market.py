"""Market-analysis tests: bucketing, basis separation, suspect exclusion.

Run: python test_market.py
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


def _ad(conn, sid, aid, adv, kw=None, kwh=None, price=None, basis=None,
        flag="", offer=True):
    a = extract.Ad(ad_archive_id=aid, page_id="pg" + adv, page_name=adv,
                   is_active=True)
    store.upsert_ad(conn, a)
    store.record_snapshot(conn, sid, a)
    store.record_price(conn, aid, sid, "regex", price_aud=price,
                       capacity_kwh=kwh, system_kw=kw, flag=flag)
    if offer:
        store.record_offer(conn, aid, "v1", "test", {
            "price_aud": price, "capacity_kwh": kwh, "system_kw": kw,
            "price_basis": basis})


def test_bands_are_contiguous_across_the_residential_range():
    """A gap would silently drop real ads from the medians."""
    conn, path, sid = _db()
    sizes = [2.0, 3.0, 4.5, 5.9, 6.0, 6.6, 7.2, 7.4, 9.0, 9.4, 11.5, 13.2,
             14.0, 15.0, 17.0, 17.9, 25.0, 40.0]
    for i, kw in enumerate(sizes):
        _ad(conn, sid, f"k{i}", f"A{i}", kw=kw, price=5000)
    conn.commit()
    rows = conn.execute("SELECT kw, config_kw FROM ad_market "
                        "WHERE kw IS NOT NULL").fetchall()
    unbucketed = [r["kw"] for r in rows if r["config_kw"] is None]
    assert not unbucketed, f"in-range sizes left unbucketed: {unbucketed}"
    conn.close(); os.unlink(path)


def test_known_sizes_land_in_the_expected_bucket():
    conn, path, sid = _db()
    cases = [(6.6, "6.6kW"), (6.95, "6.6kW"), (7.4, "8kW"), (9.4, "10kW"),
             (13.2, "13.2kW"), (20.0, "20kW+")]
    for i, (kw, _) in enumerate(cases):
        _ad(conn, sid, f"k{i}", f"A{i}", kw=kw, price=5000)
    conn.commit()
    got = {r["kw"]: r["config_kw"] for r in
           conn.execute("SELECT kw, config_kw FROM ad_market WHERE kw IS NOT NULL")}
    for kw, want in cases:
        assert got[kw] == want, f"{kw}kW -> {got[kw]}, expected {want}"
    conn.close(); os.unlink(path)


def test_out_of_range_sizes_are_not_forced_into_a_bucket():
    conn, path, sid = _db()
    _ad(conn, sid, "tiny", "Tiny", kw=1.2, price=900)
    _ad(conn, sid, "huge", "Huge", kw=95.0, price=90000)
    _ad(conn, sid, "bigb", "BigBatt", kwh=200.0, price=99000)
    conn.commit()
    for r in conn.execute("SELECT config_kw, config_kwh FROM ad_market"):
        assert r["config_kw"] is None and r["config_kwh"] is None
    conn.close(); os.unlink(path)


def test_rebate_bases_are_never_blended():
    """The central rule. A pre-rebate and a post-rebate price describe
    different things; one median across both describes neither."""
    conn, path, sid = _db()
    for i, p in enumerate([4000, 4500, 5000]):
        _ad(conn, sid, f"po{i}", f"Post{i}", kw=6.6, price=p, basis="post_rebate")
    for i, p in enumerate([9000, 10000]):
        _ad(conn, sid, f"pr{i}", f"Pre{i}", kw=6.6, price=p, basis="pre_rebate")
    conn.commit()
    m = report_site.load_market(conn, "kw")
    cfg = next(c for c in m["configs"] if c["config"] == "6.6kW")
    assert cfg["post"]["price"]["median"] == 4500
    assert cfg["pre"]["price"]["median"] == 9500
    # The blended median would be 5000 — it must appear nowhere.
    assert cfg["unknown"]["price"]["n"] == 0
    conn.close(); os.unlink(path)


def test_advertisers_are_compared_against_their_own_basis():
    """Measuring a pre-rebate price against a post-rebate median would recreate
    exactly the comparison the split exists to prevent."""
    conn, path, sid = _db()
    for i, p in enumerate([4000, 4500, 5000]):
        _ad(conn, sid, f"po{i}", f"Post{i}", kw=6.6, price=p, basis="post_rebate")
    for i, p in enumerate([9000, 10000]):
        _ad(conn, sid, f"pr{i}", f"Pre{i}", kw=6.6, price=p, basis="pre_rebate")
    conn.commit()
    m = report_site.load_market(conn, "kw")
    pre = [p for p in m["positions"] if p["basis"] == "pre_rebate"]
    # 9000 against the pre-rebate median of 9500 is -5%, not +100% against 4500.
    assert {p["vsMedian"] for p in pre} == {-5, 5}, [p["vsMedian"] for p in pre]
    conn.close(); os.unlink(path)


def test_suspect_parses_are_excluded_from_every_statistic():
    conn, path, sid = _db()
    for i, p in enumerate([4000, 4500, 5000]):
        _ad(conn, sid, f"ok{i}", f"Ok{i}", kw=6.6, price=p, basis="post_rebate")
    # A rebate amount misread as a price, with no LLM value to supersede it.
    _ad(conn, sid, "bad", "Misparse", kw=6.6, price=40000, flag="check-parse",
        offer=False)
    conn.commit()
    m = report_site.load_market(conn, "kw")
    cfg = next(c for c in m["configs"] if c["config"] == "6.6kW")
    assert cfg["total"] == 3, "suspect row leaked into the configuration"
    assert cfg["post"]["price"]["max"] == 5000
    assert m["coverage"]["suspect"] == 1, "exclusions must still be reported"
    conn.close(); os.unlink(path)


def test_llm_value_supersedes_a_flagged_regex_read():
    """The AF Electrical case: regex took 'scalable up to 42kWh'; the ad says
    14kWh for $3,999."""
    conn, path, sid = _db()
    _ad(conn, sid, "af", "AF Electrical", kwh=42.0, price=3999, flag="check-parse")
    conn.execute("UPDATE ad_offers SET capacity_kwh = 14.0 WHERE ad_archive_id='af'")
    conn.commit()
    r = conn.execute("SELECT kwh, per_kwh, suspect, config_kwh, price_source "
                     "FROM ad_market WHERE ad_archive_id='af'").fetchone()
    assert r["kwh"] == 14.0 and r["price_source"] == "llm"
    assert r["suspect"] == 0, "flag should clear once a real reading replaces it"
    assert round(r["per_kwh"]) == 286
    conn.close(); os.unlink(path)


def test_small_samples_are_marked_indicative():
    conn, path, sid = _db()
    for i, p in enumerate([4000, 4500]):
        _ad(conn, sid, f"a{i}", f"A{i}", kw=6.6, price=p, basis="post_rebate")
    for i, p in enumerate([8000, 8200, 8400, 8600, 8800]):
        _ad(conn, sid, f"b{i}", f"B{i}", kw=10.0, price=p, basis="post_rebate")
    conn.commit()
    m = report_site.load_market(conn, "kw")
    small = next(c for c in m["configs"] if c["config"] == "6.6kW")
    big = next(c for c in m["configs"] if c["config"] == "10kW")
    assert small["post"]["price"]["indicative"] is True
    assert big["post"]["price"]["indicative"] is False
    conn.close(); os.unlink(path)


def test_configs_are_ordered_by_size_not_alphabetically():
    """'10kW' sorting before '6.6kW' reads as a bug to anyone scanning."""
    conn, path, sid = _db()
    for i, kw in enumerate([10.0, 6.6, 20.0, 3.0]):
        _ad(conn, sid, f"c{i}", f"C{i}", kw=kw, price=5000)
    conn.commit()
    m = report_site.load_market(conn, "kw")
    assert [c["config"] for c in m["configs"]] == ["3kW", "6.6kW", "10kW", "20kW+"]
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
