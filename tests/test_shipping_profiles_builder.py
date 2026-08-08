"""
tests/test_shipping_profiles_builder.py — clone-from-reference builder.

Load-bearing guarantees:
  * _clone_participant refuses an unserviced participant — the empty carrier
    that returns no rate at checkout must never be cloned into a new profile.
  * _build_profile_template_input keeps the proven zone geography
    (US includeAllProvinces, World restOfWorld) and refuses a zone with no
    cloned carrier methods.

Plain asserts (no pytest dependency) so this runs under pytest or standalone.
"""

from services.shipping_profiles import (
    _zone_key,
    _clone_participant,
    _reference_zone_methods_from_payload,
    _build_profile_template_input,
    CARRIER_UPS,
    CARRIER_USPS,
    CARRIER_DHL_ECOMMERCE,
    CARRIER_DHL_EXPRESS,
)


def _part(carrier, services, active=True, fixed=None, adapt=False, pct=None):
    return {
        "__typename": "DeliveryParticipant",
        "carrierService": {"id": carrier},
        "participantServices": [{"name": n, "active": active} for n in services],
        "fixedFee": ({"amount": fixed, "currencyCode": "USD"} if fixed else None),
        "percentageOfRateFee": pct,
        "adaptToNewServicesFlag": adapt,
    }


def _method(name, rate_provider):
    return {"node": {"name": name, "active": True, "rateProvider": rate_provider}}


REFERENCE_PAYLOAD = {
    "id": "gid://shopify/DeliveryProfile/REF",
    "name": "General",
    "profileLocationGroups": [
        {
            "locationGroupZones": {
                "edges": [
                    {
                        "node": {
                            "zone": {
                                "name": "United States",
                                "countries": [{"code": {"countryCode": "US", "restOfWorld": False}}],
                            },
                            "methodDefinitions": {
                                "edges": [
                                    _method("UPS\u00ae", _part(
                                        CARRIER_UPS,
                                        ["UPS\u00ae Ground", "UPS\u00ae Standard",
                                         "UPS 2nd Day Air\u00ae", "UPS Next Day Air\u00ae"],
                                        fixed="1.00")),
                                    _method("USPS", _part(
                                        CARRIER_USPS,
                                        ["Ground Advantage", "Media Mail", "Priority Mail"],
                                        fixed="1.00")),
                                    # A flat-rate method must be ignored by the clone.
                                    {"node": {"name": "Flat", "active": True, "rateProvider": {
                                        "__typename": "DeliveryRateDefinition",
                                        "price": {"amount": "5.00", "currencyCode": "USD"}}}},
                                ]
                            },
                        }
                    },
                    {
                        "node": {
                            "zone": {
                                "name": "Rest of the World",
                                "countries": [{"code": {"countryCode": None, "restOfWorld": True}}],
                            },
                            "methodDefinitions": {
                                "edges": [
                                    _method("DHL eCommerce", _part(
                                        CARRIER_DHL_ECOMMERCE,
                                        ["DHL eCommerce Parcel Direct", "DHL eCommerce Parcel Standard"],
                                        adapt=True)),
                                    _method("DHL Express", _part(
                                        CARRIER_DHL_EXPRESS, ["DHL Express Worldwide"], adapt=True)),
                                    _method("UPS\u00ae", _part(
                                        CARRIER_UPS,
                                        ["UPS Worldwide Saver\u00ae", "UPS Worldwide Expedited\u00ae"],
                                        fixed="1.00")),
                                    _method("USPS", _part(
                                        CARRIER_USPS,
                                        ["Priority Mail Express International", "Priority Mail International"],
                                        fixed="1.00")),
                                ]
                            },
                        }
                    },
                ]
            }
        }
    ],
}


def test_zone_key_by_country_not_name():
    assert _zone_key({"countries": [{"code": {"countryCode": "US", "restOfWorld": False}}]}) == "US"
    assert _zone_key({"countries": [{"code": {"countryCode": None, "restOfWorld": True}}]}) == "World"
    # Any non-US international zone (flagged or enumerated) maps to World.
    assert _zone_key({"countries": [{"code": {"countryCode": "CA", "restOfWorld": False}}]}) == "World"
    # No countries at all -> unclassifiable.
    assert _zone_key({"countries": []}) is None


def test_transform_clones_services_and_fees():
    zm = _reference_zone_methods_from_payload(REFERENCE_PAYLOAD)
    assert set(zm) == {"US", "World"}
    assert len(zm["US"]) == 2  # flat-rate method skipped
    assert len(zm["World"]) == 4
    ups = next(m for m in zm["US"] if m["participant"]["carrierServiceId"] == CARRIER_UPS)
    services = ups["participant"]["participantServices"]
    assert len(services) == 4 and all(s["active"] for s in services)
    assert ups["participant"]["fixedFee"] == {"amount": "1.00", "currencyCode": "USD"}


def test_transform_preserves_adapt_flag():
    zm = _reference_zone_methods_from_payload(REFERENCE_PAYLOAD)
    dhl = next(m for m in zm["World"] if m["participant"]["carrierServiceId"] == CARRIER_DHL_ECOMMERCE)
    assert dhl["participant"].get("adaptToNewServicesFlag") is True


def test_clone_refuses_unserviced_participant():
    bad = _part(CARRIER_UPS, ["UPS\u00ae Ground"], active=False, adapt=False)
    raised = False
    try:
        _clone_participant(bad)
    except ValueError:
        raised = True
    assert raised, "expected _clone_participant to raise on an unserviced participant"


def test_clone_allows_adapt_only():
    ok = _part(CARRIER_DHL_EXPRESS, [], active=False, adapt=True)
    participant = _clone_participant(ok)
    assert participant["adaptToNewServicesFlag"] is True


def test_build_input_geography_and_methods():
    zm = _reference_zone_methods_from_payload(REFERENCE_PAYLOAD)
    inp = _build_profile_template_input("Week of Aug 2\u20138, 2026", "gid://shopify/ProductVariant/1", zm)
    zones = inp["locationGroupsToCreate"][0]["zonesToCreate"]
    us = next(z for z in zones if z["name"] == "US")
    world = next(z for z in zones if z["name"] == "World")
    assert us["countries"][0] == {"code": "US", "includeAllProvinces": True}
    assert world["countries"][0] == {"restOfWorld": True}
    assert len(us["methodDefinitionsToCreate"]) == 2
    assert len(world["methodDefinitionsToCreate"]) == 4
    assert inp["variantsToAssociate"] == ["gid://shopify/ProductVariant/1"]


def test_build_input_refuses_missing_world_zone():
    only_us = {"US": [{"name": "UPS", "active": True, "participant": {
        "carrierServiceId": CARRIER_UPS,
        "participantServices": [{"name": "UPS\u00ae Ground", "active": True}]}}]}
    raised = False
    try:
        _build_profile_template_input("x", "gid://shopify/ProductVariant/1", only_us)
    except ValueError as e:
        raised = "World" in str(e)
    assert raised, "expected build to refuse when World-zone methods are missing"


def test_zone_key_enumerated_world():
    # This store's "Rest of the World" zone lists explicit countries and does
    # NOT set the restOfWorld flag. It must still classify as World.
    enumerated = {"countries": [
        {"code": {"countryCode": "AL", "restOfWorld": False}},
        {"code": {"countryCode": "DZ", "restOfWorld": False}},
        {"code": {"countryCode": "AD", "restOfWorld": False}},
    ]}
    assert _zone_key(enumerated) == "World"


def test_transform_with_enumerated_world_zone():
    # Same as the reference payload but the World zone is enumerated, not flagged.
    payload = {
        "profileLocationGroups": [
            {"locationGroupZones": {"edges": [
                {"node": {
                    "zone": {"name": "United States",
                             "countries": [{"code": {"countryCode": "US", "restOfWorld": False}}]},
                    "methodDefinitions": {"edges": [
                        _method("USPS", _part(CARRIER_USPS, ["Ground Advantage", "Priority Mail"], fixed="1.00")),
                    ]},
                }},
                {"node": {
                    "zone": {"name": "Rest of the World",
                             "countries": [
                                 {"code": {"countryCode": "AL", "restOfWorld": False}},
                                 {"code": {"countryCode": "DZ", "restOfWorld": False}},
                             ]},
                    "methodDefinitions": {"edges": [
                        _method("DHL eCommerce", _part(CARRIER_DHL_ECOMMERCE,
                            ["DHL eCommerce Parcel Direct"], adapt=True)),
                        _method("USPS", _part(CARRIER_USPS, ["Priority Mail International"], fixed="1.00")),
                    ]},
                }},
            ]}}
        ]
    }
    zm = _reference_zone_methods_from_payload(payload)
    assert set(zm) == {"US", "World"}, set(zm)
    assert len(zm["World"]) == 2
    carriers = {m["participant"]["carrierServiceId"] for m in zm["World"]}
    assert carriers == {CARRIER_DHL_ECOMMERCE, CARRIER_USPS}


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
