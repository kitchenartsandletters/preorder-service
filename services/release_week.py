"""
services/release_week.py — canonical Sun–Sat release-week helpers.

Single source of truth for the NYT-style Sunday–Saturday week used by both
shipping-profile grouping and NYT reporting. The arithmetic mirrors the
inlined logic in routes/admin_nyt.py exactly (isoweekday() % 7), so the two
definitions can never diverge. The NYT code can be migrated to import these
helpers later with no behavior change.

The canonical key for a release week is its `week_start` (the Sunday). Profile
display names are derived from it via `week_profile_name`, but are never parsed
back into dates — the week↔profile mapping lives in
preorder.shipping_profile_week.
"""

from __future__ import annotations

from datetime import date, timedelta

__all__ = ["week_start_for", "release_week", "week_profile_name"]


def week_start_for(d: date) -> date:
    """Return the Sunday that begins the Sun–Sat week containing `d`.

    Mirrors routes/admin_nyt.py: days_since_sunday = isoweekday() % 7
    (Mon=1 .. Sun=7, so Sunday maps to 0 and is its own week start).
    """
    days_since_sunday = d.isoweekday() % 7
    return d - timedelta(days=days_since_sunday)


def release_week(d: date) -> tuple[date, date]:
    """Return (week_start=Sunday, week_end=Saturday) for the week containing `d`."""
    start = week_start_for(d)
    return start, start + timedelta(days=6)


def week_profile_name(week_start: date) -> str:
    """Human-legible shipping-profile name for a release week, anchored on its
    Sunday. Widens across month and year boundaries so it is always unambiguous:

        same month:   "Week of Aug 2–8, 2026"
        cross-month:  "Week of Aug 30 – Sep 5, 2026"
        cross-year:   "Week of Dec 28, 2025 – Jan 3, 2026"

    Display only. The canonical key is `week_start`; never parse this string
    back into a date.
    """
    start = week_start
    end = week_start + timedelta(days=6)
    mon_s, mon_e = start.strftime("%b"), end.strftime("%b")
    if start.year != end.year:
        return (
            f"Week of {mon_s} {start.day}, {start.year} "
            f"– {mon_e} {end.day}, {end.year}"
        )
    if start.month != end.month:
        return f"Week of {mon_s} {start.day} – {mon_e} {end.day}, {end.year}"
    return f"Week of {mon_s} {start.day}–{end.day}, {end.year}"
