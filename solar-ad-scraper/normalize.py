"""
Normalise ads to a comparable basis: $ per usable kWh, plus an estimated
post-rebate price under the federal Cheaper Home Batteries Program.

The headline number in an ad is not comparable on its own — a 27 kWh system at
$5,981 and a 15 kWh system at $6,990 only become comparable once you divide by
usable capacity. This module also estimates the federal rebate so you can tell
whether an advertised price already has it baked in.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RebateModel:
    dollars_per_kwh_tier1: float = 272
    dollars_per_kwh_tier2: float = 163
    dollars_per_kwh_tier3: float = 41
    tier1_cap: float = 14
    tier2_cap: float = 28
    tier3_cap: float = 50

    def estimate(self, kwh: float) -> float:
        """Estimated federal rebate ($) for a battery of the given kWh."""
        if not kwh or kwh <= 0:
            return 0.0
        capped = min(kwh, self.tier3_cap)
        t1 = min(capped, self.tier1_cap)
        t2 = max(0.0, min(capped, self.tier2_cap) - self.tier1_cap)
        t3 = max(0.0, capped - self.tier2_cap)
        return (
            t1 * self.dollars_per_kwh_tier1
            + t2 * self.dollars_per_kwh_tier2
            + t3 * self.dollars_per_kwh_tier3
        )


@dataclass
class Comparison:
    price: int | None
    capacity_kwh: float | None
    dollars_per_kwh: float | None
    est_rebate: float | None
    price_if_pre_rebate: int | None  # what you'd pay if the ad price is BEFORE rebate


def choose_price(prices: list[int]) -> int | None:
    """Pick the headline price. Ads usually lead with the offer price, which is
    typically the largest prominent figure; we take the max plausible value."""
    return max(prices) if prices else None


def choose_capacity(caps: list[float]) -> float | None:
    """Pick the most likely system capacity (the largest stated kWh, since ads
    headline total capacity)."""
    return max(caps) if caps else None


def compare(prices: list[int], caps: list[float], rebate: RebateModel) -> Comparison:
    price = choose_price(prices)
    cap = choose_capacity(caps)
    dpk = round(price / cap, 1) if price and cap else None
    reb = round(rebate.estimate(cap)) if cap else None
    pre = round(price - reb) if (price is not None and reb is not None) else None
    return Comparison(
        price=price,
        capacity_kwh=cap,
        dollars_per_kwh=dpk,
        est_rebate=reb,
        price_if_pre_rebate=pre,
    )


def deal_score(cmp: Comparison, days_running: int | None) -> float:
    """Lower is better. Primarily $/usable kWh, with a small discount for
    long-running ads (your hypothesis: stable, public, real price points)."""
    if cmp.dollars_per_kwh is None:
        return float("inf")
    score = cmp.dollars_per_kwh
    if days_running and days_running > 30:
        # Up to ~15% nudge for ads that have run a long time without changing.
        score *= 1 - min(days_running, 365) / 365 * 0.15
    return round(score, 1)
