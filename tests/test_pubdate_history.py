import pytest
from datetime import date

from classification.engine import ClassificationResult
import orchestrator


# -------------------------------------------------------------------
# Fake Supabase Stub
# -------------------------------------------------------------------

class FakeResponse:
    def __init__(self, data=None):
        self.data = data


class FakeSupabase:
    def __init__(self, existing_status=None):
        self.existing_status = existing_status
        self.insert_calls = []
        self.upsert_calls = []
        self.current_table = None

    def table(self, name):
        self.current_table = name
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def insert(self, payload):
        self.insert_calls.append((self.current_table, payload))
        return self

    def upsert(self, payload, on_conflict=None):
        self.upsert_calls.append((self.current_table, payload))
        return self

    def execute(self):
        if self.current_table == "preorder.product_status":
            return FakeResponse(self.existing_status)
        return FakeResponse()


class FakeMetadata:
    def __init__(self, product_id):
        self.product_id = product_id
        self.tags = []
        self.in_preorder_collection = False
        self.pub_date_raw = None
        self.override_date_raw = None
        self.inventory = 0

    def parsed_date_tags(self):
        return []


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------


def test_baseline_insert(monkeypatch):
    supabase = FakeSupabase(existing_status=None)
    metadata = FakeMetadata(product_id=1)

    monkeypatch.setattr(
        orchestrator,
        "classify_preorder_product",
        lambda *_: ClassificationResult(
            status="active_preorder",
            anomaly_type=None,
            effective_pub_date=date(2025, 10, 1),
        ),
    )

    orchestrator.classify_and_persist_product(
        supabase=supabase,
        product_metadata=metadata,
        engine_version="test",
    )

    history_inserts = [
        call for call in supabase.insert_calls
        if call[0] == "preorder.pubdate_history"
    ]

    assert len(history_inserts) == 1
    payload = history_inserts[0][1]
    assert payload["old_effective_pub_date"] is None
    assert payload["new_effective_pub_date"] == date(2025, 10, 1)
    assert payload["change_source"] == "initial_baseline"



def test_no_change(monkeypatch):
    supabase = FakeSupabase(
        existing_status=[{"effective_pub_date": "2025-10-01"}]
    )
    metadata = FakeMetadata(product_id=1)

    monkeypatch.setattr(
        orchestrator,
        "classify_preorder_product",
        lambda *_: ClassificationResult(
            status="active_preorder",
            anomaly_type=None,
            effective_pub_date=date(2025, 10, 1),
        ),
    )

    orchestrator.classify_and_persist_product(
        supabase=supabase,
        product_metadata=metadata,
        engine_version="test",
    )

    history_inserts = [
        call for call in supabase.insert_calls
        if call[0] == "preorder.pubdate_history"
    ]

    assert len(history_inserts) == 0



def test_shopify_pub_date_change(monkeypatch):
    supabase = FakeSupabase(
        existing_status=[{"effective_pub_date": "2025-10-01"}]
    )
    metadata = FakeMetadata(product_id=1)

    monkeypatch.setattr(
        orchestrator,
        "classify_preorder_product",
        lambda *_: ClassificationResult(
            status="active_preorder",
            anomaly_type=None,
            effective_pub_date=date(2025, 11, 1),
        ),
    )

    # Simulate shopify pub_date (no override)
    # monkeypatch.setattr(orchestrator, "ENGINE_VERSION", "test")

    orchestrator.classify_and_persist_product(
        supabase=supabase,
        product_metadata=metadata,
        engine_version="test",
    )

    history_inserts = [
        call for call in supabase.insert_calls
        if call[0] == "preorder.pubdate_history"
    ]

    assert len(history_inserts) == 1
    payload = history_inserts[0][1]
    assert payload["old_effective_pub_date"] == date(2025, 10, 1)
    assert payload["new_effective_pub_date"] == date(2025, 11, 1)



def test_idempotency(monkeypatch):
    supabase = FakeSupabase(existing_status=None)
    metadata = FakeMetadata(product_id=1)

    monkeypatch.setattr(
        orchestrator,
        "classify_preorder_product",
        lambda *_: ClassificationResult(
            status="active_preorder",
            anomaly_type=None,
            effective_pub_date=date(2025, 10, 1),
        ),
    )

    # First run
    orchestrator.classify_and_persist_product(
        supabase=supabase,
        product_metadata=metadata,
        engine_version="test",
    )

    supabase.existing_status = [
        {"effective_pub_date": "2025-10-01"}
    ]

    # Second run
    orchestrator.classify_and_persist_product(
        supabase=supabase,
        product_metadata=metadata,
        engine_version="test",
    )

    history_inserts = [
        call for call in supabase.insert_calls
        if call[0] == "preorder.pubdate_history"
    ]

    assert len(history_inserts) == 1