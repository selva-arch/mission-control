"""
Build the list of Ad Library queries a sweep will run.

Two kinds of target:

  keyword    — a search term, optionally scoped to a state ("solar battery
               Adelaide"). State-scoped queries do double duty: they surface
               local advertisers that national terms miss, and they give a
               weak delivery signal for state inference.

  advertiser — every ad from one Page, via the Ad Library's
               `view_all_page_id` view. This is the richest source per request,
               but we can only build it after a keyword pass has discovered who
               the advertisers are, so it runs as a second stage.

Target keys are stable strings so a resumed sweep can tell what it already did.
"""

from __future__ import annotations

STATES = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"]

# Capital + major regional centres, used to build state-scoped search terms.
STATE_CITIES = {
    "NSW": ["Sydney", "Newcastle", "Wollongong"],
    "VIC": ["Melbourne", "Geelong", "Ballarat"],
    "QLD": ["Brisbane", "Gold Coast", "Townsville"],
    "SA":  ["Adelaide"],
    "WA":  ["Perth"],
    "TAS": ["Hobart", "Launceston"],
    "NT":  ["Darwin"],
    "ACT": ["Canberra"],
}

# Category → national search terms. Deliberately a mix of generic demand terms,
# brand names and offer language, because advertisers word things differently
# and the Ad Library only matches on ad text.
CATEGORY_KEYWORDS = {
    "battery": [
        "solar battery", "home battery", "battery storage", "battery rebate",
        "battery installation", "Tesla Powerwall", "Sungrow battery",
        "Fox ESS battery", "Pylontech battery", "BYD battery", "Anker SOLIX",
        "Alpha ESS", "GoodWe battery", "Sigenergy", "cheaper home batteries",
        "battery bundle", "solar and battery",
    ],
    "solar": [
        "solar panels", "solar system", "solar installation", "rooftop solar",
        "solar quote", "solar power system", "6.6kW solar", "10kW solar",
        "solar rebate", "solar upgrade", "Jinko solar", "Trina solar",
        "REC solar panels", "LONGi solar", "SunPower", "Fronius inverter",
        "Enphase microinverter", "solar deal",
    ],
    "ev_charger": [
        "EV charger", "home EV charger", "electric vehicle charger",
        "Tesla wall connector", "EV charger installation", "Zappi charger",
        "Fronius Wattpilot",
    ],
    "heat_pump": [
        "heat pump hot water", "hot water heat pump", "heat pump rebate",
        "Reclaim heat pump", "Sanden heat pump", "heat pump installation",
    ],
}

# Terms worth re-running per state. Kept short on purpose: the full keyword
# list × 8 states would be several hundred queries and hours of runtime for
# diminishing returns.
STATE_SCOPED_KEYWORDS = {
    "battery": ["solar battery", "home battery", "battery rebate"],
    "solar":   ["solar panels", "solar quote", "solar rebate"],
    "ev_charger": ["EV charger"],
    "heat_pump": ["heat pump hot water"],
}

# Small, high-signal subset for --pilot.
PILOT_KEYWORDS = {"battery": ["solar battery"], "solar": ["solar panels"]}
PILOT_STATES = ["SA", "NSW"]


def _key(kind: str, query: str, state: str = "") -> str:
    return f"{kind}:{query}:{state}".strip(":")


def keyword_targets(categories: list[str] | None = None,
                    states: list[str] | None = None,
                    pilot: bool = False) -> list[dict]:
    """National sweeps for every category, plus state-scoped sweeps.

    State scoping is done by appending both the state abbreviation and its
    major cities, since ads say "Adelaide" far more often than "SA".
    """
    cats = categories or list(CATEGORY_KEYWORDS)
    sts = states or STATES
    if pilot:
        cats = [c for c in cats if c in PILOT_KEYWORDS]
        sts = [s for s in sts if s in PILOT_STATES]

    out: list[dict] = []
    seen: set[str] = set()

    def add(query: str, state: str, category: str):
        k = _key("kw", query, state)
        if k in seen:
            return
        seen.add(k)
        out.append({"key": k, "kind": "keyword", "query": query,
                    "state": state, "category": category})

    source = PILOT_KEYWORDS if pilot else CATEGORY_KEYWORDS
    for cat in cats:
        for kw in source.get(cat, []):
            add(kw, "", cat)

    scoped = PILOT_KEYWORDS if pilot else STATE_SCOPED_KEYWORDS
    for cat in cats:
        for kw in scoped.get(cat, []):
            for st in sts:
                add(f"{kw} {st}", st, cat)
                for city in STATE_CITIES.get(st, [])[:1 if pilot else 2]:
                    add(f"{kw} {city}", st, cat)
    return out


def advertiser_targets(page_ids: list[tuple[str, str]]) -> list[dict]:
    """Second-stage targets: every ad from each discovered Page.

    `page_ids` is a list of (page_id, page_name) so the target carries a
    human-readable label for progress output.
    """
    out = []
    for pid, name in page_ids:
        if not pid:
            continue
        out.append({
            "key": _key("page", pid),
            "kind": "advertiser",
            "query": pid,
            "state": "",
            "category": "mixed",
            "label": name,
        })
    return out


def summarise(targets: list[dict]) -> str:
    by_kind: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    for t in targets:
        by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
        by_cat[t.get("category", "?")] = by_cat.get(t.get("category", "?"), 0) + 1
    kinds = ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))
    cats = ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items()))
    return f"{len(targets)} targets ({kinds}) by category: {cats}"
