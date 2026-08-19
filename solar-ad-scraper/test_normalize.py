"""Quick sanity tests for the price/capacity/rebate logic.

Run: python -m pytest test_normalize.py   (or: python test_normalize.py)
Uses the three ads from the original screenshots as fixtures.
"""

import extract
import states
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



# ---------------------------------------------------------------------------
# System sizes (kW) vs capacity (kWh)
# ---------------------------------------------------------------------------

def test_kw_is_not_confused_with_kwh():
    """The single most damaging parse error: reading a 6.6kW array as 6.6kWh
    of storage would triple every $/kWh figure for solar ads."""
    text = "6.6kW solar system with a 13.5kWh battery"
    assert extract.extract_system_sizes(text) == [6.6]
    assert extract.extract_capacities(text) == [13.5]


def test_system_size_bounds():
    # Residential arrays only; a 500 kW figure is commercial or a misparse.
    assert extract.extract_system_sizes("500kW commercial") == []
    assert extract.extract_system_sizes("10 kW system") == [10.0]


def test_price_bands_are_per_category():
    """A $899 headline is noise in a battery ad but real for an EV charger."""
    assert extract.extract_prices("EV charger $899", "ev_charger") == [899]
    assert extract.extract_prices("EV charger $899", "battery") == []
    assert extract.extract_prices("battery $8,990", "battery") == [8990]


def test_category_classifier():
    assert extract.classify_category("Tesla Powerwall 13.5kWh storage") == "battery"
    assert extract.classify_category("Rooftop solar panels + inverter") == "solar"
    assert extract.classify_category("hot water heat pump rebate") == "heat_pump"
    assert extract.classify_category("Home EV charger installation") == "ev_charger"
    # A bundled mention must not hijack the primary product.
    assert extract.classify_category(
        "6.6kW solar system and 13.5kWh battery, free EV charger"
    ) == "solar_battery"
    assert extract.classify_category("Book a free consultation") == "other"


# ---------------------------------------------------------------------------
# State inference
# ---------------------------------------------------------------------------

def test_state_abbreviations_do_not_false_positive():
    """'sa', 'wa', 'act' and 'nt' occur constantly inside ordinary words. If
    lowercase matches were trusted, most of the corpus would be attributed to
    South Australia."""
    for text in ["Our sales team will act fast", "Full warranty included",
                 "We want your business", "Act now, don't wait"]:
        assert states.infer(text) == [], f"false positive on: {text}"


def test_state_tokens_and_cities():
    sigs = states.infer("Servicing Adelaide and regional South Australia")
    assert {s.state for s in sigs} == {"SA"}
    assert {s.signal for s in sigs} == {"state_token", "city"}
    assert all(s.confidence == "high" for s in sigs)


def test_scheme_names_are_high_confidence():
    sigs = states.infer("Claim the Queensland Battery Booster rebate")
    assert any(s.signal == "scheme" and s.state == "QLD" and s.confidence == "high"
               for s in sigs)


def test_ambiguous_area_code_emits_every_candidate():
    """08 covers SA, WA and NT. Recording one guess would be a fabrication."""
    sigs = [s for s in states.infer("Call us on 08 8123 4567") if s.signal == "area_code"]
    assert {s.state for s in sigs} == {"SA", "WA", "NT"}
    assert all(s.confidence == "medium" for s in sigs)


def test_national_campaign_is_flagged_not_counted_everywhere():
    """A national installer listing service areas must not read as a local
    advertiser in eight separate markets."""
    sigs = states.resolve(states.infer(
        "We install across NSW, VIC, QLD, SA, WA and TAS"))
    assert any(s.state == states.NATIONAL for s in sigs)
    assert states.best_state(sigs)[0] == states.NATIONAL


def test_query_provenance_is_weak_evidence_only():
    sigs = states.infer("Great solar deal", query_state="VIC")
    assert [(s.state, s.confidence) for s in sigs] == [("VIC", "low")]
    # Strong copy evidence outranks the search that surfaced the ad.
    sigs = states.infer("Adelaide install special", query_state="VIC")
    assert states.best_state(states.resolve(sigs)) == ("SA", "high")


def test_unknown_state_stays_unknown():
    """An honest blank beats a fabricated attribution."""
    assert states.best_state(states.infer("Free solar quote today")) == ("", "")


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
