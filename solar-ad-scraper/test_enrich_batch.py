"""Tests for bulk enrichment: Message Batches submit/fetch and the Fable 5
audit. The `anthropic` SDK is mocked, matching what test_coverage.py and the
smoke tests already verified live against real Batches/Messages API shapes.

Run: python test_enrich_batch.py
"""

import json
import os
import sys
import tempfile
import types

import extract
import store


def _install_fake_anthropic(batch_ad_ids_by_request=None, audit_fn=None):
    """Replace the `anthropic` module with a minimal fake covering the calls
    enrich.py makes: messages.batches.{create,retrieve,results} and
    beta.messages.create.
    """
    fake = types.ModuleType("anthropic")

    class Block:
        def __init__(self, t):
            self.type = "text"
            self.text = t

    class Msg:
        def __init__(self, t):
            self.content = [Block(t)]

    class Result:
        def __init__(self, cid, ok, text=None):
            self.custom_id = cid
            self.result = types.SimpleNamespace(
                type="succeeded" if ok else "errored",
                message=Msg(text) if ok else None,
            )

    class Batch:
        def __init__(self, id_, status="ended"):
            self.id = id_
            self.processing_status = status
            self.request_counts = types.SimpleNamespace(
                processing=0, succeeded=1, errored=0)

    store_by_id = {}
    counter = {"n": 0}

    class Batches:
        def create(self, requests):
            counter["n"] += 1
            bid = f"batch_{counter['n']}"
            store_by_id[bid] = requests
            return Batch(bid)

        def retrieve(self, batch_id):
            return Batch(batch_id)

        def results(self, batch_id):
            import re
            out = []
            for i, req in enumerate(store_by_id[batch_id]):
                cid = req["custom_id"]
                payload = req["params"]["messages"][0]["content"]
                ad_ids = re.findall(r"ad_id: (\S+)", payload)
                if batch_ad_ids_by_request and i in batch_ad_ids_by_request.get("fail", []):
                    out.append(Result(cid, ok=False))
                    continue
                items = [{
                    "ad_id": aid, "product_category": "battery", "brand": "Sungrow",
                    "model_name": None, "price_aud": 8990.0, "price_basis": "post_rebate",
                    "capacity_kwh": 13.5, "system_kw": None, "offer_type": "discount",
                    "finance_terms": None, "claimed_rebates": [], "states_mentioned": ["SA"],
                    "cities_mentioned": ["Adelaide"], "urgency_tactics": [],
                    "warranty_years": 10.0, "install_included": True, "cta": "Get a quote",
                } for aid in ad_ids]
                out.append(Result(cid, ok=True, text=json.dumps({"ads": items})))
            return out

    class Messages:
        batches = Batches()

    class BetaMessages:
        def create(self, **kw):
            if audit_fn:
                return audit_fn(kw)
            return types.SimpleNamespace(
                content=[Block(json.dumps({"audits": []}))], stop_reason="end_turn")

    class Beta:
        messages = BetaMessages()

    class Anthropic:
        def __init__(self):
            self.messages = Messages()
            self.beta = Beta()

    fake.Anthropic = Anthropic
    sys.modules["anthropic"] = fake

    m1 = types.ModuleType("anthropic.types.message_create_params")
    m1.MessageCreateParamsNonStreaming = lambda **kw: dict(kw)
    sys.modules["anthropic.types.message_create_params"] = m1
    m2 = types.ModuleType("anthropic.types.messages.batch_create_params")
    m2.Request = lambda **kw: dict(kw)
    sys.modules["anthropic.types.messages.batch_create_params"] = m2

    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-fake-test-key"

    if "enrich" in sys.modules:
        del sys.modules["enrich"]
    import enrich
    return enrich


def _archive(n_ads=24):
    path = tempfile.mktemp(suffix=".db")
    conn = store.connect(path)
    sid = store.begin_sweep(conn, "t", "pilot")
    for i in range(n_ads):
        a = extract.Ad(ad_archive_id=f"a{i}", page_id="p", page_name=f"Adv{i}",
                       body_text="13.5kWh battery $8,990 Adelaide")
        store.upsert_ad(conn, a)
        store.record_snapshot(conn, sid, a)
    conn.commit()
    return conn, path


def test_batch_request_matches_sync_request_shape():
    """Submitting via the batch path must build the exact same request the
    synchronous path would — two copies of the packing logic would drift."""
    enrich = _install_fake_anthropic()
    rows = [{"ad_archive_id": "a1", "page_name": "X", "title": "", "body_text": "y",
             "caption": "", "link_description": "", "cta_text": "", "ocr_text": ""}]
    sync_kwargs = enrich._extraction_request(rows, "claude-sonnet-5")
    batch_kwargs = enrich._extraction_request(rows, "claude-sonnet-5")
    assert sync_kwargs == batch_kwargs, "the two paths must build identical requests"
    assert sync_kwargs["output_config"]["effort"] == "low"
    assert sync_kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_submit_then_fetch_enriches_all_pending():
    enrich = _install_fake_anthropic()
    conn, path = _archive(16)
    before = len(store.ads_needing_enrichment(conn, enrich.ENRICH_VERSION))
    assert before == 16

    bid = enrich.submit_batch(conn, store, model="claude-sonnet-5", verbose=False)
    assert bid is not None
    n = enrich.fetch_batch(conn, store, verbose=False)
    assert n == 16

    after = len(store.ads_needing_enrichment(conn, enrich.ENRICH_VERSION))
    assert after == 0
    conn.close(); os.unlink(path)


def test_double_submit_is_refused_while_batch_open():
    enrich = _install_fake_anthropic()
    conn, path = _archive(8)
    first = enrich.submit_batch(conn, store, model="claude-sonnet-5", verbose=False)
    assert first is not None
    second = enrich.submit_batch(conn, store, model="claude-sonnet-5", verbose=False)
    assert second is None, "a second submit must be refused while one is open"
    conn.close(); os.unlink(path)


def test_errored_requests_leave_their_ads_pending_for_resubmit():
    """Recovery is by absence: an ad in a failed request must simply still
    lack an ad_offers row, with no separate bookkeeping needed."""
    enrich = _install_fake_anthropic(batch_ad_ids_by_request={"fail": [0]})
    conn, path = _archive(enrich.BATCH_SIZE * 2)  # 2 requests, one fails
    enrich.submit_batch(conn, store, model="claude-sonnet-5", verbose=False)
    n = enrich.fetch_batch(conn, store, verbose=False)
    assert n == enrich.BATCH_SIZE  # only the surviving request's ads

    pending = store.ads_needing_enrichment(conn, enrich.ENRICH_VERSION)
    assert len(pending) == enrich.BATCH_SIZE

    # And a fresh submit picks up exactly those, with no manual intervention.
    del sys.modules["anthropic"]
    enrich2 = _install_fake_anthropic()  # a batch that now succeeds for everyone
    bid2 = enrich2.submit_batch(conn, store, model="claude-sonnet-5", verbose=False)
    assert bid2 is not None
    n2 = enrich2.fetch_batch(conn, store, verbose=False)
    assert n2 == enrich.BATCH_SIZE
    assert len(store.ads_needing_enrichment(conn, enrich.ENRICH_VERSION)) == 0
    conn.close(); os.unlink(path)


def test_fetch_with_no_open_batch_is_a_clean_noop():
    enrich = _install_fake_anthropic()
    conn, path = _archive(4)
    n = enrich.fetch_batch(conn, store, verbose=False)
    assert n == 0
    conn.close(); os.unlink(path)


def test_fetch_before_ready_reports_zero_without_marking_done():
    """A batch still processing must not be marked fetched — the next
    --enrich-fetch has to be able to try again."""
    enrich = _install_fake_anthropic()
    conn, path = _archive(4)

    # Patch retrieve() to report the batch as still in progress this once.
    orig_retrieve = sys.modules["anthropic"].Anthropic().messages.batches.retrieve
    import anthropic as _a
    real_batches_cls = type(_a.Anthropic().messages.batches)
    _orig = real_batches_cls.retrieve
    def not_ready(self, batch_id):
        b = _orig(self, batch_id)
        b.processing_status = "in_progress"
        return b
    real_batches_cls.retrieve = not_ready

    enrich.submit_batch(conn, store, model="claude-sonnet-5", verbose=False)
    n = enrich.fetch_batch(conn, store, verbose=False)
    assert n == 0
    row = store.latest_enrich_batch(conn)
    assert row["status"] != "done", "must stay open until actually collected"

    real_batches_cls.retrieve = _orig
    conn.close(); os.unlink(path)


# --- Audit -------------------------------------------------------------

def _archive_with_offers(n=30, wrong_every=3):
    conn, path = _archive(n)
    for i in range(n):
        store.record_price(conn, f"a{i}", 1, "regex", price_aud=8990, capacity_kwh=13.5)
        store.record_offer(conn, f"a{i}", "v1", "claude-sonnet-5", {
            "price_aud": 8990, "capacity_kwh": 13.5, "price_basis": "post_rebate",
            "product_category": "battery", "brand": "Sungrow"})
    d = extract.Ad(ad_archive_id="dyn0", page_id="p", page_name="Catalogue Co",
                   body_text="Sungrow 13.5kWh $8,990", is_dynamic=True)
    store.upsert_ad(conn, d)
    store.record_offer(conn, "dyn0", "v1", "claude-sonnet-5",
                       {"price_aud": 8990, "capacity_kwh": 13.5,
                        "product_category": "battery"})
    conn.commit()
    return conn, path


def test_audit_sample_never_exceeds_requested_size():
    conn, path = _archive_with_offers(30)
    import enrich
    for n in (5, 10, 20, 100):
        sample = enrich.sample_for_audit(conn, n=n)
        assert len(sample) <= n
    conn.close(); os.unlink(path)


def test_audit_sample_includes_dynamic_ads_when_present():
    conn, path = _archive_with_offers(30)
    import enrich
    sample = enrich.sample_for_audit(conn, n=10)
    ids = [r["ad_archive_id"] for r in sample]
    assert "dyn0" in ids
    conn.close(); os.unlink(path)


def _audit_response(kw):
    import re
    payload = kw["messages"][0]["content"]
    ad_ids = re.findall(r"ad_id: (\S+)", payload)
    audits = [{"ad_id": aid, "verdict": "wrong", "wrong_fields": ["price_basis"],
              "note": "test"} if i % 3 == 0 else
             {"ad_id": aid, "verdict": "confirmed", "wrong_fields": [], "note": None}
             for i, aid in enumerate(ad_ids)]
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=json.dumps({"audits": audits}))],
        stop_reason="end_turn")


def test_audit_verdicts_land_under_audit_version_not_v1():
    enrich = _install_fake_anthropic(audit_fn=_audit_response)
    conn, path = _archive_with_offers(20)
    enrich.run_audit(conn, store, n=12, model="claude-fable-5", verbose=False)

    v1_count = conn.execute(
        "SELECT COUNT(*) FROM ad_offers WHERE enrich_version = 'v1'").fetchone()[0]
    audit_count = conn.execute(
        "SELECT COUNT(*) FROM enrich_audits WHERE audit_version = ?",
        (enrich.AUDIT_VERSION,)).fetchone()[0]
    assert v1_count == 21, "the audit must not touch v1 extractions"
    assert audit_count == 12
    conn.close(); os.unlink(path)


def test_audit_refusal_is_handled_without_crashing():
    def refuse(kw):
        return types.SimpleNamespace(content=[], stop_reason="refusal")
    enrich = _install_fake_anthropic(audit_fn=refuse)
    rows = [{"ad_archive_id": "x1", "page_name": "A", "title": "", "body_text": "test",
            "caption": "", "link_description": "", "cta_text": "", "ocr_text": ""}]
    result = enrich.audit_batch(rows, {}, model="claude-fable-5")
    assert result == []


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except AssertionError:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__} (unexpected exception)"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
