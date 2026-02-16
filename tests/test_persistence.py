from unittest.mock import MagicMock
from datetime import date

from persistence import persist_classification
from classification.engine import ClassificationResult


def make_classification(
    status="active_preorder",
    anomaly_type=None,
    effective_pub_date=None,
):
    return ClassificationResult(
        status=status,
        anomaly_type=anomaly_type,
        effective_pub_date=effective_pub_date,
    )


def test_upsert_called_with_correct_table():
    supabase = MagicMock()
    classification = make_classification()

    persist_classification(
        supabase=supabase,
        product_id=123,
        classification=classification,
    )

    supabase.table.assert_called_once_with("preorder.product_status")


def test_upsert_called_with_on_conflict_product_id():
    supabase = MagicMock()
    classification = make_classification()

    persist_classification(
        supabase=supabase,
        product_id=456,
        classification=classification,
    )

    supabase.table().upsert.assert_called_once()
    args, kwargs = supabase.table().upsert.call_args
    assert kwargs["on_conflict"] == "product_id"


def test_payload_contains_expected_fields():
    supabase = MagicMock()
    classification = make_classification(
        status="anomaly_missing_tag",
        anomaly_type="anomaly_missing_tag",
        effective_pub_date=date(2026, 5, 1),
    )

    metadata = {
        "tags": ["preorder"],
        "inventory": 0,
    }

    persist_classification(
        supabase=supabase,
        product_id=999,
        classification=classification,
        metadata_snapshot=metadata,
    )

    args, kwargs = supabase.table().upsert.call_args
    payload = args[0]

    assert payload["product_id"] == 999
    assert payload["status"] == "anomaly_missing_tag"
    assert payload["anomaly_type"] == "anomaly_missing_tag"
    assert payload["effective_pub_date"] == date(2026, 5, 1)
    assert payload["metadata_snapshot"] == metadata
    assert "last_classified_at" in payload
    assert payload["engine_version"] is not None


def test_engine_version_override():
    supabase = MagicMock()
    classification = make_classification()

    persist_classification(
        supabase=supabase,
        product_id=321,
        classification=classification,
        engine_version="custom-version",
    )

    args, _ = supabase.table().upsert.call_args
    payload = args[0]

    assert payload["engine_version"] == "custom-version"