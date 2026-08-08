"""
tests/test_release_week.py — lock the Sun–Sat week helpers to the NYT model.

The critical guarantee is test_matches_nyt_arithmetic: week_start_for must
equal the isoweekday() % 7 computation inlined in routes/admin_nyt.py for
every day across a full year, so shipping grouping and NYT reporting share one
definition of "week".
"""

from datetime import date, timedelta

from services.release_week import week_start_for, release_week, week_profile_name


def test_saturday_is_week_end():
    # Aug 8 2026 is a Saturday; its week is Aug 2 (Sun) – Aug 8 (Sat).
    assert release_week(date(2026, 8, 8)) == (date(2026, 8, 2), date(2026, 8, 8))


def test_sunday_is_week_start():
    assert release_week(date(2026, 8, 2)) == (date(2026, 8, 2), date(2026, 8, 8))


def test_midweek_wednesday():
    assert release_week(date(2026, 8, 5)) == (date(2026, 8, 2), date(2026, 8, 8))


def test_week_start_for_endpoints():
    assert week_start_for(date(2026, 8, 8)) == date(2026, 8, 2)  # Saturday
    assert week_start_for(date(2026, 8, 2)) == date(2026, 8, 2)  # Sunday


def test_name_same_month():
    assert week_profile_name(date(2026, 8, 2)) == "Week of Aug 2\u20138, 2026"


def test_name_cross_month():
    # Aug 30 2026 (Sun) – Sep 5 2026 (Sat)
    assert week_profile_name(date(2026, 8, 30)) == "Week of Aug 30 \u2013 Sep 5, 2026"


def test_name_cross_year():
    # Dec 28 2025 (Sun) – Jan 3 2026 (Sat)
    assert week_profile_name(date(2025, 12, 28)) == "Week of Dec 28, 2025 \u2013 Jan 3, 2026"


def test_matches_nyt_arithmetic():
    # Mirror routes/admin_nyt.py exactly across a full year of dates.
    for offset in range(0, 371):
        d = date(2025, 12, 1) + timedelta(days=offset)
        days_since_sunday = d.isoweekday() % 7
        expected_start = d - timedelta(days=days_since_sunday)
        assert week_start_for(d) == expected_start
        start, end = release_week(d)
        assert start == expected_start
        assert end == expected_start + timedelta(days=6)
        assert (end - start).days == 6
