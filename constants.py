# preorder/constants.py
"""
Authoritative constants for the preorder system.
See docs/Trust_Tier_Labeling.md for the reasoning behind these values.
"""
from datetime import date

# The date on which reliable webhook capture began.
# All commitment_ledger rows with occurred_at < CUTOVER_DATE
# are either backfill (orders/create_backfill) or pre-capture events
# and must be treated as Tier 3 estimated data.
# Do not change this value without running a new Phase 1 audit.
LEDGER_CUTOVER_DATE: date = date(2026, 2, 11)

# Topics that represent verified live event evidence (Tier 1).
# orders/create_backfill and reconciliation.adjustment are explicitly excluded.
LIVE_COMMITMENT_TOPICS: frozenset[str] = frozenset({
    "orders/create",
    "orders/paid",
    "orders/fulfilled",
    "orders/cancelled",
    "refunds/create",
})

# Topics that contribute to presale_sales_total (positive presale evidence only).
# Fulfillments are intentionally excluded — they do not reduce the presale count.
PRESALE_POSITIVE_TOPICS: frozenset[str] = frozenset({
    "orders/create",
    "orders/paid",
})

# Topics that reduce presale_sales_total (pre-pub cancellations and refunds).
PRESALE_NEGATIVE_TOPICS: frozenset[str] = frozenset({
    "orders/cancelled",
    "refunds/create",
})