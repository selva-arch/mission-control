"""
Nearmap integration: AI solar/roof detection + vertical imagery tiles.

Endpoints and the solar/roof class IDs are from Nearmap's developer docs and
their official SDK (nearmap/nmaipy). The two solar UUIDs are SDK-verified; the
Roof UUID is high-confidence-but-verify — call classes()/packs() once against
your account to confirm before a large run.

Auth: header `Authorization: Apikey <key>` for JSON APIs, `?apikey=` for tiles.
"""

from __future__ import annotations

import io
import math
import time
from dataclasses import dataclass

import requests
from PIL import Image

from .imagery import ImagerySource

NM_BASE = "https://api.nearmap.com"

# SDK-verified (nmaipy/constants.py). Re-confirm via classes.json for your account.
PV_PANEL_ID = "3680e1b8-8ae1-5a15-8ec7-820078ef3298"   # "Solar Panel" (PV)
SOLAR_HW_ID = "c1143023-135b-54fd-9a07-8de0ff55de51"   # "Solar Hot Water" — EXCLUDE
ROOF_ID = "c08255a4-ba9f-562b-932c-ff76f2faeeeb"        # "Roof"


@dataclass
class SolarResult:
    has_solar: bool = False
    panel_count: int = 0
    pv_area_sqm: float = 0.0
    roof_area_sqm: float = 0.0
    confidence: float = 0.0
    survey_date: str = ""
    error: str = ""


def _aoi_polygon(lat: float, lon: float, size_m: float) -> str:
    """A small square AOI (lon,lat corners) centred on the point. Use a real
    parcel/footprint polygon instead when you have one — a synthetic square can
    clip a neighbour's panels (which only ever makes us SKIP a lead, never
    mis-mail one, so it's the safe direction to err)."""
    half = size_m / 2.0
    dlat = half / 111320.0
    dlon = half / (111320.0 * math.cos(math.radians(lat)) or 1e-6)
    corners = [
        (lon - dlon, lat - dlat), (lon + dlon, lat - dlat),
        (lon + dlon, lat + dlat), (lon - dlon, lat + dlat),
    ]
    return ",".join(f"{x:.7f},{y:.7f}" for x, y in corners)


class NearmapClient:
    """AI Feature API client for per-property solar/roof detection."""

    def __init__(self, api_key: str, packs: str = "solar,roof_char",
                 ai_path: str = "/ai/features/v4/features.json",
                 min_confidence: float = 0.5, aoi_size_m: float = 25.0,
                 session: requests.Session | None = None):
        self.key = api_key
        self.packs = packs
        self.ai_path = ai_path
        self.min_conf = min_confidence
        self.aoi_size_m = aoi_size_m
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Apikey {api_key}"})

    def solar(self, lat: float, lon: float,
              since: str | None = None, until: str | None = None) -> SolarResult:
        """Detect existing PV solar + roof area for the property at (lat, lon)."""
        params = {"polygon": _aoi_polygon(lat, lon, self.aoi_size_m), "packs": self.packs}
        if since:
            params["since"] = since
        if until:
            params["until"] = until

        data = None
        for attempt in range(4):  # backoff on rate-limit / transient errors
            try:
                r = self.s.get(NM_BASE + self.ai_path, params=params, timeout=40)
                if r.status_code == 404:
                    return SolarResult(error="no coverage")
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    return SolarResult(error=str(e))
                time.sleep(2 ** attempt)
        if data is None:
            return SolarResult(error="no response")

        feats = data.get("features", [])
        area = lambda f: f.get("clippedAreaSqm", f.get("areaSqm", 0)) or 0
        pv = [f for f in feats
              if f.get("classId") == PV_PANEL_ID and f.get("confidence", 0) >= self.min_conf]
        roofs = [f for f in feats if f.get("classId") == ROOF_ID]
        return SolarResult(
            has_solar=len(pv) > 0,
            panel_count=len(pv),
            pv_area_sqm=round(sum(area(f) for f in pv), 1),
            roof_area_sqm=round(sum(area(f) for f in roofs), 1),
            confidence=round(max((f.get("confidence", 0) for f in pv), default=0.0), 3),
            survey_date=data.get("surveyDate", ""),
        )

    # --- one-off account checks (run these once when wiring up) ---
    def classes(self) -> dict:
        r = self.s.get(NM_BASE + "/ai/features/v4/classes.json", timeout=40)
        r.raise_for_status()
        return r.json()

    def packs_available(self) -> dict:
        r = self.s.get(NM_BASE + "/ai/features/v4/packs.json", timeout=40)
        r.raise_for_status()
        return r.json()

    def coverage(self, lat: float, lon: float) -> dict:
        r = self.s.get(f"{NM_BASE}/coverage/v2/point/{lon},{lat}", timeout=40)  # lon,lat!
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Vertical imagery tiles → stitched, roof-centred image (ImagerySource).
# ---------------------------------------------------------------------------

def _lonlat_to_tile(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    xf = (lon + 180.0) / 360.0 * n
    yf = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return xf, yf


class NearmapTiles(ImagerySource):
    """Fetch + stitch Nearmap Vert (ortho) tiles and crop to the roof.

    Mail-safe imagery source (with the right Nearmap external-use licence +
    attribution); slots into the same ImagerySource interface as Google."""

    def __init__(self, api_key: str, zoom: int = 21, grid: int = 3,
                 fmt: str = "jpg", footprint_m: float = 40.0,
                 session: requests.Session | None = None):
        self.key = api_key
        self.zoom = zoom
        self.grid = grid if grid % 2 == 1 else grid + 1  # force odd
        self.fmt = fmt
        self.footprint_m = footprint_m
        self.s = session or requests.Session()

    def tile(self, lat: float, lon: float) -> bytes:
        z, N = self.zoom, self.grid
        xf, yf = _lonlat_to_tile(lat, lon, z)
        cx, cy = int(math.floor(xf)), int(math.floor(yf))
        half = (N - 1) // 2
        xmin, ymin = cx - half, cy - half

        canvas = Image.new("RGB", (N * 256, N * 256))
        for x in range(cx - half, cx + half + 1):
            for y in range(cy - half, cy + half + 1):
                url = f"{NM_BASE}/tiles/v3/Vert/{z}/{x}/{y}.{self.fmt}"
                try:
                    r = self.s.get(url, params={"apikey": self.key}, timeout=40)
                    if r.status_code != 200:
                        continue
                    im = Image.open(io.BytesIO(r.content)).convert("RGB")
                    canvas.paste(im, ((x - xmin) * 256, (y - ymin) * 256))
                except Exception:  # noqa: BLE001
                    continue

        # Crop a footprint_m box centred on the roof pixel.
        mpp = 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)
        px = max(64, int(self.footprint_m / mpp))
        rx, ry = (xf - xmin) * 256, (yf - ymin) * 256
        box = (int(rx - px / 2), int(ry - px / 2), int(rx + px / 2), int(ry + px / 2))
        buf = io.BytesIO()
        canvas.crop(box).save(buf, "JPEG", quality=90)
        return buf.getvalue()
