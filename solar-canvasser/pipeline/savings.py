"""
Estimate annual bill savings from a solar system size.

No battery assumed (matches the canvassing pitch): savings come from
self-consumed generation offsetting the retail rate, plus exported surplus
earning the feed-in tariff. Figures are INDICATIVE — always labelled as such on
the flyer (Australian Consumer Law).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SolarAssumptions:
    retail_rate: float = 0.3641       # $/kWh offset when self-consumed
    feed_in_tariff: float = 0.05      # $/kWh for exported surplus
    self_consumption: float = 0.40    # fraction of generation used on-site
    specific_yield: float = 1400      # kWh per kW per year (Adelaide ~1400)


def annual_generation_kwh(system_kw: float, a: SolarAssumptions) -> float:
    return system_kw * a.specific_yield


def annual_saving(system_kw: float, a: SolarAssumptions,
                  annual_kwh: float | None = None) -> dict:
    """Return indicative savings breakdown for a system size.

    If annual_kwh is provided (e.g. from Google Solar API), it overrides the
    specific-yield estimate.
    """
    gen = annual_kwh if annual_kwh else annual_generation_kwh(system_kw, a)
    self_used = gen * a.self_consumption
    exported = gen * (1 - a.self_consumption)
    saving = self_used * a.retail_rate + exported * a.feed_in_tariff
    return {
        "system_kw": round(system_kw, 1),
        "annual_generation_kwh": round(gen),
        "self_consumed_kwh": round(self_used),
        "exported_kwh": round(exported),
        "annual_saving_aud": round(saving),
    }
