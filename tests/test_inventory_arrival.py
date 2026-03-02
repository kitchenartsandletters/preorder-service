

from datetime import datetime, UTC
import pytest

from persistence import persist_inventory_arrival


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, store, table_name):
        self.store = store
        self.table_name = table_name
        self._filters = {}

    def select(self, *_):
        return self

    def eq(self, field, value):
        self._filters[field] = value
        return self

    def limit(self, *_):
        return self

    def insert(self, payload):
        self._insert_payload = payload
        return self

    def execute(self):
        if self.table_name not in self.store:
            self.store[self.table_name] = []

        # Handle SELECT
        if hasattr(self, "_filters") and self._filters:
            product_id = self._filters.get("product_id")
            rows = [
                r for r in self.store[self.table_name]
                if r.get("product_id") == product_id
            ]
            return FakeResponse(rows)

        # Handle INSERT
        if hasattr(self, "_insert_payload"):
            self.store[self.table_name].append(self._insert_payload)
            return FakeResponse([self._insert_payload])

        return FakeResponse([])


class FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return FakeTable(self.store, name)


# -----------------------
# Test Cases
# -----------------------

def test_first_arrival_insert():
    supabase = FakeSupabase()

    persist_inventory_arrival(
        supabase=supabase,
        product_id=1,
        inventory=5,
        engine_version="test",
    )

    rows = supabase.store.get("preorder.inventory_arrival", [])
    assert len(rows) == 1
    assert rows[0]["product_id"] == 1
    assert rows[0]["engine_version"] == "test"
    assert "first_positive_inventory_at" in rows[0]


def test_no_insert_when_inventory_zero_or_negative():
    supabase = FakeSupabase()

    persist_inventory_arrival(
        supabase=supabase,
        product_id=1,
        inventory=0,
        engine_version="test",
    )

    rows = supabase.store.get("preorder.inventory_arrival", [])
    assert rows == []

    persist_inventory_arrival(
        supabase=supabase,
        product_id=1,
        inventory=-3,
        engine_version="test",
    )

    rows = supabase.store.get("preorder.inventory_arrival", [])
    assert rows == []


def test_no_duplicate_insert():
    supabase = FakeSupabase()

    persist_inventory_arrival(
        supabase=supabase,
        product_id=1,
        inventory=5,
        engine_version="test",
    )

    persist_inventory_arrival(
        supabase=supabase,
        product_id=1,
        inventory=10,
        engine_version="test",
    )

    rows = supabase.store.get("preorder.inventory_arrival", [])
    assert len(rows) == 1


def test_independent_of_classification_state():
    """
    Inventory arrival should be recorded regardless of classification outcome.
    This test simply ensures arrival logic is purely inventory-driven.
    """
    supabase = FakeSupabase()

    persist_inventory_arrival(
        supabase=supabase,
        product_id=99,
        inventory=2,
        engine_version="test",
    )

    rows = supabase.store.get("preorder.inventory_arrival", [])
    assert len(rows) == 1
    assert rows[0]["product_id"] == 99