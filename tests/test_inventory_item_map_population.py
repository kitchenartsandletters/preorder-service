from populate_inventory_item_map import _gid_to_int

def test_gid_to_int():
    assert _gid_to_int("gid://shopify/Product/123") == 123
    assert _gid_to_int("gid://shopify/ProductVariant/999") == 999
    assert _gid_to_int(None) is None
    assert _gid_to_int("not-a-gid") is None