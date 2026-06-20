"""
Google Solar API — roof sizing and generation potential.

Returns roof area, a realistic max system size, modelled annual generation, and
per-segment geometry (azimuth/pitch) used to place panels believably in the
render. Note: the Solar API does NOT report whether panels already exist — that
is detect.py's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests


@dataclass
class RoofInsight:
    roof_area_m2: float = 0.0
    max_panel_count: int = 0
    max_system_kw: float = 0.0
    annual_kwh: float = 0.0
    segments: list = field(default_factory=list)  # azimuth/pitch per roof plane
    raw: dict = field(default_factory=dict)


class GoogleSolarAPI:
    URL = "https://solar.googleapis.com/v1/buildingInsights:findClosest"

    def __init__(self, api_key: str, panel_watts: float = 440):
        self.api_key = api_key
        self.panel_watts = panel_watts

    def insight(self, lat: float, lon: float) -> RoofInsight | None:
        params = {
            "location.latitude": lat,
            "location.longitude": lon,
            "requiredQuality": "HIGH",
            "key": self.api_key,
        }
        r = requests.get(self.URL, params=params, timeout=30)
        if r.status_code == 404:
            return None  # no coverage for this building
        r.raise_for_status()
        data = r.json()
        sp = data.get("solarPotential", {})

        # Best modelled config = the largest panel layout the API returns.
        configs = sp.get("solarPanelConfigs", [])
        best = configs[-1] if configs else {}
        panel_count = best.get("panelsCount", sp.get("maxArrayPanelsCount", 0))
        annual_kwh = best.get("yearlyEnergyDcKwh", 0.0)

        segments = [
            {
                "azimuth": s.get("azimuthDegrees"),
                "pitch": s.get("pitchDegrees"),
                "area_m2": s.get("stats", {}).get("areaMeters2"),
            }
            for s in sp.get("roofSegmentStats", [])
        ]

        return RoofInsight(
            roof_area_m2=round(sp.get("wholeRoofStats", {}).get("areaMeters2", 0.0), 1),
            max_panel_count=panel_count,
            max_system_kw=round(panel_count * self.panel_watts / 1000, 2),
            annual_kwh=round(annual_kwh),
            segments=segments,
            raw=data,
        )
