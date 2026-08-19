"""
Infer which Australian state(s) an ad is aimed at.

This exists because Meta publishes NO geographic targeting for Australian
commercial ads — state delivery data is available only for political/social-
issue ads and for EU ads under the DSA. Everything here is therefore inference
from observable evidence, never a Meta-reported fact, and the dashboard must
present it that way.

Design: every signal that fires is recorded as its own row with its own
confidence and the exact evidence string that triggered it. Nothing is
collapsed into a single "the state is X" guess at write time. That keeps the
decision reversible — you can re-weight signals later, or audit why an ad was
attributed to a state, without re-scraping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STATES = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"]
NATIONAL = "NATIONAL"

# An ad hitting this many distinct states is almost certainly a national
# campaign listing service areas, not a local one.
NATIONAL_THRESHOLD = 4


@dataclass(frozen=True)
class StateSignal:
    state: str
    signal: str      # state_token | city | scheme | advertiser | area_code | query | llm
    confidence: str  # high | medium | low
    evidence: str


# --- Full state names and abbreviations ------------------------------------
STATE_NAMES = {
    "NSW": ["new south wales", "nsw"],
    "VIC": ["victoria", "vic"],
    "QLD": ["queensland", "qld"],
    "SA":  ["south australia", "sa"],
    "WA":  ["western australia", "wa"],
    "TAS": ["tasmania", "tas"],
    "NT":  ["northern territory", "nt"],
    "ACT": ["australian capital territory", "act"],
}

# Two-letter abbreviations are dangerous as bare tokens: "sa" appears inside
# ordinary words and "wa"/"act"/"nt" are common English fragments. These are
# matched only with word boundaries AND require an uppercase form in the
# original text (checked separately below).
RISKY_ABBREVIATIONS = {"sa", "wa", "act", "nt", "vic", "tas"}

# --- Cities and major regional centres --------------------------------------
CITY_TO_STATE = {
    # NSW
    "sydney": "NSW", "newcastle": "NSW", "wollongong": "NSW", "central coast": "NSW",
    "parramatta": "NSW", "penrith": "NSW", "wagga wagga": "NSW", "albury": "NSW",
    "tamworth": "NSW", "coffs harbour": "NSW", "port macquarie": "NSW", "dubbo": "NSW",
    "orange nsw": "NSW", "bathurst": "NSW", "nowra": "NSW", "byron bay": "NSW",
    # VIC
    "melbourne": "VIC", "geelong": "VIC", "ballarat": "VIC", "bendigo": "VIC",
    "shepparton": "VIC", "mildura": "VIC", "warrnambool": "VIC", "traralgon": "VIC",
    "frankston": "VIC", "dandenong": "VIC", "gippsland": "VIC", "mornington peninsula": "VIC",
    # QLD
    "brisbane": "QLD", "gold coast": "QLD", "sunshine coast": "QLD", "townsville": "QLD",
    "cairns": "QLD", "toowoomba": "QLD", "mackay": "QLD", "rockhampton": "QLD",
    "bundaberg": "QLD", "hervey bay": "QLD", "gladstone": "QLD", "ipswich": "QLD",
    "logan": "QLD", "caboolture": "QLD",
    # SA
    "adelaide": "SA", "mount gambier": "SA", "whyalla": "SA", "murray bridge": "SA",
    "port lincoln": "SA", "port augusta": "SA", "gawler": "SA", "victor harbor": "SA",
    # WA
    "perth": "WA", "fremantle": "WA", "bunbury": "WA", "geraldton": "WA",
    "kalgoorlie": "WA", "albany wa": "WA", "mandurah": "WA", "joondalup": "WA",
    "rockingham": "WA", "busselton": "WA",
    # TAS
    "hobart": "TAS", "launceston": "TAS", "devonport": "TAS", "burnie": "TAS",
    # NT
    "darwin": "NT", "alice springs": "NT", "palmerston nt": "NT", "katherine nt": "NT",
    # ACT
    "canberra": "ACT", "belconnen": "ACT", "gungahlin": "ACT", "tuggeranong": "ACT",
}

# --- State-specific incentive scheme names ----------------------------------
# These are the strongest possible signal: an advertiser naming a state scheme
# is unambiguously selling into that state.
SCHEME_TO_STATE = {
    "battery booster": "QLD",
    "queensland battery": "QLD",
    "solar homes": "VIC",
    "solar victoria": "VIC",
    "solar for rentals": "VIC",
    "victorian energy upgrades": "VIC",
    "veu": "VIC",
    "empowering homes": "NSW",
    "peak demand reduction scheme": "NSW",
    "energy savings scheme": "NSW",
    "pdrs": "NSW",
    "home battery scheme": "SA",
    "sa home battery": "SA",
    "retailer energy productivity scheme": "SA",
    "reps": "SA",
    "residential battery scheme": "WA",
    "distributed energy buyback": "WA",
    "synergy": "WA",
    "horizon power": "WA",
    "renewable energy loan scheme": "TAS",
    "aurora energy": "TAS",
    "energy efficiency improvement scheme": "ACT",
    "sustainable household scheme": "ACT",
    "jacana energy": "NT",
    # Distribution networks are effectively state-bound too.
    "ausgrid": "NSW", "endeavour energy": "NSW", "essential energy": "NSW",
    "citipower": "VIC", "powercor": "VIC", "jemena": "VIC", "ausnet": "VIC",
    "united energy": "VIC",
    "energex": "QLD", "ergon": "QLD",
    "sa power networks": "SA",
    "western power": "WA",
    "tasnetworks": "TAS",
    "evoenergy": "ACT",
    "power and water": "NT",
}

# --- Phone area codes -------------------------------------------------------
# Genuinely ambiguous: one code covers several states, so each emits a row per
# candidate state at medium confidence rather than pretending to be precise.
AREA_CODE_TO_STATES = {
    "02": ["NSW", "ACT"],
    "03": ["VIC", "TAS"],
    "07": ["QLD"],
    "08": ["SA", "WA", "NT"],
}
PHONE_RE = re.compile(r"(?:^|[^0-9])\(?(0[2378])\)?[\s-]?\d{4}[\s-]?\d{4}")


def _find_state_tokens(text: str) -> list[StateSignal]:
    """Match state names and abbreviations.

    Full names are safe to match case-insensitively. Bare two/three-letter
    abbreviations are only trusted when they appear uppercase in the original
    text, because "sa" and "act" occur constantly inside ordinary words and
    would otherwise attribute half the corpus to South Australia.
    """
    out: list[StateSignal] = []
    low = text.lower()
    for state, variants in STATE_NAMES.items():
        for variant in variants:
            if variant in RISKY_ABBREVIATIONS:
                # Require the uppercase form, as a standalone word.
                if re.search(rf"\b{variant.upper()}\b", text):
                    out.append(StateSignal(state, "state_token", "high", variant.upper()))
                    break
            else:
                if re.search(rf"\b{re.escape(variant)}\b", low):
                    out.append(StateSignal(state, "state_token", "high", variant))
                    break
    return out


def _find_cities(text: str) -> list[StateSignal]:
    low = text.lower()
    out: list[StateSignal] = []
    seen: set[str] = set()
    for city, state in CITY_TO_STATE.items():
        if re.search(rf"\b{re.escape(city)}\b", low):
            key = f"{state}:{city}"
            if key not in seen:
                seen.add(key)
                out.append(StateSignal(state, "city", "high", city))
    return out


def _find_schemes(text: str) -> list[StateSignal]:
    low = text.lower()
    out: list[StateSignal] = []
    for scheme, state in SCHEME_TO_STATE.items():
        if scheme in low:
            out.append(StateSignal(state, "scheme", "high", scheme))
    return out


def _find_area_codes(text: str) -> list[StateSignal]:
    out: list[StateSignal] = []
    for m in PHONE_RE.finditer(text or ""):
        code = m.group(1)
        for state in AREA_CODE_TO_STATES.get(code, []):
            out.append(StateSignal(state, "area_code", "medium", code))
    return out


def infer(text: str, *, query_state: str = "", advertiser_state: str = "",
          llm_states: list[str] | None = None) -> list[StateSignal]:
    """Return every state signal that fires for one ad.

    `text` should be all copy surfaces concatenated (title, body, caption, CTA
    and OCR). `query_state` is the state scope of the search that surfaced the
    ad — weak evidence of delivery, not of targeting. `advertiser_state` is the
    Page's home state. `llm_states` are states the enrichment pass read out of
    the copy.
    """
    signals: list[StateSignal] = []
    signals += _find_state_tokens(text or "")
    signals += _find_cities(text or "")
    signals += _find_schemes(text or "")
    signals += _find_area_codes(text or "")

    if advertiser_state in STATES:
        signals.append(StateSignal(advertiser_state, "advertiser", "medium",
                                   "page home state"))
    if query_state in STATES:
        signals.append(StateSignal(query_state, "query", "low",
                                   f"surfaced by {query_state}-scoped search"))
    for st in (llm_states or []):
        st = (st or "").strip().upper()
        if st in STATES:
            signals.append(StateSignal(st, "llm", "medium", "llm extraction"))

    # De-duplicate identical (state, signal, evidence) triples.
    return list(dict.fromkeys(signals))


def resolve(signals: list[StateSignal],
            national_threshold: int = NATIONAL_THRESHOLD) -> list[StateSignal]:
    """Add a NATIONAL marker when the evidence points everywhere at once.

    Without this a single national installer listing every service area would
    be counted as a local advertiser in all eight states, inflating every
    per-state figure. The underlying per-state signals are kept — the marker is
    additive, so the dashboard can exclude national campaigns from local counts
    rather than losing the data.
    """
    strong = {s.state for s in signals if s.confidence == "high"}
    if len(strong) >= national_threshold:
        return signals + [StateSignal(NATIONAL, "derived", "high",
                                      f"{len(strong)} states cited")]
    return signals


def best_state(signals: list[StateSignal]) -> tuple[str, str]:
    """Collapse signals to a single (state, confidence) for summary display.

    Ranks by strongest confidence, then by how many independent signals agree.
    Returns ("", "") when there is nothing to go on — an honest unknown beats a
    fabricated attribution.
    """
    if any(s.state == NATIONAL for s in signals):
        return NATIONAL, "high"
    rank = {"high": 3, "medium": 2, "low": 1}
    tally: dict[str, tuple[int, int]] = {}
    for s in signals:
        if s.state == NATIONAL:
            continue
        best, count = tally.get(s.state, (0, 0))
        tally[s.state] = (max(best, rank[s.confidence]), count + 1)
    if not tally:
        return "", ""
    state = max(tally, key=lambda st: (tally[st][0], tally[st][1]))
    inv = {3: "high", 2: "medium", 1: "low"}
    return state, inv[tally[state][0]]
