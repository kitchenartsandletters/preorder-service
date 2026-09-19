"""
Persistence contract tests for `persistence.persist_classification`.

Asserts the CURRENT contract (verified against persistence.py):
- writes to schema `preorder`, table `product_status` (schema-scoped call);
- upserts with on_conflict="product_id" (one row per product, idempotent);
- serializes effective_pub_date to an ISO string (or None);
- normalizes metadata_snapshot to a fixed key set (missing keys -> None);
- defaults engine_version to persistence.ENGINE_VERSION, overridable.

The previous version of this file asserted the pre-`.schema()` call style
(`table("preorder.product_status")`), a `date` object for effective_pub_date,
and a raw pass-through snapshot — none of which match production any longer.
"""

from datetime import date

import persistence
from persistence import persist_classification
from classification.engine import ClassificationResult

SNAPSHOT_KEYS = {
    "product_id",
    "title",
    "vendor",
    "isbn",
    "tags",
    "pub_date",
    "inventory",
    "override_date",
    "in_preorder_collection",
}


def make_classification(status="active_preorder", anomaly_type=None, effective_pub_date=None):
    return ClassificationResult(
        status=status,
        anomaly_type=anomaly_type,
        effective_pub_date=effective_pub_date,
    )


def test_writes_to_schema_scoped_product_status(fake_supabase):
    persist_classification(
        supabase=fake_supabase,
        product_id=123,
        classification=make_classification(),
    )

    assert len(fake_supabase.calls_to("preorder", "product_status", "upsert")) == 1
    # A regression to the old unscoped call style would land here instead.
    assert fake_supabase.calls_to(None, "preorder.product_status") == []


def test_upsert_is_keyed_on_product_id_and_idempotent(fake_supabase):
    for status in ("active_preorder", "historical_preorder"):
        persist_classification(
            supabase=fake_supabase,
            product_id=456,
            classification=make_classification(status=status),
        )

    call = fake_supabase.calls_to("preorder", "product_status", "upsert")[0]
    assert call["on_conflict"] == "product_id"

    rows = fake_supabase.rows("preorder", "product_status")
    assert len(rows) == 1
    assert rows[0]["status"] == "historical_preorder"


def test_payload_fields_and_serialization(fake_supabase):
    persist_classification(
        supabase=fake_supabase,
        product_id=999,
        classification=make_classification(
            status="anomaly_missing_tag",
            anomaly_type="anomaly_missing_tag",
            effective_pub_date=date(2026, 5, 1),
        ),
        metadata_snapshot={"tags": ["preorder"], "inventory": 0},
    )

    row = fake_supabase.rows("preorder", "product_status")[0]
    assert row["product_id"] == 999
    assert row["status"] == "anomaly_missing_tag"
    assert row["anomaly_type"] == "anomaly_missing_tag"
    assert row["effective_pub_date"] == "2026-05-01"
    assert row["last_classified_at"]

    snap = row["metadata_snapshot"]
    assert set(snap) == SNAPSHOT_KEYS
    assert snap["product_id"] == 999
    assert snap["tags"] == ["preorder"]
    assert snap["inventory"] == 0
    assert snap["title"] is None and snap["override_date"] is None


def test_missing_date_and_snapshot_persist_as_none(fake_supabase):
    persist_classification(
        supabase=fake_supabase,
        product_id=1,
        classification=make_classification(effective_pub_date=None),
    )
    row = fake_supabase.rows("preorder", "product_status")[0]
    assert row["effective_pub_date"] is None
    assert row["metadata_snapshot"] is None


def test_engine_version_default_and_override(fake_supabase):
    persist_classification(
        supabase=fake_supabase, product_id=1, classification=make_classification()
    )
    persist_classification(
        supabase=fake_supabase,
        product_id=2,
        classification=make_classification(),
        engine_version="custom-version",
    )
    by_id = {r["product_id"]: r for r in fake_supabase.rows("preorder", "product_status")}
    assert by_id[1]["engine_version"] == persistence.ENGINE_VERSION
    assert by_id[2]["engine_version"] == "custom-version"
