from datetime import date, datetime, timezone

def derive_lifecycle_state(
    effective_pub_date: date | None,
    first_positive_inventory_at: datetime | None,
    presale_commitment_total: int | None,
    lifecycle_closed_at: datetime | None,
    presale_fulfilled_at: datetime | None,
    net_commitment_qty: int,
    today_et: date,
) -> str | None:
    if effective_pub_date is None:
        return None

    if lifecycle_closed_at is not None:
        return "closed_preorder"

    pct = presale_commitment_total or 0

    if effective_pub_date <= today_et and pct > 0 and first_positive_inventory_at is None:
        return "late_preorder"

    if effective_pub_date <= today_et and pct > 0 and first_positive_inventory_at is not None and presale_fulfilled_at is None:
        return "backfilled_preorder"

    if effective_pub_date > today_et and net_commitment_qty > 0:
        return "open_preorder"

    if effective_pub_date <= today_et and pct == 0:
        return "trivial_close"

    return None


def test_late_preorder():
    s = derive_lifecycle_state(
        effective_pub_date=date(2025, 10, 1),
        first_positive_inventory_at=None,
        presale_commitment_total=5,
        lifecycle_closed_at=None,
        presale_fulfilled_at=None,
        net_commitment_qty=5,
        today_et=date(2025, 10, 5),
    )
    assert s == "late_preorder"


def test_backfilled_preorder():
    s = derive_lifecycle_state(
        effective_pub_date=date(2025, 10, 1),
        first_positive_inventory_at=datetime(2025, 10, 3, tzinfo=timezone.utc),
        presale_commitment_total=5,
        lifecycle_closed_at=None,
        presale_fulfilled_at=None,
        net_commitment_qty=2,
        today_et=date(2025, 10, 5),
    )
    assert s == "backfilled_preorder"


def test_closed_preorder_terminal():
    s = derive_lifecycle_state(
        effective_pub_date=date(2025, 10, 1),
        first_positive_inventory_at=datetime(2025, 10, 3, tzinfo=timezone.utc),
        presale_commitment_total=5,
        lifecycle_closed_at=datetime(2025, 10, 6, tzinfo=timezone.utc),
        presale_fulfilled_at=datetime(2025, 10, 6, tzinfo=timezone.utc),
        net_commitment_qty=0,
        today_et=date(2025, 10, 7),
    )
    assert s == "closed_preorder"


def test_open_preorder():
    s = derive_lifecycle_state(
        effective_pub_date=date(2025, 10, 10),
        first_positive_inventory_at=None,
        presale_commitment_total=None,  # snapshot not yet created
        lifecycle_closed_at=None,
        presale_fulfilled_at=None,
        net_commitment_qty=3,
        today_et=date(2025, 10, 5),
    )
    assert s == "open_preorder"


def test_trivial_close():
    s = derive_lifecycle_state(
        effective_pub_date=date(2025, 10, 1),
        first_positive_inventory_at=None,
        presale_commitment_total=0,
        lifecycle_closed_at=None,
        presale_fulfilled_at=None,
        net_commitment_qty=0,
        today_et=date(2025, 10, 5),
    )
    assert s == "trivial_close"