"""
DEPRECATED TEST FILE — anomaly_inventory_contradiction

As of Preorder Classification Spec Rev 3:

There is NO LONGER an anomaly called `anomaly_inventory_contradiction`.

Correct behavior:
    - Future effective_pub_date + inventory > 0 → early_stock_arrival
    - NOT an anomaly_*
    - Should be tested in test_early_stock_arrival.py

This file is retained temporarily so pytest does not break,
but must contain no active test logic.

TODO:
- Remove this file entirely once the early_stock_arrival tests are implemented.
- Do NOT write tests here. All inventory-related preorder logic
  belongs in test_early_stock_arrival.py.
"""

# Intentionally no imports, no tests.