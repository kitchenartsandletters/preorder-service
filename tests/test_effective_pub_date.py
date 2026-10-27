from datetime import date

from classification.utils import resolve_effective_pub_date



def test_pub_date_used_when_no_override():
    """pub_date is the primary signal (override retired in Move 1e)."""
    result = resolve_effective_pub_date(
        date_tags=[date(2025, 11, 1), date(2025, 12, 1)],
        pub_date=date(2025, 11, 20),
    )

    assert result == date(2025, 11, 20)


def test_latest_date_tag_used_when_no_metafields():
    """When only date_tags exist, the LATEST tag is the effective pub date."""
    result = resolve_effective_pub_date(
        date_tags=[date(2025, 11, 1), date(2025, 12, 1)],
        pub_date=None,
    )

    assert result == date(2025, 12, 1)


def test_date_tag_order_does_not_matter():
    """date_tags may be unsorted; latest date must still be selected."""
    result = resolve_effective_pub_date(
        date_tags=[date(2025, 12, 1), date(2025, 11, 1)],
        pub_date=None,
    )

    assert result == date(2025, 12, 1)



def test_no_dates_returns_none():
    """If no date signals exist, effective_pub_date must be None."""
    result = resolve_effective_pub_date(
        date_tags=[],
        pub_date=None,
    )

    assert result is None