import pytest
from datetime import datetime, date
from zoneinfo import ZoneInfo

from arrival_timing import derive_arrival_timing


ET = ZoneInfo("America/New_York")


def dt(year, month, day):
    """Helper to create ET-aware datetime."""
    return datetime(year, month, day, 12, 0, 0, tzinfo=ET)


# ---------------------------
# 1️⃣ No Arrival
# ---------------------------

def test_no_arrival_with_pub_date():
    pub_date = date(2025, 10, 7)
    arrival_ts = None

    result = derive_arrival_timing(pub_date, arrival_ts)

    assert result == "no_arrival"


def test_no_arrival_no_pub_date():
    pub_date = None
    arrival_ts = None

    result = derive_arrival_timing(pub_date, arrival_ts)

    assert result is None


# ---------------------------
# 2️⃣ On-Time Arrival
# ---------------------------

def test_arrival_on_pub_date():
    pub_date = date(2025, 10, 7)
    arrival_ts = dt(2025, 10, 7)

    result = derive_arrival_timing(pub_date, arrival_ts)

    assert result == "on_time_arrival"


def test_arrival_within_7_days_before():
    pub_date = date(2025, 10, 7)
    arrival_ts = dt(2025, 10, 3)  # 4 days before

    result = derive_arrival_timing(pub_date, arrival_ts)

    assert result == "on_time_arrival"


def test_arrival_exactly_7_days_before():
    pub_date = date(2025, 10, 7)
    arrival_ts = dt(2025, 9, 30)  # 7 days before

    result = derive_arrival_timing(pub_date, arrival_ts)

    assert result == "on_time_arrival"


# ---------------------------
# 3️⃣ Early Arrival
# ---------------------------

def test_arrival_more_than_7_days_before():
    pub_date = date(2025, 10, 7)
    arrival_ts = dt(2025, 9, 29)  # 8 days before

    result = derive_arrival_timing(pub_date, arrival_ts)

    assert result == "early_arrival"


# ---------------------------
# 4️⃣ Late Arrival
# ---------------------------

def test_arrival_after_pub_date():
    pub_date = date(2025, 10, 7)
    arrival_ts = dt(2025, 10, 8)

    result = derive_arrival_timing(pub_date, arrival_ts)

    assert result == "late_arrival"


# ---------------------------
# 5️⃣ Pub Date Required
# ---------------------------

def test_arrival_with_no_pub_date():
    pub_date = None
    arrival_ts = dt(2025, 10, 7)

    result = derive_arrival_timing(pub_date, arrival_ts)

    assert result is None