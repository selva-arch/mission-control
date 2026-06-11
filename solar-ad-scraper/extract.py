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
    page_name: str = ""
    body_text: str = ""
    title: str = ""
    link_url: str = ""
    image_urls: list[str] = field(default_factory=list)
    start_date: datetime | None = None
    end_date: datetime | None = None
    is_active: bool | None = None
    collation_count: int | None = None  # how many near-identical copies are running
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
        return "\n".join([self.title, self.body_text, self.ocr_text]).strip()


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
    urls: list[str] = []
    for img in snapshot.get("images") or []:
        if isinstance(img, dict):
            u = _first(img, "original_image_url", "resized_image_url", "url")
            if u:
                urls.append(u)
    for vid in snapshot.get("videos") or []:
        if isinstance(vid, dict):
            u = _first(vid, "video_preview_image_url", "thumbnail_url")
            if u:
                urls.append(u)
    for card in snapshot.get("cards") or []:
        if isinstance(card, dict):
            u = _first(card, "original_image_url", "resized_image_url")
            if u:
                urls.append(u)
    # De-dupe, keep order.
    return list(dict.fromkeys(urls))


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

                body_obj = snapshot.get("body")
                if isinstance(body_obj, dict):
                    body_text = body_obj.get("text") or ""
                else:
                    body_text = body_obj or ""

                ad = ads.get(archive_id) or Ad(ad_archive_id=archive_id)
                ad.page_name = ad.page_name or (_first(snapshot, "page_name") or "")
                ad.body_text = ad.body_text or body_text
                ad.title = ad.title or (_first(snapshot, "title", "caption") or "")
                ad.link_url = ad.link_url or (
                    _first(snapshot, "link_url", "caption") or ""
                )
                if not ad.image_urls:
                    ad.image_urls = _collect_images(snapshot)
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
                if ad.collation_count is None:
                    ad.collation_count = _first(node, "collation_count", "collationCount")

                ads[archive_id] = ad
    return ads


# ---------------------------------------------------------------------------
# Price + capacity signal extraction (text and OCR).
# ---------------------------------------------------------------------------

PRICE_RE = re.compile(r"\$\s?([0-9]{1,3}(?:[, ][0-9]{3})+|[0-9]{4,6})(?:\.[0-9]{2})?")
KWH_RE = re.compile(r"([0-9]{1,3}(?:\.[0-9]{1,2})?)\s?k\s?w\s?h", re.IGNORECASE)


def extract_prices(text: str) -> list[int]:
    """Return plausible battery prices (whole dollars) found in text, deduped."""
    out: list[int] = []
    for m in PRICE_RE.finditer(text or ""):
        raw = m.group(1).replace(",", "").replace(" ", "")
        try:
            val = int(raw)
        except ValueError:
            continue
        # Battery system prices realistically sit between ~$1k and ~$60k.
        if 1000 <= val <= 60000:
            out.append(val)
    return list(dict.fromkeys(out))


def extract_capacities(text: str) -> list[float]:
    """Return plausible kWh capacities found in text, deduped."""
    out: list[float] = []
    for m in KWH_RE.finditer(text or ""):
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        if 1 <= val <= 200:
            out.append(val)
    return list(dict.fromkeys(out))


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
