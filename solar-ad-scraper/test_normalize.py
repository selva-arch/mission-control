"""Quick sanity tests for the price/capacity/rebate logic.

Run: python -m pytest test_normalize.py   (or: python test_normalize.py)
Uses the three ads from the original screenshots as fixtures.
"""

import extract
import normalize


def test_extract_prices():
    assert extract.extract_prices("Now Only $5981*") == [5981]
    assert extract.extract_prices("15KWH FROM $6,990*") == [6990]
    # Junk / out-of-range numbers are ignored.
    assert extract.extract_prices("call 1300 867 353") == []
    assert extract.extract_prices("$50 off") == []


def test_extract_capacities():
    assert extract.extract_capacities("FOX ESS 27.96kWH") == [27.96]
    assert extract.extract_capacities("15KWH FROM") == [15.0]
    assert extract.extract_capacities("Up to 180kWh Capacity") == [180.0]


def test_rebate_tiers():
    r = normalize.RebateModel()
    # First 14 kWh fully subsidised.
    assert round(r.estimate(14)) == round(14 * 272)
    # 15 kWh = 14*272 + 1*163.
    assert round(r.estimate(15)) == round(14 * 272 + 1 * 163)
    # Capped at 50 kWh.
    assert r.estimate(80) == r.estimate(50)


def test_compare_foxess():
    # FOX ESS: $5,981 for 27.96 kWh.
    cmp = normalize.compare([5981], [27.96], normalize.RebateModel())
    assert cmp.dollars_per_kwh == round(5981 / 27.96, 1)
    assert cmp.price == 5981
    assert cmp.capacity_kwh == 27.96


def test_compare_pylontech_cheaper_per_kwh_check():
    fox = normalize.compare([5981], [27.96], normalize.RebateModel())
    pyl = normalize.compare([6990], [15.0], normalize.RebateModel())
    # FOX is much cheaper per usable kWh than the Pylontech offer.
    assert fox.dollars_per_kwh < pyl.dollars_per_kwh


def test_deal_score_favours_long_running():
    cmp = normalize.compare([5981], [27.96], normalize.RebateModel())
    fresh = normalize.deal_score(cmp, days_running=1)
    aged = normalize.deal_score(cmp, days_running=300)
    assert aged < fresh  # long-running ad scores (slightly) better


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
