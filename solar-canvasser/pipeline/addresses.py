"""
Suburb -> list of addresses with rooftop coordinates.

MVP uses the OpenStreetMap Overpass API (no key): pull nodes/ways tagged with
addresses inside the named suburb. For a real mail-out, switch to G-NAF (the
authoritative free AU address file) — Overpass misses unmapped dwellings.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def geocode(address: str, user_agent: str = "solar-canvasser") -> tuple[float, float]:
    """Free address -> (lat, lon) via OpenStreetMap Nominatim (no API key).

    Nominatim asks for a descriptive User-Agent and ~1 req/sec — fine for
    individual lookups. Returns the best match's coordinates."""
    r = requests.get(NOMINATIM_URL,
                     params={"q": address, "format": "json", "limit": 1},
                     headers={"User-Agent": user_agent}, timeout=30)
    r.raise_for_status()
    res = r.json()
    if not res:
        raise ValueError(f"Could not geocode: {address!r}")
    return float(res[0]["lat"]), float(res[0]["lon"])


@dataclass
class Address:
    full: str
    lat: float
    lon: float
    housenumber: str = ""
    street: str = ""
    suburb: str = ""
    postcode: str = ""


def fetch_addresses(suburb: str, limit: int | None = None) -> list[Address]:
    """Return addressed buildings within the named suburb via Overpass.

    `suburb` like "Dulwich, SA". We match the suburb name in addr:suburb tags
    within Australia.
    """
    name = suburb.split(",")[0].strip()
    query = f"""
    [out:json][timeout:60];
    area["name"="Australia"]["admin_level"="2"]->.au;
    (
      node["addr:housenumber"]["addr:suburb"="{name}"](area.au);
      way["addr:housenumber"]["addr:suburb"="{name}"](area.au);
    );
    out center {limit or ''};
    """
    r = requests.post(OVERPASS_URL, data={"data": query}, timeout=90)
    r.raise_for_status()
    out: list[Address] = []
    seen = set()
    for el in r.json().get("elements", []):
        tags = el.get("tags", {})
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        hn = tags.get("addr:housenumber", "")
        st = tags.get("addr:street", "")
        key = (hn, st)
        if not hn or not st or key in seen:
            continue
        seen.add(key)
        out.append(Address(
            full=f"{hn} {st}, {tags.get('addr:suburb', name)} "
                 f"{tags.get('addr:state', '')} {tags.get('addr:postcode', '')}".strip(),
            lat=float(lat), lon=float(lon),
            housenumber=hn, street=st,
            suburb=tags.get("addr:suburb", name),
            postcode=tags.get("addr:postcode", ""),
        ))
    return out
