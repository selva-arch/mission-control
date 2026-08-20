"""
LLM pass that turns messy ad copy into structured offer fields.

Regex gets the obvious figures; it cannot tell you that "$99/week for 60 months"
is financing rather than a system price, that "while stocks last" is an urgency
tactic, or that "SA" in a phone number is not a state claim. This module reads
each ad's full copy (title, body, caption, CTA and OCR text) and extracts a
consistent record.

It SUPPLEMENTS regex rather than replacing it. Both readings are written to the
database side by side (price_observations.source = 'regex' | 'llm') so the
disagreement rate is measurable instead of assumed.

Cost control: ads are batched into one request, the instruction block is cached
across requests, and every extraction is keyed by (ad_id, enrich_version) so a
re-run costs nothing for ads already done.
"""

from __future__ import annotations

import json
import os

# Bump when the prompt or schema changes; old extractions are kept alongside.
ENRICH_VERSION = "v1"

# Accuracy is the priority here — this is the step that decides what the
# dashboard reports. Override in config.yaml (enrich.model) with
# claude-sonnet-5 or claude-haiku-4-5 for cheaper bulk passes.
DEFAULT_MODEL = "claude-opus-5"

BATCH_SIZE = 8

SYSTEM_PROMPT = """\
You extract structured data from Australian solar, battery, EV-charger and \
hot-water advertisements taken from the Meta Ad Library.

Rules:
- Extract only what the ad actually states. Never infer a price, capacity or \
brand that is not present. Use null for anything absent.
- price_aud is the headline consumer price for the advertised system. Ignore \
figures that are savings, bill amounts, rebate values, deposits or weekly \
finance repayments — those belong in other fields.
- price_basis: "post_rebate" if the ad says the price is after a rebate or \
discount is applied, "pre_rebate" if it says the rebate is claimable on top, \
otherwise "unknown". Do not guess.
- capacity_kwh is battery storage in kWh. system_kw is solar array size in kW. \
These are different measurements; never copy one into the other.
- states_mentioned: Australian state/territory codes explicitly indicated by \
the copy, including via cities (Adelaide implies SA), state rebate scheme \
names, or distributor names. Use only: NSW VIC QLD SA WA TAS NT ACT. Empty \
list if the ad is generic.
- urgency_tactics: short phrases describing pressure techniques used \
(e.g. "limited stock", "ends June 30", "only 5 left").
- Text may contain OCR noise from reading the ad image. Ignore garbled \
fragments rather than inventing meaning from them.
"""

AD_SCHEMA = {
    "type": "object",
    "properties": {
        "ad_id": {"type": "string"},
        "product_category": {
            "type": "string",
            "enum": ["battery", "solar", "solar_battery", "ev_charger",
                     "heat_pump", "other"],
        },
        "brand": {"type": ["string", "null"]},
        "model_name": {"type": ["string", "null"]},
        "price_aud": {"type": ["number", "null"]},
        "price_basis": {
            "type": "string",
            "enum": ["post_rebate", "pre_rebate", "unknown"],
        },
        "capacity_kwh": {"type": ["number", "null"]},
        "system_kw": {"type": ["number", "null"]},
        "offer_type": {
            "type": "string",
            "enum": ["discount", "financing", "bundle", "free_upgrade",
                     "quote_only", "other"],
        },
        "finance_terms": {"type": ["string", "null"]},
        "claimed_rebates": {"type": "array", "items": {"type": "string"}},
        "states_mentioned": {"type": "array", "items": {"type": "string"}},
        "cities_mentioned": {"type": "array", "items": {"type": "string"}},
        "urgency_tactics": {"type": "array", "items": {"type": "string"}},
        "warranty_years": {"type": ["number", "null"]},
        "install_included": {"type": ["boolean", "null"]},
        "cta": {"type": ["string", "null"]},
    },
    "required": [
        "ad_id", "product_category", "brand", "model_name", "price_aud",
        "price_basis", "capacity_kwh", "system_kw", "offer_type",
        "finance_terms", "claimed_rebates", "states_mentioned",
        "cities_mentioned", "urgency_tactics", "warranty_years",
        "install_included", "cta",
    ],
    "additionalProperties": False,
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"ads": {"type": "array", "items": AD_SCHEMA}},
    "required": ["ads"],
    "additionalProperties": False,
}


def available() -> bool:
    """True when the SDK is installed and a credential is resolvable."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _ad_block(row) -> str:
    """Render one ad as a labelled text block for the model."""
    def field(name, value):
        value = (value or "").strip()
        return f"{name}: {value}" if value else ""

    parts = [
        f"ad_id: {row['ad_archive_id']}",
        field("advertiser", row["page_name"]),
        field("title", row["title"]),
        field("body", row["body_text"]),
        field("caption", row["caption"]),
        field("link_description", row["link_description"]),
        field("cta", row["cta_text"]),
        field("text_in_images_ocr", (row["ocr_text"] or "")[:2000]),
    ]
    return "\n".join(p for p in parts if p)


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def enrich_batch(rows: list, model: str = DEFAULT_MODEL) -> list[dict]:
    """Extract structured fields for a batch of ad rows.

    Returns one dict per ad the model returned. Ads it fails to return are
    simply absent — the caller keeps them pending rather than storing a
    fabricated record.
    """
    import anthropic

    client = anthropic.Anthropic()
    payload = "\n\n---\n\n".join(_ad_block(r) for r in rows)

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            # Instructions are identical across every batch, so cache them.
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{
            "role": "user",
            "content": (
                f"Extract one record for each of the {len(rows)} ads below. "
                f"Return them in the same order, echoing each ad_id exactly."
                f"\n\n{payload}"
            ),
        }],
        output_config={
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            # Reading fields off ad copy is mechanical work, not reasoning.
            # Left at the default effort the model deliberates over every batch,
            # which on a few hundred batches costs real time and money for no
            # measurable gain in extraction accuracy.
            "effort": "low",
        },
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        return []
    try:
        return json.loads(text).get("ads", [])
    except json.JSONDecodeError:
        return []


def enrich_pending(conn, store, model: str = DEFAULT_MODEL,
                   limit: int | None = None, verbose: bool = True) -> int:
    """Enrich every ad lacking an extraction at the current version.

    Returns the number of ads enriched. Safe to interrupt and re-run — work
    already committed is never repeated.
    """
    rows = store.ads_needing_enrichment(conn, ENRICH_VERSION, limit)
    if not rows:
        if verbose:
            print("[enrich] nothing pending")
        return 0
    if not available():
        # Report the actual cause: telling someone to install a package they
        # already have sends them the wrong way.
        try:
            import anthropic  # noqa: F401
            reason = "ANTHROPIC_API_KEY is not set in this shell"
        except ImportError:
            reason = "the `anthropic` package is not installed (pip install anthropic)"
        print(f"[enrich] skipped — {reason}. "
              f"{len(rows)} ads are waiting for structured extraction; "
              f"run `python run.py --enrich-only` once resolved.")
        return 0

    done = 0
    for batch in _chunk(list(rows), BATCH_SIZE):
        try:
            results = enrich_batch(batch, model=model)
        except Exception as e:
            print(f"  ! enrich batch failed: {e}")
            continue

        by_id = {r["ad_archive_id"]: r for r in batch}
        for item in results:
            ad_id = str(item.get("ad_id", ""))
            if ad_id not in by_id:
                # A hallucinated or mangled id — drop it rather than attaching
                # the extraction to the wrong ad.
                continue
            store.record_offer(conn, ad_id, ENRICH_VERSION, model, item)
            done += 1
        conn.commit()
        if verbose:
            pct = 100 * done // max(1, len(rows))
            print(f"  [enrich] {done}/{len(rows)} ({pct}%)")
    return done
