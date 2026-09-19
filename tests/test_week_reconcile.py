"""
tests/test_week_reconcile.py — week-aware reconcile bucketing.

Key semantics under the week model:
  * on the MAPPED week profile        -> correctly_assigned
  * on an old per-date profile        -> wrong_profile (the migration to-do)
  * on General                        -> missing_from_profile
  * arrived + on a profile (any status) -> arrived_should_detach (detach -> General)
  * arrived + on General (any status)   -> exempt (correctly placed; no action)
  * past pub date, on any profile     -> should_be_removed
  * early stock w/ inventory, no live arrival record -> exempt (fallback)
  * no pub date                       -> no_pub_date
  * empty non-default profiles        -> migration.repurpose_ready_profiles
"""

from datetime import date

from services.week_reconcile import compute_week_reconcile

TODAY = date(2026, 8, 9)

DATE_OCT6 = "gid://shopify/DeliveryProfile/OCT6"
WEEK_OCT4 = "gid://shopify/DeliveryProfile/WEEKOCT4"
WEEK_NOV1 = "gid://shopify/DeliveryProfile/WEEKNOV1"
WEEK_SEP13 = "gid://shopify/DeliveryProfile/WEEKSEP13"
DATE_JUL1 = "gid://shopify/DeliveryProfile/JUL1"
EMPTY_OLD = "gid://shopify/DeliveryProfile/EMPTYOLD"

NAME_WEEK_OCT4 = "Week of Oct 4–10, 2026"
NAME_WEEK_NOV1 = "Week of Nov 1–7, 2026"
NAME_WEEK_SEP13 = "Week of Sep 13–19, 2026"


def _reconcile():
    preorders = [
        {"product_id": 1, "status": "active_preorder", "pub_date": date(2026, 11, 3), "title": "OnWeek", "inventory": 0},
        {"product_id": 2, "status": "active_preorder", "pub_date": date(2026, 10, 6), "title": "OnDate", "inventory": 0},
        {"product_id": 3, "status": "active_preorder", "pub_date": date(2026, 10, 7), "title": "OnGeneral", "inventory": 0},
        {"product_id": 4, "status": "active_preorder", "pub_date": date(2026, 7, 1), "title": "Past", "inventory": 0},
        {"product_id": 5, "status": "early_stock_arrival", "pub_date": date(2026, 9, 1), "title": "Early", "inventory": 3},
        {"product_id": 6, "status": "active_preorder", "pub_date": None, "title": "NoPub", "inventory": 0},
    ]
    product_profile_map = {
        1: {"profile_name": NAME_WEEK_NOV1, "profile_gid": WEEK_NOV1},
        2: {"profile_name": "October 6, 2026", "profile_gid": DATE_OCT6},
        4: {"profile_name": "July 1, 2026", "profile_gid": DATE_JUL1},
    }
    non_default_profiles = [
        {"profile_gid": WEEK_NOV1, "name": NAME_WEEK_NOV1, "products": [{"product_id": 1}]},
        {"profile_gid": DATE_OCT6, "name": "October 6, 2026", "products": [{"product_id": 2}]},
        {"profile_gid": DATE_JUL1, "name": "July 1, 2026", "products": [{"product_id": 4}]},
        {"profile_gid": EMPTY_OLD, "name": "September 1, 2026", "products": []},
    ]
    week_mapping = {date(2026, 11, 1): WEEK_NOV1}
    profiles_by_name = {p["name"]: p for p in non_default_profiles}
    return compute_week_reconcile(
        preorders, product_profile_map, non_default_profiles,
        week_mapping, profiles_by_name, TODAY,
    )


def test_on_mapped_week_profile_is_correct():
    r = _reconcile()["report"]
    ids = [e["product_id"] for e in r["correctly_assigned"]]
    assert ids == [1]
    assert r["correctly_assigned"][0]["profile"] == NAME_WEEK_NOV1


def test_on_date_profile_is_wrong_profile():
    r = _reconcile()["report"]
    wp = {e["product_id"]: e for e in r["wrong_profile"]}
    assert set(wp) == {2}
    assert wp[2]["expected_profile"] == NAME_WEEK_OCT4
    assert wp[2]["current_profile"] == "October 6, 2026"


def test_on_general_is_missing():
    r = _reconcile()["report"]
    ids = [e["product_id"] for e in r["missing_from_profile"]]
    assert ids == [3]
    assert r["missing_from_profile"][0]["expected_profile"] == NAME_WEEK_OCT4


def test_past_exempt_nopub_buckets():
    r = _reconcile()["report"]
    assert [e["product_id"] for e in r["should_be_removed"]] == [4]
    assert [e["product_id"] for e in r["exempt"]] == [5]
    assert [e["product_id"] for e in r["no_pub_date"]] == [6]


def test_summary_and_model():
    out = _reconcile()
    assert out["model"] == "week"
    s = out["summary"]
    assert s == {
        "correctly_assigned": 1,
        "wrong_profile": 1,
        "missing_from_profile": 1,
        "arrived_should_detach": 0,
        "should_be_removed": 1,
        "exempt": 1,
        "no_pub_date": 1,
    }


def test_migration_progress_and_repurpose_ready():
    m = _reconcile()["migration"]
    assert m["titles_on_week_profile"] == 1
    assert m["titles_needing_migration"] == 2
    assert m["arrived_to_detach"] == 0
    ready = {p["profile_gid"] for p in m["repurpose_ready_profiles"]}
    assert ready == {EMPTY_OLD}


# ── arrival signal governs (detach on profile, exempt on General) ────────

def _arrived_case(on_profile, arrived=True, status="active_preorder", inv=0, pub=date(2026, 9, 15)):
    """One product; on WEEK_SEP13 if on_profile else General. Sep 15 -> week Sep 13."""
    ppm = {}
    non_default = []
    if on_profile:
        ppm[1] = {"profile_name": NAME_WEEK_SEP13, "profile_gid": WEEK_SEP13}
        non_default = [{"profile_gid": WEEK_SEP13, "name": NAME_WEEK_SEP13, "products": [{"product_id": 1}]}]
    preorders = [{
        "product_id": 1, "status": status, "pub_date": pub, "title": "Arrived",
        "inventory": inv, "arrival_record_is_live": arrived,
        "first_positive_inventory_at": "2026-08-24T00:00:00+00:00",
    }]
    profiles_by_name = {p["name"]: p for p in non_default}
    return compute_week_reconcile(preorders, ppm, non_default, {}, profiles_by_name, TODAY)


def test_arrived_active_on_profile_flags_detach():
    out = _arrived_case(on_profile=True)
    r = out["report"]
    assert [e["product_id"] for e in r["arrived_should_detach"]] == [1]
    e = r["arrived_should_detach"][0]
    assert e["current_profile"] == NAME_WEEK_SEP13
    assert e["arrived_at"].startswith("2026-08-24")
    assert r["correctly_assigned"] == [] and r["wrong_profile"] == [] and r["should_be_removed"] == []
    assert out["migration"]["arrived_to_detach"] == 1


def test_arrived_active_on_general_is_exempt():
    r = _arrived_case(on_profile=False)["report"]
    assert [e["product_id"] for e in r["exempt"]] == [1]
    assert r["exempt"][0]["arrived_at"].startswith("2026-08-24")
    assert r["arrived_should_detach"] == []
    assert r["missing_from_profile"] == []


def test_arrived_independent_of_inventory_sign():
    r = _arrived_case(on_profile=True, inv=-14)["report"]
    assert [e["product_id"] for e in r["arrived_should_detach"]] == [1]


def test_arrived_precedes_should_be_removed_for_past_pub():
    r = _arrived_case(on_profile=True, pub=date(2026, 7, 1))["report"]
    assert [e["product_id"] for e in r["arrived_should_detach"]] == [1]
    assert r["should_be_removed"] == []


def test_not_arrived_active_unaffected():
    r = _arrived_case(on_profile=True, arrived=False)["report"]
    assert r["arrived_should_detach"] == []
    assert [e["product_id"] for e in r["correctly_assigned"]] == [1]


def test_early_stock_arrived_on_profile_detaches():
    # generalized: an early_stock_arrival that has arrived and sits on a profile
    # detaches now (previously it was left exempt).
    r = _arrived_case(on_profile=True, status="early_stock_arrival", inv=5)["report"]
    assert [e["product_id"] for e in r["arrived_should_detach"]] == [1]
    assert r["exempt"] == []


def test_early_stock_arrived_on_general_is_exempt():
    # the Spinasse shape: early_stock_arrival, arrived (manual-correction), 0 on
    # hand, on General -> exempt (out of Missing), not detach.
    r = _arrived_case(on_profile=False, status="early_stock_arrival", inv=0)["report"]
    assert [e["product_id"] for e in r["exempt"]] == [1]
    assert r["missing_from_profile"] == []
    assert r["arrived_should_detach"] == []


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"PASS {fn.__name__}")
        except Exception as e:
            print(f"FAIL {fn.__name__}: {e!r}"); traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
