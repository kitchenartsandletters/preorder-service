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
- Effective date changed                     -> one row, old -> new, with
  change_source chosen by which input produced the new date:
    override present          -> "override_date"
    else pub_date present     -> "shopify_pub_date"
    else (date-tag fallback)  -> "legacy_tag_fallback"
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


def test_pub_date_change_records_shopify_pub_date(fake_supabase):
    run(fake_supabase, pub_date_raw="2099-10-01")
    run(fake_supabase, pub_date_raw="2099-11-01")

    change = history(fake_supabase)[-1]
    assert change["old_effective_pub_date"] == "2099-10-01"
    assert change["new_effective_pub_date"] == "2099-11-01"
    assert change["change_source"] == "shopify_pub_date"


def test_override_change_records_override_date(fake_supabase):
    run(fake_supabase, pub_date_raw="2099-10-01")
    run(fake_supabase, pub_date_raw="2099-10-01", override_date_raw="2099-12-01")

    change = history(fake_supabase)[-1]
    assert change["old_effective_pub_date"] == "2099-10-01"
    assert change["new_effective_pub_date"] == "2099-12-01"
    assert change["change_source"] == "override_date"


def test_tag_fallback_change_records_legacy_tag_fallback(fake_supabase):
    # Reachable only on this unit path: on the live Shopify path date tags are
    # always empty (DOCS_STATUS E2), so production has zero such rows.
    run(fake_supabase, pub_date_raw=None, date_tags_raw=["10-01-2099"])
    run(fake_supabase, pub_date_raw=None, date_tags_raw=["10-01-2099", "11-15-2099"])

    change = history(fake_supabase)[-1]
    assert change["old_effective_pub_date"] == "2099-10-01"
    assert change["new_effective_pub_date"] == "2099-11-15"
    assert change["change_source"] == "legacy_tag_fallback"


def test_losing_the_date_writes_no_row(fake_supabase):
    run(fake_supabase, pub_date_raw="2099-10-01")
    run(fake_supabase, pub_date_raw=None)

    assert len(history(fake_supabase)) == 1  # baseline only; no NULL-new row
