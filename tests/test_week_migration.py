"""
tests/test_week_migration.py — Phase 2 planner + apply orchestration.

The planner (_compute_week_plan) is pure and gets the bulk of coverage via a
rich scenario. apply_week_plan is orchestration, faked at its seams.
"""

import asyncio
from datetime import date

import services.week_migration as wm

TODAY = date(2026, 8, 9)

DATE_OCT6 = "gid://shopify/DeliveryProfile/OCT6"
DATE_OCT7 = "gid://shopify/DeliveryProfile/OCT7"
DATE_JUL1 = "gid://shopify/DeliveryProfile/JUL1"
WEEK_NOV1 = "gid://shopify/DeliveryProfile/WEEKNOV1"

NAME_WEEK_NOV1 = "Week of Nov 1–7, 2026"


def _scenario_plan():
    preorders = [
        {"product_id": 1, "status": "active_preorder", "pub_date": date(2026, 10, 6), "title": "Book1", "inventory": 0},
        {"product_id": 2, "status": "active_preorder", "pub_date": date(2026, 10, 7), "title": "Book2", "inventory": 0},
        {"product_id": 3, "status": "active_preorder", "pub_date": date(2026, 10, 21), "title": "Book3", "inventory": 0},
        {"product_id": 4, "status": "active_preorder", "pub_date": date(2026, 11, 3), "title": "Book4", "inventory": 0},
        {"product_id": 5, "status": "active_preorder", "pub_date": date(2026, 7, 1), "title": "Book5", "inventory": 0},
        {"product_id": 6, "status": "early_stock_arrival", "pub_date": date(2026, 9, 1), "title": "Book6", "inventory": 5},
        {"product_id": 7, "status": "active_preorder", "pub_date": None, "title": "Book7", "inventory": 0},
    ]
    product_profile_map = {
        1: {"profile_name": "October 6, 2026", "profile_gid": DATE_OCT6},
        2: {"profile_name": "October 7, 2026", "profile_gid": DATE_OCT7},
        4: {"profile_name": NAME_WEEK_NOV1, "profile_gid": WEEK_NOV1},
        5: {"profile_name": "July 1, 2026", "profile_gid": DATE_JUL1},
    }
    non_default_profiles = [
        {"profile_gid": DATE_OCT6, "name": "October 6, 2026", "products": [{"product_id": 1}]},
        {"profile_gid": DATE_OCT7, "name": "October 7, 2026", "products": [{"product_id": 2}]},
        {"profile_gid": DATE_JUL1, "name": "July 1, 2026", "products": [{"product_id": 5}]},
        {"profile_gid": WEEK_NOV1, "name": NAME_WEEK_NOV1, "products": [{"product_id": 4}]},
    ]
    week_mapping = {date(2026, 11, 1): WEEK_NOV1}
    profiles_by_name = {p["name"]: p for p in non_default_profiles}
    return wm._compute_week_plan(
        preorders, product_profile_map, non_default_profiles,
        week_mapping, profiles_by_name, TODAY,
    )


def test_plan_groups_by_week():
    plan = _compute = _scenario_plan()
    weeks = {w["week_start"]: w for w in plan["weeks"]}
    assert set(weeks) == {"2026-10-04", "2026-10-18", "2026-11-01"}
    oct4 = weeks["2026-10-04"]
    assert oct4["profile_name"] == "Week of Oct 4–10, 2026"
    assert oct4["profile_status"] == "create" and oct4["profile_gid"] is None
    acts = {t["product_id"]: t["action"] for t in oct4["titles"]}
    assert acts == {1: "move", 2: "move"}


def test_plan_add_from_general_and_existing_week():
    plan = _scenario_plan()
    weeks = {w["week_start"]: w for w in plan["weeks"]}
    oct18 = weeks["2026-10-18"]
    assert oct18["profile_status"] == "create"
    assert [t["action"] for t in oct18["titles"]] == ["add"]
    assert oct18["titles"][0]["current_profile"] == "General"
    nov1 = weeks["2026-11-01"]
    assert nov1["profile_status"] == "exists" and nov1["profile_gid"] == WEEK_NOV1
    assert [t["action"] for t in nov1["titles"]] == ["already"]


def test_plan_buckets():
    plan = _scenario_plan()
    assert [r["product_id"] for r in plan["should_be_removed"]] == [5]
    assert [r["product_id"] for r in plan["exempt"]] == [6]
    assert [r["product_id"] for r in plan["no_pub_date"]] == [7]


def test_plan_emptied_profiles():
    plan = _scenario_plan()
    emptied = {e["profile_gid"] for e in plan["emptied_profiles"]}
    assert DATE_OCT6 in emptied and DATE_OCT7 in emptied
    assert DATE_JUL1 not in emptied
    assert WEEK_NOV1 not in emptied


def test_plan_summary():
    s = _scenario_plan()["summary"]
    assert s["weeks_total"] == 3
    assert s["weeks_to_create"] == 2
    assert s["titles_total"] == 4
    assert s["titles_moving"] == 3
    assert s["titles_already"] == 1
    assert s["profiles_emptied"] == 2
    assert s["should_be_removed"] == 1
    assert s["exempt"] == 1
    assert s["no_pub_date"] == 1


def test_plan_adopt_by_name_when_unmapped():
    preorders = [{"product_id": 10, "status": "active_preorder", "pub_date": date(2026, 10, 21), "title": "B", "inventory": 0}]
    name = "Week of Oct 18–24, 2026"
    prof = {"profile_gid": "gid://shopify/DeliveryProfile/WEEKOCT18", "name": name, "products": []}
    plan = wm._compute_week_plan(
        preorders, {}, [prof], {}, {name: prof}, TODAY,
    )
    wk = plan["weeks"][0]
    assert wk["profile_status"] == "exists"
    assert wk["profile_gid"] == "gid://shopify/DeliveryProfile/WEEKOCT18"
    assert wk["titles"][0]["action"] == "add"


def test_plan_excludes_arrived_active():
    # An active preorder that has physically arrived is excluded from week
    # grouping (slated to detach, not group), even with a future pub date.
    preorders = [
        {"product_id": 1, "status": "active_preorder", "pub_date": date(2026, 9, 15),
         "title": "Arrived", "inventory": 0, "arrival_record_is_live": True,
         "first_positive_inventory_at": "2026-08-24T00:00:00+00:00"},
        {"product_id": 2, "status": "active_preorder", "pub_date": date(2026, 9, 15),
         "title": "NotArrived", "inventory": 0},
    ]
    ppm = {1: {"profile_name": "October 6, 2026", "profile_gid": DATE_OCT6}}
    non_default = [{"profile_gid": DATE_OCT6, "name": "October 6, 2026", "products": [{"product_id": 1}]}]
    plan = wm._compute_week_plan(preorders, ppm, non_default, {}, {p["name"]: p for p in non_default}, TODAY)
    title_ids = [t["product_id"] for w in plan["weeks"] for t in w["titles"]]
    assert 1 not in title_ids   # arrived-active excluded
    assert 2 in title_ids       # non-arrived grouped normally


def test_plan_excludes_arrived_early_stock():
    # generalized: an early_stock_arrival that has arrived is also excluded from
    # week grouping (the Spinasse shape), not just active preorders — and it is
    # not dumped into the plan's exempt list either.
    preorders = [
        {"product_id": 1, "status": "early_stock_arrival", "pub_date": date(2026, 9, 29),
         "title": "Spinasse", "inventory": 0, "arrival_record_is_live": True,
         "first_positive_inventory_at": "2026-08-28T20:54:00+00:00"},
        {"product_id": 2, "status": "active_preorder", "pub_date": date(2026, 9, 29),
         "title": "NotArrived", "inventory": 0},
    ]
    plan = wm._compute_week_plan(preorders, {}, [], {}, {}, TODAY)
    title_ids = [t["product_id"] for w in plan["weeks"] for t in w["titles"]]
    assert 1 not in title_ids   # arrived early-stock excluded
    assert 2 in title_ids       # non-arrived active grouped normally
    assert [r["product_id"] for r in plan["exempt"]] == []


def test_apply_single_week_creates_and_assigns():
    calls = {"preview": 0, "resolved": None, "assigned": []}

    plan = {"weeks": [{
        "week_start": "2026-10-04", "week_end": "2026-10-10",
        "profile_name": "Week of Oct 4–10, 2026",
        "profile_status": "create", "profile_gid": None,
        "titles": [
            {"product_id": 1, "title": "Book1", "pub_date": "2026-10-06", "current_profile": "October 6, 2026", "action": "move"},
            {"product_id": 2, "title": "Book2", "pub_date": "2026-10-07", "current_profile": "October 7, 2026", "action": "move"},
        ],
    }]}

    async def fake_build(client, sb):
        return plan

    async def fake_preview(client, reference_gid=None):
        calls["preview"] += 1
        return {"zones": {"US": [1], "World": [1]}}

    async def fake_resolve(client, sb, pub_date, product_id, variant_gid=None):
        calls["resolved"] = (pub_date.isoformat(), product_id)
        return {"profile_gid": "gid://shopify/DeliveryProfile/NEW", "name": "Week of Oct 4–10, 2026"}

    async def fake_assign(client, profile_gid, product_id, variant_gid=None):
        calls["assigned"].append((profile_gid, product_id))
        return []

    saved = {}
    for n, f in {
        "build_week_plan": fake_build,
        "preview_reference_clone": fake_preview,
        "find_or_create_profile_for_week": fake_resolve,
        "assign_product_to_profile": fake_assign,
    }.items():
        saved[n] = getattr(wm, n)
        setattr(wm, n, f)
    try:
        result = asyncio.run(wm.apply_week_plan(object(), object(), date(2026, 10, 4)))
    finally:
        for n, f in saved.items():
            setattr(wm, n, f)

    assert calls["preview"] == 1
    assert calls["resolved"] == ("2026-10-06", 1)
    assert calls["assigned"] == [
        ("gid://shopify/DeliveryProfile/NEW", 1),
        ("gid://shopify/DeliveryProfile/NEW", 2),
    ]
    assert result["status"] == "applied" and result["created"] is True
    assert result["assigned"] == [1, 2] and result["errors"] == []


def test_apply_noop_when_week_absent():
    async def fake_build(client, sb):
        return {"weeks": []}
    saved = wm.build_week_plan
    wm.build_week_plan = fake_build
    try:
        result = asyncio.run(wm.apply_week_plan(object(), object(), date(2026, 10, 4)))
    finally:
        wm.build_week_plan = saved
    assert result["status"] == "noop"


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
