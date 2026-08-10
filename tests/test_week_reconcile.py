"""
tests/test_week_reconcile.py — week-aware reconcile bucketing.

Key semantics under the week model:
  * on the MAPPED week profile        -> correctly_assigned
  * on an old per-date profile        -> wrong_profile (the migration to-do)
  * on General                        -> missing_from_profile
  * past pub date, on any profile     -> should_be_removed
  * early stock w/ inventory          -> exempt
  * no pub date                       -> no_pub_date
  * empty non-default profiles        -> migration.repurpose_ready_profiles
"""

from datetime import date

from services.week_reconcile import compute_week_reconcile

TODAY = date(2026, 8, 9)

DATE_OCT6 = "gid://shopify/DeliveryProfile/OCT6"
WEEK_OCT4 = "gid://shopify/DeliveryProfile/WEEKOCT4"
WEEK_NOV1 = "gid://shopify/DeliveryProfile/WEEKNOV1"
DATE_JUL1 = "gid://shopify/DeliveryProfile/JUL1"
EMPTY_OLD = "gid://shopify/DeliveryProfile/EMPTYOLD"

NAME_WEEK_OCT4 = "Week of Oct 4\u201310, 2026"
NAME_WEEK_NOV1 = "Week of Nov 1\u20137, 2026"


def _reconcile():
    preorders = [
        # on its MAPPED week profile -> correctly_assigned
        {"product_id": 1, "status": "active_preorder", "pub_date": date(2026, 11, 3), "title": "OnWeek", "inventory": 0},
        # still on the old per-date profile -> wrong_profile (needs migration)
        {"product_id": 2, "status": "active_preorder", "pub_date": date(2026, 10, 6), "title": "OnDate", "inventory": 0},
        # on General -> missing_from_profile
        {"product_id": 3, "status": "active_preorder", "pub_date": date(2026, 10, 7), "title": "OnGeneral", "inventory": 0},
        # past pub date, on a profile -> should_be_removed
        {"product_id": 4, "status": "active_preorder", "pub_date": date(2026, 7, 1), "title": "Past", "inventory": 0},
        # exempt
        {"product_id": 5, "status": "early_stock_arrival", "pub_date": date(2026, 9, 1), "title": "Early", "inventory": 3},
        # no pub date
        {"product_id": 6, "status": "active_preorder", "pub_date": None, "title": "NoPub", "inventory": 0},
    ]
    product_profile_map = {
        1: {"profile_name": NAME_WEEK_NOV1, "profile_gid": WEEK_NOV1},
        2: {"profile_name": "October 6, 2026", "profile_gid": DATE_OCT6},
        4: {"profile_name": "July 1, 2026", "profile_gid": DATE_JUL1},
        # 3,5,6 not on a date/week profile (General)
    }
    non_default_profiles = [
        {"profile_gid": WEEK_NOV1, "name": NAME_WEEK_NOV1, "products": [{"product_id": 1}]},
        {"profile_gid": DATE_OCT6, "name": "October 6, 2026", "products": [{"product_id": 2}]},
        {"profile_gid": DATE_JUL1, "name": "July 1, 2026", "products": [{"product_id": 4}]},
        {"profile_gid": EMPTY_OLD, "name": "September 1, 2026", "products": []},  # emptied earlier
    ]
    week_mapping = {date(2026, 11, 1): WEEK_NOV1}  # Oct 4 week NOT yet mapped
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
    # expected is the WEEK profile; current is the old date profile
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
        "should_be_removed": 1,
        "exempt": 1,
        "no_pub_date": 1,
    }


def test_migration_progress_and_repurpose_ready():
    m = _reconcile()["migration"]
    assert m["titles_on_week_profile"] == 1
    assert m["titles_needing_migration"] == 2  # wrong_profile + missing
    ready = {p["profile_gid"] for p in m["repurpose_ready_profiles"]}
    assert ready == {EMPTY_OLD}  # only the currently-empty non-default profile


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
