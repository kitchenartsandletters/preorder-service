"""
classification/utils.py

Pure helper utilities for preorder-service classification.

PHASE 1 SCOPE:
- Effective publication date resolution ONLY.
- No anomaly logic.
- No inventory logic.
- No classification logic.

All functions in this module MUST be:
- Pure
- Deterministic
- Side-effect free
"""

from __future__ import annotations

from datetime import date
from typing import Optional, List


def resolve_effective_pub_date(
    *,
    date_tags: List[date],
    pub_date: Optional[date],
    override_date: Optional[date],
) -> Optional[date]:
    """
    Resolve the single authoritative publication date for a product.

    Priority order (Rev 3 — FINAL):
        1. override_date
        2. pub_date
        3. latest date_tag (chronologically max)
        4. None

    IMPORTANT NOTES:
    - date_tags may be unsorted; ordering MUST NOT be trusted.
    - date_tags represent a revision history of publication dates.
    - The latest date_tag represents the most recent planned pub date.
    - This function performs NO validation beyond selection.
      Conflict detection is handled in anomaly logic (Phase 2).

    Parameters
    ----------
    date_tags : list[date]
        Parsed publication date tags (MM-DD-YYYY → date)
    pub_date : Optional[date]
        Primary publication date metafield (YYYY-MM-DD)
    override_date : Optional[date]
        Override publication date metafield (YYYY-MM-DD)

    Returns
    -------
    Optional[date]
        The effective publication date, or None if no date signals exist.
    """

    # PHASE 1 IMPLEMENTATION (minimal, deterministic)

    if override_date is not None:
        return override_date

    if pub_date is not None:
        return pub_date

    if date_tags:
        # Tags may be unsorted; choose the latest date
        return max(date_tags)

    return None
