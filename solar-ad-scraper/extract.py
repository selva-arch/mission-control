"""
Turn raw Ad Library GraphQL bodies into structured ad records, and pull price /
capacity signals out of both ad text and the ad creative images (via OCR).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Ad:
    ad_archive_id: str = ""
    page_id: str = ""
    page_name: str = ""
    body_text: str = ""
    title: str = ""
    caption: str = ""
    link_description: str = ""
    cta_type: str = ""
    cta_text: str = ""
    link_url: str = ""
    display_format: str = ""
    publisher_platforms: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    video_poster_urls: list[str] = field(default_factory=list)
    start_date: datetime | None = None
    end_date: datetime | None = None
    is_active: bool | None = None
    # The Ad Library's nearest thing to an "ad set": a group of near-identical
    # variants running together. Meta never exposes real campaign structure.
    collation_id: str = ""
    collation_count: int | None = None
    ocr_text: str = ""

    @property
    def library_url(self) -> str:
        return f"https://www.facebook.com/ads/library/?id={self.ad_archive_id}"

    @property
    def status(self) -> str:
        if self.is_active is True:
            return "Active"
        if self.is_active is False:
            return "Ended"
        return ""

    @property
    def days_running(self) -> int | None:
        """How long the ad ran. For ended ads, start->end; for live ads,
        start->now (i.e. how long it has been running so far)."""
        if not self.start_date:
            return None
        end = self.end_date if (self.is_active is False and self.end_date) else \
            datetime.now(timezone.utc)
        return max(0, (end - self.start_date).days)

    @property
    def all_text(self) -> str:
        """Every text surface an offer might be stated on, including OCR."""
        return "\n".join([
            self.title, self.body_text, self.caption,
            self.link_description, self.cta_text, self.ocr_text,
        ]).strip()

    @property
    def all_creative_urls(self) -> list[str]:
        return list(dict.fromkeys(self.image_urls + self.video_poster_urls))


# ---------------------------------------------------------------------------
# JSON parsing — walk the GraphQL payloads for ad result nodes.
# ---------------------------------------------------------------------------

def _iter_json_objects(body: str):
    """Yield top-level JSON objects from a body that may be a single object or
    a newline-delimited stream of objects (Facebook streams several)."""
    body = body.strip()
    if not body:
        return
    try:
        yield json.loads(body)
        return
    except json.JSONDecodeError:
        pass
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _walk(obj):
    """Recursively yield every dict in a nested JSON structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _epoch_to_dt(value) -> datetime | None:
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    # Ad Library uses seconds; guard against ms just in case.
    if ts > 10_000_000_000:
        ts //= 1000
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _first(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _collect_images(snapshot: dict) -> list[str]:
    """Still images: hero images plus every carousel card.

    Carousel cards matter disproportionately — the price is very often on card
    2 or 3 while the hero is a lifestyle shot.
    """
    urls: list[str] = []
    for img in snapshot.get("images") or []:
        if isinstance(img, dict):
            u = _first(img, "original_image_url", "resized_image_url", "url")
            if u:
                urls.append(u)
    for card in snapshot.get("cards") or []:
        if isinstance(card, dict):
            u = _first(card, "original_image_url", "resized_image_url",
                       "video_preview_image_url")
            if u:
                urls.append(u)
    return list(dict.fromkeys(urls))


def _collect_video_posters(snapshot: dict) -> list[str]:
    """Poster frames for video ads. We keep the frame, not the video file —
    the analytical value is in the copy and the opening frame."""
    urls: list[str] = []
    for vid in snapshot.get("videos") or []:
        if isinstance(vid, dict):
            u = _first(vid, "video_preview_image_url", "thumbnail_url")
            if u:
                urls.append(u)
    return list(dict.fromkeys(urls))


def _text_of(value) -> str:
    """Snapshot fields are sometimes a bare string, sometimes {"text": ...}."""
    if isinstance(value, dict):
        return value.get("text") or ""
    return value or ""


def parse_ad_nodes(bodies: list[str]) -> dict[str, Ad]:
    """Parse all response bodies into a dict of {ad_archive_id: Ad}."""
    ads: dict[str, Ad] = {}
    for body in bodies:
        for root in _iter_json_objects(body):
            for node in _walk(root):
                snapshot = node.get("snapshot")
                if not isinstance(snapshot, dict):
                    continue

                archive_id = str(
                    _first(node, "ad_archive_id", "adArchiveID", "id")
                    or _first(snapshot, "ad_archive_id")
                    or ""
                )
                if not archive_id:
                    continue

                ad = ads.get(archive_id) or Ad(ad_archive_id=archive_id)

                # Text surfaces. Only ever widen — a later payload for the same
                # ad is sometimes sparser, and must not blank out real copy.
                ad.page_name = ad.page_name or (_first(snapshot, "page_name") or "")
                ad.page_id = ad.page_id or str(
                    _first(snapshot, "page_id", "pageID")
                    or _first(node, "page_id", "pageID") or ""
                )
                ad.body_text = ad.body_text or _text_of(snapshot.get("body"))
                ad.title = ad.title or _text_of(_first(snapshot, "title"))
                ad.caption = ad.caption or _text_of(_first(snapshot, "caption"))
                ad.link_description = ad.link_description or _text_of(
                    _first(snapshot, "link_description", "linkDescription")
                )
                ad.cta_type = ad.cta_type or (
                    _first(snapshot, "cta_type", "ctaType") or ""
                )
                ad.cta_text = ad.cta_text or (
                    _first(snapshot, "cta_text", "ctaText") or ""
                )
                ad.link_url = ad.link_url or (_first(snapshot, "link_url") or "")
                ad.display_format = ad.display_format or (
                    _first(snapshot, "display_format", "displayFormat") or ""
                )

                if not ad.publisher_platforms:
                    plats = _first(node, "publisher_platform", "publisherPlatform") or \
                        _first(snapshot, "publisher_platform")
                    if isinstance(plats, list):
                        ad.publisher_platforms = [str(p) for p in plats]
                    elif plats:
                        ad.publisher_platforms = [str(plats)]

                if not ad.image_urls:
                    ad.image_urls = _collect_images(snapshot)
                if not ad.video_poster_urls:
                    ad.video_poster_urls = _collect_video_posters(snapshot)

                if ad.start_date is None:
                    ad.start_date = _epoch_to_dt(
                        _first(node, "start_date", "startDate", "ad_delivery_start_time")
                    )
                if ad.end_date is None:
                    ad.end_date = _epoch_to_dt(
                        _first(node, "end_date", "endDate", "ad_delivery_stop_time")
                    )
                if ad.is_active is None:
                    ad.is_active = _first(node, "is_active", "isActive")
                if not ad.collation_id:
                    ad.collation_id = str(
                        _first(node, "collation_id", "collationID") or ""
                    )
                if ad.collation_count is None:
                    ad.collation_count = _first(node, "collation_count", "collationCount")

                ads[archive_id] = ad
    return ads


# ---------------------------------------------------------------------------
# Price + capacity signal extraction (text and OCR).
# ---------------------------------------------------------------------------

PRICE_RE = re.compile(r"\$\s?([0-9]{1,3}(?:[, ][0-9]{3})+|[0-9]{3,6})(?:\.[0-9]{2})?")
KWH_RE = re.compile(r"([0-9]{1,3}(?:\.[0-9]{1,2})?)\s?k\s?w\s?h", re.IGNORECASE)
# kW that is NOT kWh — the negative lookahead is what separates a 6.6kW panel
# array from a 6.6kWh battery. Without it every system size reads as capacity.
KW_RE = re.compile(r"([0-9]{1,3}(?:\.[0-9]{1,2})?)\s?k\s?w(?!\s?h)", re.IGNORECASE)

# Plausible installed-price bands per product. v1 hardcoded the battery band
# ($1k-$60k), which silently dropped every EV charger and heat pump price.
PRICE_BANDS = {
    "battery":    (1000, 60000),
    "solar":      (1000, 60000),
    "ev_charger": (300, 6000),
    "heat_pump":  (800, 10000),
    "mixed":      (300, 60000),
}

# Sanity bands for the derived unit prices, used to flag bad parses.
DOLLARS_PER_KWH_BAND = (150, 1500)
DOLLARS_PER_KW_BAND = (400, 4000)


def extract_prices(text: str, category: str = "mixed") -> list[int]:
    """Return plausible prices (whole dollars) found in text, deduped.

    The band is category-dependent because a $900 figure is noise in a battery
    ad but a real headline price in an EV-charger ad.
    """
    lo, hi = PRICE_BANDS.get(category, PRICE_BANDS["mixed"])
    out: list[int] = []
    for m in PRICE_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "").replace(" ", "")
        try:
            val = int(raw)
        except ValueError:
            continue
        if lo <= val <= hi:
            out.append(val)
    return list(dict.fromkeys(out))


def extract_capacities(text: str) -> list[float]:
    """Return plausible battery capacities (kWh) found in text, deduped."""
    out: list[float] = []
    for m in KWH_RE.finditer(text or ""):
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        if 1 <= val <= 200:
            out.append(val)
    return list(dict.fromkeys(out))


def extract_system_sizes(text: str) -> list[float]:
    """Return plausible solar array sizes (kW) found in text, deduped.

    Residential arrays run roughly 1.5-30 kW; anything larger is commercial or
    a misparse, and inverter ratings below that are usually a different figure.
    """
    out: list[float] = []
    for m in KW_RE.finditer(text or ""):
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        if 1.5 <= val <= 30:
            out.append(val)
    return list(dict.fromkeys(out))


# Keyword signatures for classifying an ad when the search category is unknown
# (per-advertiser sweeps return everything a Page runs, not one category).
CATEGORY_SIGNATURES = {
    "heat_pump":  ["heat pump", "hot water"],
    "ev_charger": ["ev charger", "electric vehicle charger", "wall connector",
                   "wallbox", "charging station"],
    "battery":    ["battery", "powerwall", "storage", "kwh"],
    "solar":      ["solar panel", "solar system", "rooftop solar", "inverter",
                   "solar power", "kw system"],
}


def classify_category(text: str) -> str:
    """Best-effort product category from ad copy.

    Scores by how many distinct signature phrases hit, rather than taking the
    first match, so a solar+battery ad that merely mentions a bundled EV
    charger is not misfiled as an EV-charger ad. This is a fallback only —
    enrich.py's LLM pass is the authoritative category when it has run.
    """
    low = (text or "").lower()
    scores = {
        cat: sum(1 for sig in sigs if sig in low)
        for cat, sigs in CATEGORY_SIGNATURES.items()
    }
    batt, solar = scores["battery"], scores["solar"]
    if batt and solar:
        # A combined-system ad; only let a niche category win if it dominates.
        niche = max(scores["heat_pump"], scores["ev_charger"])
        if niche > max(batt, solar):
            return "heat_pump" if scores["heat_pump"] >= scores["ev_charger"] else "ev_charger"
        return "solar_battery"
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] else "other"


def ocr_image(path: str) -> str:
    """OCR a downloaded creative. Returns '' if OCR deps are unavailable."""
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return ""
    try:
        return pytesseract.image_to_string(Image.open(path))
    except Exception:
        return ""
