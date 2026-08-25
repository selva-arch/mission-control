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


def _extraction_request(rows: list, model: str) -> dict:
    """Build the kwargs for one extraction request.

    Shared by the synchronous path (client.messages.create(**kwargs)) and the
    Batches API path (wrapped in a Request). Two copies of this would drift —
    a change made to one and not the other would silently change what gets
    extracted depending on which path ran.
    """
    payload = "\n\n---\n\n".join(_ad_block(r) for r in rows)
    return {
        "model": model,
        "max_tokens": 16000,
        "system": [{
            "type": "text",
            "text": SYSTEM_PROMPT,
            # Instructions are identical across every batch, so cache them.
            # Batches API requests do not share a cache with each other, but
            # this still helps when mixing batch and sync calls in one run.
            "cache_control": {"type": "ephemeral"},
        }],
        "messages": [{
            "role": "user",
            "content": (
                f"Extract one record for each of the {len(rows)} ads below. "
                f"Return them in the same order, echoing each ad_id exactly."
                f"\n\n{payload}"
            ),
        }],
        "output_config": {
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            # Reading fields off ad copy is mechanical work, not reasoning.
            # Left at the default effort the model deliberates over every batch,
            # which on a few hundred batches costs real time and money for no
            # measurable gain in extraction accuracy.
            "effort": "low",
        },
    }


def _parse_extraction_text(text: str) -> list[dict]:
    if not text:
        return []
    try:
        return json.loads(text).get("ads", [])
    except json.JSONDecodeError:
        return []


def enrich_batch(rows: list, model: str = DEFAULT_MODEL) -> list[dict]:
    """Extract structured fields for a batch of ad rows, synchronously.

    Returns one dict per ad the model returned. Ads it fails to return are
    simply absent — the caller keeps them pending rather than storing a
    fabricated record.
    """
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(**_extraction_request(rows, model))
    text = next((b.text for b in response.content if b.type == "text"), "")
    return _parse_extraction_text(text)


# ---------------------------------------------------------------------------
# Message Batches API — bulk enrichment.
#
# The synchronous path above is one request at a time: fine for a handful of
# ads, wrong for thousands. A sweep this size (tens of thousands of pending
# ads) needs a request submitted once and collected later, at half the price,
# with no requirement to keep a laptop awake for hours.
#
# Recovery is by absence rather than bookkeeping: an ad with no ad_offers row
# is simply still pending, whether because its request errored, its response
# failed the id-echo guard, or it was never submitted. The next --enrich-submit
# picks up exactly the ads still missing a row — no separate tracking of which
# custom_id covered which ad is needed.
# ---------------------------------------------------------------------------

def _open_batch(conn, store):
    """The batch currently awaiting collection, if any."""
    return store.latest_enrich_batch(conn, open_only=True)


def submit_batch(conn, store, model: str = DEFAULT_MODEL,
                 limit: int | None = None, verbose: bool = True,
                 watchlist_only: bool = False):
    """Submit every pending ad as one Message Batch. Returns the batch id.

    Refuses to run while a previous batch is still open, so the same ads
    cannot be submitted twice while their results are outstanding.
    """
    open_batch = _open_batch(conn, store)
    if open_batch:
        if verbose:
            print(f"[enrich] batch {open_batch['batch_id']} is still open "
                  f"({open_batch['status']}) — run --enrich-fetch first")
        return None

    rows = store.ads_needing_enrichment(conn, ENRICH_VERSION, limit,
                                       watchlist_only=watchlist_only)
    if not rows:
        if verbose:
            scope = " on the watchlist" if watchlist_only else ""
            print(f"[enrich] nothing pending{scope}")
        return None
    if not available():
        try:
            import anthropic  # noqa: F401
            reason = "ANTHROPIC_API_KEY is not set in this shell"
        except ImportError:
            reason = "the `anthropic` package is not installed (pip install anthropic)"
        print(f"[enrich] skipped — {reason}. {len(rows)} ads are waiting.")
        return None

    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    chunks = list(_chunk(list(rows), BATCH_SIZE))
    requests = [
        Request(
            custom_id=f"req-{i}",
            params=MessageCreateParamsNonStreaming(**_extraction_request(chunk, model)),
        )
        for i, chunk in enumerate(chunks)
    ]

    batch = client.messages.batches.create(requests=requests)
    store.record_enrich_batch(conn, batch.id, len(requests), batch.processing_status,
                              model=model)

    if verbose:
        scope = " (watchlist only)" if watchlist_only else ""
        print(f"[enrich] submitted batch {batch.id}: {len(requests)} requests "
              f"covering {len(rows)} ads on {model}{scope}")
        print(f"  Most batches finish within an hour, up to 24h. Check with "
              f"`python run.py --enrich-fetch`.")
    return batch.id


def fetch_batch(conn, store, verbose: bool = True) -> int:
    """Collect results for the open batch, if it has finished.

    Returns the number of ads newly enriched. 0 with no error means either
    nothing was open or the batch has not finished yet — both print a status
    line rather than looking identical to a silent no-op.
    """
    row = _open_batch(conn, store)
    if not row:
        if verbose:
            print("[enrich] no open batch — run --enrich-submit first")
        return 0
    if not available():
        if verbose:
            print("[enrich] ANTHROPIC_API_KEY is not set in this shell")
        return 0

    import anthropic
    client = anthropic.Anthropic()
    batch = client.messages.batches.retrieve(row["batch_id"])

    if batch.processing_status != "ended":
        if verbose:
            counts = batch.request_counts
            print(f"[enrich] batch {row['batch_id']} still {batch.processing_status} "
                  f"— processing {counts.processing}, "
                  f"succeeded {counts.succeeded}, errored {counts.errored}")
        return 0

    # The model recorded at submit time — sqlite3.Row has no dict-style
    # .get(), and there is no reason to guess when it was written down.
    submitted_model = row["model"] or DEFAULT_MODEL

    done, errored, mismatched = 0, 0, 0
    for result in client.messages.batches.results(row["batch_id"]):
        if result.result.type != "succeeded":
            errored += 1
            continue
        msg = result.result.message
        text = next((b.text for b in msg.content if b.type == "text"), "")
        for item in _parse_extraction_text(text):
            ad_id = str(item.get("ad_id", ""))
            if not ad_id:
                mismatched += 1
                continue
            store.record_offer(conn, ad_id, ENRICH_VERSION, submitted_model, item)
            done += 1
    conn.commit()
    store.finish_enrich_batch(conn, row["batch_id"], done, errored)

    if verbose:
        print(f"[enrich] batch {row['batch_id']}: {done} ads enriched, "
              f"{errored} requests errored, {mismatched} malformed ids")
        if errored or mismatched:
            print(f"  Affected ads remain pending and will be picked up by "
                  f"the next --enrich-submit automatically.")
    return done


# ---------------------------------------------------------------------------
# Adversarial audit — claude-fable-5 checks a sample of what enrichment wrote.
#
# Bulk extraction runs on Sonnet because reading a stated price off ad copy is
# mostly transcription. Checking that extraction for the specific ways it can
# be subtly wrong — a finance repayment mistaken for the price, the rebate
# basis inverted, kW read as kWh — is a reasoning task, and that is where the
# extra capability is worth spending. The audit never overwrites the v1
# extraction; it writes a separate verdict alongside it.
# ---------------------------------------------------------------------------

AUDIT_VERSION = "audit-f5"
AUDIT_MODEL = "claude-fable-5"
AUDIT_BATCH_SIZE = 5  # smaller than extraction: each item now carries a verdict too

AUDIT_SYSTEM_PROMPT = """You are adversarially checking structured extractions another model made from Australian solar/battery/EV-charger/heat-pump ads. Your job is to find mistakes, not to rubber-stamp a plausible-looking record.

For each ad you are given the original copy AND the extraction already made from it. Check every field against the copy. Specifically hunt for these known failure modes, in order of how often they occur:
- price_aud is actually a weekly/monthly finance repayment ("$99/week"), a rebate amount, a savings claim, or a deposit — not the system's stated price.
- price_basis contradicts what the ad says: it states the price is after a rebate but basis is "pre_rebate" (or vice versa), or a basis was guessed when the ad never actually states one (should be "unknown").
- capacity_kwh and system_kw are swapped, or a kW array size was recorded as battery kWh.
- brand or model_name was invented rather than read from the copy.
- product_category is wrong (e.g. an EV-charger ad tagged as battery).
- If the ad body is an unrendered template ({{...}} placeholders) and the extraction still produced a confident price or capacity, that value is only legitimate if it plainly came from readable card text or OCR also shown to you — otherwise treat it as fabricated.

Mark verdict "wrong" if ANY field fails this check and list every field that is wrong, by name. Mark "confirmed" only if every field held up against the copy. When wrong, give a one-sentence note pointing at the actual text that contradicts the extraction.
"""

AUDIT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "ad_id": {"type": "string"},
        "verdict": {"type": "string", "enum": ["confirmed", "wrong"]},
        "wrong_fields": {"type": "array", "items": {"type": "string"}},
        "note": {"type": ["string", "null"]},
    },
    "required": ["ad_id", "verdict", "wrong_fields", "note"],
    "additionalProperties": False,
}

AUDIT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"audits": {"type": "array", "items": AUDIT_ITEM_SCHEMA}},
    "required": ["audits"],
    "additionalProperties": False,
}


def sample_for_audit(conn, n: int = 200, watchlist_only: bool = False) -> list:
    """Stratified sample of enriched ads to check.

    Weighted toward ads with a detected price — that is what the Market tab
    reads — spread across rebate basis so no basis is invisible to the audit,
    and disproportionately including catalogue (dynamic) ads, since their
    recovered copy is the least reliable input the extraction saw.
    """
    import random

    sql = """
        SELECT m.ad_archive_id, a.page_name, a.title, a.body_text, a.caption,
               a.link_description, a.cta_text, a.ocr_text, a.is_dynamic,
               m.price, m.basis, m.category
          FROM ad_market m JOIN ads a ON a.ad_archive_id = m.ad_archive_id
         WHERE EXISTS (SELECT 1 FROM ad_offers o
                        WHERE o.ad_archive_id = m.ad_archive_id
                          AND o.enrich_version = ?)
    """
    if watchlist_only:
        sql += " AND m.on_watchlist = 1"
    pool = conn.execute(sql, (ENRICH_VERSION,)).fetchall()
    pool_by_id = {r["ad_archive_id"]: r for r in pool}
    if len(pool_by_id) <= n:
        return list(pool_by_id.values())

    rng = random.Random(42)  # deterministic — a re-run audits the same slice
    dynamic_ids = [aid for aid, r in pool_by_id.items() if r["is_dynamic"]]
    priced_ids = [aid for aid, r in pool_by_id.items()
                  if r["price"] is not None and not r["is_dynamic"]]
    unpriced_ids = [aid for aid, r in pool_by_id.items()
                    if r["price"] is None and not r["is_dynamic"]]

    chosen: set = set()
    dyn_quota = min(len(dynamic_ids), max(10, n // 8))
    chosen.update(rng.sample(dynamic_ids, dyn_quota))

    priced_quota = min(len(priced_ids), int((n - len(chosen)) * 0.7))
    by_basis: dict = {}
    for aid in priced_ids:
        by_basis.setdefault(pool_by_id[aid]["basis"], []).append(aid)
    if by_basis:
        per_basis = max(1, priced_quota // len(by_basis))
        for ids in by_basis.values():
            chosen.update(rng.sample(ids, min(len(ids), per_basis)))

    unpriced_pool = [aid for aid in unpriced_ids if aid not in chosen]
    remaining = max(0, n - len(chosen))
    chosen.update(rng.sample(unpriced_pool, min(len(unpriced_pool), remaining)))

    if len(chosen) < n:
        rest = [aid for aid in pool_by_id if aid not in chosen]
        chosen.update(rng.sample(rest, min(len(rest), n - len(chosen))))

    return [pool_by_id[aid] for aid in list(chosen)[:n]]


def _fetch_extractions(conn, ad_ids: list) -> dict:
    """The current v1 extraction for each ad, keyed by ad_archive_id."""
    if not ad_ids:
        return {}
    placeholders = ",".join("?" * len(ad_ids))
    rows = conn.execute(f"""
        SELECT * FROM ad_offers
         WHERE enrich_version = ? AND ad_archive_id IN ({placeholders})
    """, [ENRICH_VERSION, *ad_ids]).fetchall()
    return {r["ad_archive_id"]: dict(r) for r in rows}


def _audit_block(row, extraction: dict) -> str:
    parts = [_ad_block(row)]
    shown = {k: v for k, v in extraction.items()
            if k not in ("id", "ad_archive_id", "enrich_version", "model",
                        "raw_json", "created_at")}
    parts.append(f"extraction_made: {json.dumps(shown)}")
    return "\n".join(parts)


def audit_batch(rows: list, extractions: dict, model: str = AUDIT_MODEL) -> list[dict]:
    """Adversarially verify one batch of extractions on Fable 5."""
    import anthropic

    client = anthropic.Anthropic()
    payload = "\n\n---\n\n".join(_audit_block(r, extractions.get(r["ad_archive_id"], {}))
                                 for r in rows)

    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        # Fable 5 needs the fallback opted in explicitly; a refusal without it
        # just stops rather than retrying on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=[{
            "type": "text",
            "text": AUDIT_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{
            "role": "user",
            "content": (
                f"Audit each of the {len(rows)} extractions below against its "
                f"ad copy. Return one verdict per ad, in the same order, "
                f"echoing each ad_id exactly.\n\n{payload}"
            ),
        }],
        output_config={"format": {"type": "json_schema", "schema": AUDIT_RESPONSE_SCHEMA}},
    )

    if response.stop_reason == "refusal":
        return []
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        return []
    try:
        return json.loads(text).get("audits", [])
    except json.JSONDecodeError:
        return []


def run_audit(conn, store, n: int = 200, model: str = AUDIT_MODEL,
             verbose: bool = True, watchlist_only: bool = False) -> dict:
    """Sample enriched ads, adversarially verify them, and report a summary.

    Returns the aggregate report dict; also printed. Does not modify v1
    extractions — verdicts land in enrich_audits under AUDIT_VERSION.
    """
    sample = sample_for_audit(conn, n, watchlist_only=watchlist_only)
    if not sample:
        if verbose:
            print("[audit] no enriched ads to check yet — run enrichment first")
        return {}
    if not available():
        if verbose:
            print("[audit] ANTHROPIC_API_KEY is not set in this shell")
        return {}

    extractions = _fetch_extractions(conn, [r["ad_archive_id"] for r in sample])
    by_id = {r["ad_archive_id"]: r for r in sample}

    field_wrong: dict = {}
    field_total: dict = {}
    examples: list = []
    checked = 0

    if verbose:
        print(f"[audit] checking {len(sample)} ads on {model}...")

    for batch in _chunk(sample, AUDIT_BATCH_SIZE):
        try:
            results = audit_batch(batch, extractions, model=model)
        except KeyboardInterrupt:
            print(f"\n[audit] interrupted after {checked}/{len(sample)} — "
                  f"verdicts already written are kept")
            break
        except Exception as e:
            print(f"  ! audit batch failed: {e}")
            continue

        for item in results:
            ad_id = str(item.get("ad_id", ""))
            if ad_id not in by_id:
                continue
            verdict = item.get("verdict", "wrong")
            wrong_fields = item.get("wrong_fields") or []
            store.record_audit(conn, ad_id, AUDIT_VERSION, model, verdict,
                               wrong_fields, item.get("note") or "")
            checked += 1

            ext = extractions.get(ad_id, {})
            fields_present = [k for k, v in ext.items()
                              if k not in ("id", "ad_archive_id", "enrich_version",
                                          "model", "raw_json", "created_at")
                              and v not in (None, "", "[]")]
            for f in fields_present:
                field_total[f] = field_total.get(f, 0) + 1
            for f in wrong_fields:
                field_wrong[f] = field_wrong.get(f, 0) + 1
            if verdict == "wrong" and len(examples) < 10:
                examples.append({
                    "ad_id": ad_id, "advertiser": by_id[ad_id]["page_name"],
                    "wrong_fields": wrong_fields, "note": item.get("note"),
                })
        conn.commit()
        if verbose:
            print(f"  [audit] {checked}/{len(sample)}")

    report = {
        "checked": checked,
        "field_wrong": field_wrong,
        "field_total": field_total,
        "examples": examples,
    }

    if verbose:
        _print_audit_report(conn, checked, field_wrong, field_total, examples)
    return report


def _print_audit_report(conn, checked: int, field_wrong: dict, field_total: dict,
                        examples: list) -> None:
    confirmed = conn.execute(
        "SELECT COUNT(*) FROM enrich_audits WHERE audit_version = ? AND verdict = 'confirmed'",
        (AUDIT_VERSION,)).fetchone()[0]
    wrong = conn.execute(
        "SELECT COUNT(*) FROM enrich_audits WHERE audit_version = ? AND verdict = 'wrong'",
        (AUDIT_VERSION,)).fetchone()[0]

    print("\n" + "=" * 60)
    print(f"  audited      : {checked}")
    print(f"  confirmed    : {confirmed}")
    print(f"  wrong        : {wrong}")

    if field_wrong:
        print("\n  field error rates (of ads where that field was populated):")
        flagged = []
        for field, wrong_n in sorted(field_wrong.items(), key=lambda kv: -kv[1]):
            total = field_total.get(field, wrong_n)
            rate = wrong_n / total if total else 0
            flag = " ⚠ re-run this slice" if rate > 0.05 else ""
            print(f"    {field:<20} {wrong_n:>4}/{total:<4} ({rate:.0%}){flag}")
            if rate > 0.05:
                flagged.append(field)

        if flagged:
            print(f"\n  ⚠ Fields above 5% wrong: {', '.join(flagged)}.")
            print(f"    Recommendation: re-run enrichment for ads where that "
                  f"field is populated on a stronger model, e.g.:")
            print(f"    python run.py --enrich-submit  # after clearing those "
                  f"ad_offers rows so they are picked up again")
        else:
            print("\n  No field exceeds the 5% error threshold — v1 extractions "
                  "look trustworthy at this sample size.")

    if examples:
        print(f"\n  sample of wrong extractions:")
        for ex in examples[:5]:
            print(f"    {ex['ad_id']} ({ex['advertiser'][:24]}): "
                  f"{', '.join(ex['wrong_fields'])} — {ex['note']}")
    print("=" * 60)


def enrich_pending(conn, store, model: str = DEFAULT_MODEL,
                   limit: int | None = None, verbose: bool = True,
                   watchlist_only: bool = False) -> int:
    """Enrich every ad lacking an extraction at the current version.

    Returns the number of ads enriched. Safe to interrupt and re-run — work
    already committed is never repeated.
    """
    rows = store.ads_needing_enrichment(conn, ENRICH_VERSION, limit,
                                       watchlist_only=watchlist_only)
    if not rows:
        if verbose:
            scope = " on the watchlist" if watchlist_only else ""
            print(f"[enrich] nothing pending{scope}")
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
        except KeyboardInterrupt:
            # Deliberate stop: report where we got to rather than dumping a
            # traceback. Everything already committed stays cached, so a
            # re-run resumes here and pays nothing for completed work.
            conn.commit()
            print(f"\n[enrich] interrupted after {done}/{len(rows)} — "
                  f"completed extractions are cached; "
                  f"re-run `--enrich-only` to continue")
            return done
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
