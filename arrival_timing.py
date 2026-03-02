from datetime import date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def derive_arrival_timing(effective_pub_date, first_positive_inventory_at):
    """
    Derive arrival timing according to Phase 12 rules.

    Returns:
        None
        'no_arrival'
        'early_arrival'
        'on_time_arrival'
        'late_arrival'
    """

    if effective_pub_date is None:
        return None

    if first_positive_inventory_at is None:
        return "no_arrival"

    arrival_date = (
        first_positive_inventory_at
        .astimezone(ET)
        .date()
    )

    if arrival_date > effective_pub_date:
        return "late_arrival"

    days_diff = (effective_pub_date - arrival_date).days

    if days_diff <= 7:
        return "on_time_arrival"

    return "early_arrival"