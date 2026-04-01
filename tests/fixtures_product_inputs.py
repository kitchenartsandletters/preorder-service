"""
fixtures_product_inputs.py

Centralized fixture factory helpers for ClassificationInput objects.

NOTE:
These are scaffolding placeholders. The factory will be implemented
later once test case data is written.
"""

from datetime import date
from classification.types import ClassificationInput


def make_input(
    *,
    product_id=123,
    tags=None,
    in_preorder_collection=False,
    date_tags=None,
    pub_date=None,
    override_date=None,
    inventory=0,
    has_inventory_arrival=False,   # ← add
):
    return ClassificationInput(
        product_id=product_id,
        tags=tags or [],
        in_preorder_collection=in_preorder_collection,
        date_tags=date_tags or [],
        pub_date=pub_date,
        override_date=override_date,
        inventory=inventory,
        has_inventory_arrival=has_inventory_arrival,  # ← add
    )