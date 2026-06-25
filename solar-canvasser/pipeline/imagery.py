"""
Aerial imagery per rooftop — behind a swappable interface.

The interface matters: Google imagery is cheap and fine for the *detection*
pass, but its terms restrict use in *printed* mail. Keeping a clean
`ImagerySource` boundary lets you detect with Google and print with a licensed
or self-captured image, with no change to the rest of the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import requests


class ImagerySource(ABC):
    @abstractmethod
    def tile(self, lat: float, lon: float) -> bytes:
        """Return PNG/JPEG bytes of a top-down tile centred on (lat, lon)."""


class GoogleStaticImagery(ImagerySource):
    """Google Maps Static API satellite tile. Good for detection/prototyping.
    See module note + ARCHITECTURE.md on print licensing before mailing."""

    URL = "https://maps.googleapis.com/maps/api/staticmap"

    def __init__(self, api_key: str, zoom: int = 20, size: str = "640x640"):
        self.api_key = api_key
        self.zoom = zoom
        self.size = size

    def tile(self, lat: float, lon: float) -> bytes:
        params = {
            "center": f"{lat},{lon}",
            "zoom": self.zoom,
            "size": self.size,
            "maptype": "satellite",
            "key": self.api_key,
        }
        r = requests.get(self.URL, params=params, timeout=30)
        r.raise_for_status()
        return r.content


class LocalImagery(ImagerySource):
    """Use pre-captured tiles (drone / licensed export) named '<lat>_<lon>.jpg'
    in a folder — for the licensed, mail-safe printed asset."""

    def __init__(self, folder: str):
        self.folder = Path(folder)

    def tile(self, lat: float, lon: float) -> bytes:
        p = self.folder / f"{lat:.6f}_{lon:.6f}.jpg"
        if not p.exists():
            raise FileNotFoundError(f"No local tile for {lat},{lon} ({p})")
        return p.read_bytes()


def build_source(cfg: dict) -> ImagerySource:
    img = cfg.get("imagery", {})
    src = img.get("source", "google")
    if src == "google":
        return GoogleStaticImagery(cfg["google_api_key"],
                                   zoom=img.get("zoom", 20),
                                   size=img.get("size", "640x640"))
    if src == "local":
        return LocalImagery(img.get("folder", "tiles"))
    if src == "nearmap":
        from .nearmap import NearmapTiles  # lazy: avoids circular import
        return NearmapTiles(cfg["nearmap"]["api_key"],
                            zoom=img.get("zoom", 21),
                            grid=img.get("tile_grid", 3),
                            footprint_m=img.get("footprint_m", 40))
    raise ValueError(f"Unknown imagery source: {src}")
