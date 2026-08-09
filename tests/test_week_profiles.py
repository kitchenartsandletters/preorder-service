"""
tests/test_week_profiles.py — week resolver resolution order.

Uses fakes for the Shopify and Supabase layers (patched onto the week_profiles
module) to assert the four paths without any real I/O:
  1. mapping hit -> return mapped profile, no create
  2. adopt-by-name -> record mapping, return existing, no create
  3. create -> mint + record mapping
  4. stale mapping -> prune, fall through to create
"""

import asyncio
from datetime import date

import services.week_profiles as wp

PUB = date(2026, 8, 5)                 # Wednesday
WEEK_START = date(2026, 8, 2)          # Sunday
NAME = "Week of Aug 2\u20138, 2026"


class FakeSupabase:
    """Records mapping-table calls; the resolver only uses the three helpers,
    which we patch directly, so this is a placeholder handle."""
    pass


def _install(monkey):
    """Patch week_profiles dependencies. `monkey` is a dict of overrides."""
    saved = {}
    for name, fn in monkey.items():
        saved[name] = getattr(wp, name)
        setattr(wp, name, fn)
    return saved


def _restore(saved):
    for name, fn in saved.items():
        setattr(wp, name, fn)


def run_case(mapping_get, profiles, get_detail, variant_gid_ret, create_ret):
    calls = {"recorded": [], "deleted": [], "created": 0}

    def fake_get_profile_gid_for_week(sb, ws):
        assert ws == WEEK_START
        return mapping_get

    def fake_record(sb, gid, ws):
        calls["recorded"].append((gid, ws))

    def fake_delete(sb, gid):
        calls["deleted"].append(gid)

    async def fake_list(client):
        return profiles

    async def fake_get_detail(client, gid):
        return get_detail(gid)

    async def fake_variant(client, pid):
        return variant_gid_ret

    async def fake_create(client, name, variant):
        calls["created"] += 1
        assert name == NAME
        return dict(create_ret)

    saved = _install({
        "get_profile_gid_for_week": fake_get_profile_gid_for_week,
        "record_week_profile": fake_record,
        "delete_week_mapping": fake_delete,
        "list_shipping_profiles": fake_list,
        "get_profile_detail": fake_get_detail,
        "get_variant_gid_for_product": fake_variant,
        "create_profile_from_template": fake_create,
    })
    try:
        result = asyncio.run(
            wp.find_or_create_profile_for_week(
                shopify_client=object(), supabase=FakeSupabase(),
                pub_date=PUB, product_id=999, variant_gid=None,
            )
        )
    finally:
        _restore(saved)
    return result, calls


def test_mapping_hit_returns_without_create():
    def get_detail(gid):
        assert gid == "gid://shopify/DeliveryProfile/MAPPED"
        return {"profile_gid": gid, "name": NAME, "is_default": False}
    result, calls = run_case(
        mapping_get="gid://shopify/DeliveryProfile/MAPPED",
        profiles=[], get_detail=get_detail, variant_gid_ret=None, create_ret={},
    )
    assert result["profile_gid"] == "gid://shopify/DeliveryProfile/MAPPED"
    assert result["week_start"] == "2026-08-02" and result["week_end"] == "2026-08-08"
    assert calls["created"] == 0
    assert calls["recorded"] == []  # already mapped


def test_adopt_existing_named_profile():
    existing = {"profile_gid": "gid://shopify/DeliveryProfile/EXISTING", "name": NAME, "is_default": False}
    result, calls = run_case(
        mapping_get=None,
        profiles=[{"profile_gid": "x", "name": "October 21, 2026", "is_default": False}, existing],
        get_detail=lambda gid: None, variant_gid_ret=None, create_ret={},
    )
    assert result["profile_gid"] == "gid://shopify/DeliveryProfile/EXISTING"
    assert calls["created"] == 0
    assert calls["recorded"] == [("gid://shopify/DeliveryProfile/EXISTING", WEEK_START)]


def test_create_when_absent():
    result, calls = run_case(
        mapping_get=None,
        profiles=[{"profile_gid": "x", "name": "Some other name", "is_default": False}],
        get_detail=lambda gid: None,
        variant_gid_ret="gid://shopify/ProductVariant/555",
        create_ret={"profile_gid": "gid://shopify/DeliveryProfile/NEW", "name": NAME},
    )
    assert result["profile_gid"] == "gid://shopify/DeliveryProfile/NEW"
    assert result["week_start"] == "2026-08-02"
    assert calls["created"] == 1
    assert calls["recorded"] == [("gid://shopify/DeliveryProfile/NEW", WEEK_START)]


def test_stale_mapping_pruned_then_create():
    # mapping points at a profile that no longer exists -> get_detail returns None
    result, calls = run_case(
        mapping_get="gid://shopify/DeliveryProfile/STALE",
        profiles=[],
        get_detail=lambda gid: None,
        variant_gid_ret="gid://shopify/ProductVariant/555",
        create_ret={"profile_gid": "gid://shopify/DeliveryProfile/NEW", "name": NAME},
    )
    assert calls["deleted"] == ["gid://shopify/DeliveryProfile/STALE"]
    assert calls["created"] == 1
    assert result["profile_gid"] == "gid://shopify/DeliveryProfile/NEW"
    assert calls["recorded"] == [("gid://shopify/DeliveryProfile/NEW", WEEK_START)]


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
