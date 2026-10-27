"""
Pub-date history tests (orchestrator "Phase 10" block), run end-to-end through
`classify_and_persist_product` against the in-memory FakeSupabase.

These pin the CURRENT behavior of preorder.pubdate_history writes, which rev2
Move 1 extends (see docs/phase_unified_pubdate_rev2.md §3.1). Current rules
(verified against orchestrator.py):

- First classification with a resolved date  -> one `initial_baseline` row
  (old_effective_pub_date = None).
- First classification with no date          -> no row.
- Effective date unchanged vs product_status -> no row (idempotent).
- Effective date changed                     -> one row, old -> new, whose
  change_source names the ACTOR, not the mechanism (rev2 Move 1b):
    explicit change_source argument -> that value (the editor passes "ui_change")
    else an open write intent       -> "ui_change", with changed_by/reason
    else                            -> "shopify_direct_edit"
  The old mechanism labels ("shopify_pub_date", "override_date",
  "legacy_tag_fallback") are frozen: existing rows keep them, nothing writes
  them any more.
- Date lost (new resolves to None)           -> no row
  (new_effective_pub_date is NOT NULL in the live table).

No monkeypatching of the classifier: the second run "sees" the stored date
because the orchestrator itself persisted product_status on the first run.
The previous version of this file used a fake without `.schema()` and a
metadata stub missing fields the orchestrator reads, so all four tests errored
before reaching the logic under test.
"""

from domain_models import ProductMetadata
from orchestrator import classify_and_persist_product


def make_product(**overrides) -> ProductMetadata:
    base = dict(
        product_id=1,
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags_raw=[],
        pub_date_raw="2099-10-01",
        override_date_raw=None,
        inventory=0,
    )
    base.update(overrides)
    return ProductMetadata(**base)


def run(sb, **overrides):
    return classify_and_persist_product(
        supabase=sb, product_metadata=make_product(**overrides), engine_version="test-v"
    )


def history(sb):
    return sb.rows("preorder", "pubdate_history")


def test_baseline_row_on_first_classification(fake_supabase):
    run(fake_supabase)

    rows = history(fake_supabase)
    assert len(rows) == 1
    row = rows[0]
    assert row["product_id"] == 1
    assert row["old_effective_pub_date"] is None
    assert row["new_effective_pub_date"] == "2099-10-01"
    assert row["change_source"] == "initial_baseline"
    assert row["engine_version"] == "test-v"
    assert row["changed_at"]


def test_no_baseline_row_when_no_date(fake_supabase):
    run(fake_supabase, pub_date_raw=None)
    assert history(fake_supabase) == []


def test_unchanged_date_writes_nothing_on_rerun(fake_supabase):
    run(fake_supabase)
    run(fake_supabase)
    run(fake_supabase)
    assert len(history(fake_supabase)) == 1  # baseline only


def test_unattributed_change_is_a_direct_shopify_edit(fake_supabase):
    """No caller label and no open intent: somebody edited it in Shopify admin."""
    run(fake_supabase, pub_date_raw="2099-10-01")
    run(fake_supabase, pub_date_raw="2099-11-01")

    change = history(fake_supabase)[-1]
    assert change["old_effective_pub_date"] == "2099-10-01"
    assert change["new_effective_pub_date"] == "2099-11-01"
    assert change["change_source"] == "shopify_direct_edit"


def test_explicit_change_source_wins(fake_supabase):
    run(fake_supabase, pub_date_raw="2099-10-01")
    classify_and_persist_product(
        supabase=fake_supabase,
        product_metadata=make_product(pub_date_raw="2099-11-01"),
        engine_version="test-v",
        change_source="ui_change",
        change_metadata={"changed_by": "gil", "reason": "publisher moved it"},
    )

    change = history(fake_supabase)[-1]
    assert change["change_source"] == "ui_change"
    assert change["metadata"]["changed_by"] == "gil"
    assert change["metadata"]["reason"] == "publisher moved it"


def test_matching_write_intent_is_claimed_and_labels_the_row(fake_supabase):
    """The dashboard's own edit must not be reported as an out-of-band change."""
    run(fake_supabase, pub_date_raw="2099-10-01")
    fake_supabase.seed("preorder", "pubdate_write_intent", [{
        "id": "intent-1", "product_id": 1, "new_date": "2099-11-01",
        "changed_by": "gil", "reason": "signing moved", "consumed_at": None,
        "created_at": "2099-01-01T00:00:00+00:00",
    }])

    run(fake_supabase, pub_date_raw="2099-11-01")

    change = history(fake_supabase)[-1]
    assert change["change_source"] == "ui_change"
    assert change["metadata"]["changed_by"] == "gil"
    assert change["metadata"]["reason"] == "signing moved"
    # ...and the intent is consumed, so a later unrelated edit is not mislabelled.
    assert fake_supabase.rows("preorder", "pubdate_write_intent")[0]["consumed_at"] is not None


def test_intent_for_a_different_date_is_not_claimed(fake_supabase):
    run(fake_supabase, pub_date_raw="2099-10-01")
    fake_supabase.seed("preorder", "pubdate_write_intent", [{
        "id": "intent-2", "product_id": 1, "new_date": "2099-12-25",
        "changed_by": "gil", "reason": None, "consumed_at": None,
        "created_at": "2099-01-01T00:00:00+00:00",
    }])

    run(fake_supabase, pub_date_raw="2099-11-01")

    assert history(fake_supabase)[-1]["change_source"] == "shopify_direct_edit"
    assert fake_supabase.rows("preorder", "pubdate_write_intent")[0]["consumed_at"] is None


def test_a_consumed_intent_is_not_reused(fake_supabase):
    run(fake_supabase, pub_date_raw="2099-10-01")
    fake_supabase.seed("preorder", "pubdate_write_intent", [{
        "id": "intent-3", "product_id": 1, "new_date": "2099-11-01",
        "changed_by": "gil", "reason": None,
        "consumed_at": "2099-01-02T00:00:00+00:00",
        "created_at": "2099-01-01T00:00:00+00:00",
    }])

    run(fake_supabase, pub_date_raw="2099-11-01")

    assert history(fake_supabase)[-1]["change_source"] == "shopify_direct_edit"


def test_losing_the_date_writes_no_row(fake_supabase):
    run(fake_supabase, pub_date_raw="2099-10-01")
    run(fake_supabase, pub_date_raw=None)

    assert len(history(fake_supabase)) == 1  # baseline only; no NULL-new row
