from datetime import date

from classification.utils import resolve_effective_pub_date


def test_override_date_takes_precedence_over_all():
    """override_date must override pub_date and date_tags."""
    result = resolve_effective_pub_date(
        date_tags=[date(2025, 11, 1), date(2025, 12, 1)],
        pub_date=date(2025, 11, 1),
        override_date=date(2025, 12, 15),
    )

    assert result == date(2025, 12, 15)


def test_pub_date_used_when_no_override():
    """pub_date must be used when override_date is absent."""
    result = resolve_effective_pub_date(
        date_tags=[date(2025, 11, 1), date(2025, 12, 1)],
        pub_date=date(2025, 11, 20),
        override_date=None,
    )

    assert result == date(2025, 11, 20)


def test_latest_date_tag_used_when_no_metafields():
    """When only date_tags exist, the LATEST tag is the effective pub date."""
    result = resolve_effective_pub_date(
        date_tags=[date(2025, 11, 1), date(2025, 12, 1)],
        pub_date=None,
        override_date=None,
    )

    assert result == date(2025, 12, 1)


def test_date_tag_order_does_not_matter():
    """date_tags may be unsorted; latest date must still be selected."""
    result = resolve_effective_pub_date(
        date_tags=[date(2025, 12, 1), date(2025, 11, 1)],
        pub_date=None,
        override_date=None,
    )

    assert result == date(2025, 12, 1)


def test_override_overrides_latest_date_tag():
    """override_date must override even a later date_tag."""
    result = resolve_effective_pub_date(
        date_tags=[date(2025, 12, 1)],
        pub_date=None,
        override_date=date(2026, 1, 15),
    )

    assert result == date(2026, 1, 15)


def test_no_dates_returns_none():
    """If no date signals exist, effective_pub_date must be None."""
    result = resolve_effective_pub_date(
        date_tags=[],
        pub_date=None,
        override_date=None,
    )

    assert result is None