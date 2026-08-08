"""
Shopify Shipping Profile Service
=================================
Manages delivery profile operations for preorder products.

Profile naming convention: "Month Day, Year" (e.g., "May 5, 2026")
Products are assigned to date-based shipping profiles for per-pub-date
fulfillment costing. At pub date, products are removed and fall back
to the General profile.

Operations:
- List all delivery profiles with their products
- Find/match profile by pub date
- Assign product variant to a profile
- Remove product variant from a profile (dissociate)
- Rename an empty profile for reuse
- Create a new profile from template (cloning zone structure)

Create-from-template clones the carrier participant configuration
(participantServices / fees / adaptToNewServicesFlag) from a known-good
reference profile at create time, rather than emitting bare participants.
Bare participants — a carrier referenced with no active service — return no
rate at checkout and silently break shipping. The builder now refuses to
create any carrier method that has no active service (and no
adaptToNewServicesFlag), so an empty or misconfigured reference raises
instead of minting an unserviced profile.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

LOCATION_GID = "gid://shopify/Location/40052293765"  # Kitchen Arts & Letters, Inc.

# The reference profile whose carrier participant config (services/fees/flags)
# new date profiles are cloned from. Must be a known-good, fully-serviced
# profile with US + World zones (e.g. the General profile, or a blessed date
# profile). Set in the environment; the create path raises if it is unset.
REFERENCE_PROFILE_GID_ENV = "SHIPPING_REFERENCE_PROFILE_GID"


# ──────────────────────────────────────────────
# Carrier services (store-level constants)
# These IDs are stable across all delivery profiles in this store.
# Verified from existing date-based profile structure.
# Informational since the builder clones participants from the reference
# profile; retained for documentation and any external callers.
# ──────────────────────────────────────────────

CARRIER_UPS = "gid://shopify/DeliveryCarrierService/33373421701"
CARRIER_USPS = "gid://shopify/DeliveryCarrierService/33373356165"
CARRIER_DHL_ECOMMERCE = "gid://shopify/DeliveryCarrierService/57607979141"
CARRIER_DHL_EXPRESS = "gid://shopify/DeliveryCarrierService/33373388933"

US_ZONE_CARRIERS = [CARRIER_UPS, CARRIER_USPS]
WORLD_ZONE_CARRIERS = [CARRIER_UPS, CARRIER_USPS, CARRIER_DHL_ECOMMERCE, CARRIER_DHL_EXPRESS]

# ──────────────────────────────────────────────
# Date ↔ Profile Name
# ──────────────────────────────────────────────

def pub_date_to_profile_name(pub_date: date) -> str:
    """Convert a date to the Shopify profile name format: 'Month Day, Year'."""
    return pub_date.strftime("%B %-d, %Y")


def profile_name_to_date(name: str) -> Optional[date]:
    """Parse a profile name like 'May 5, 2026' back to a date. Returns None if unparseable."""
    try:
        return datetime.strptime(name, "%B %d, %Y").date()
    except ValueError:
        try:
            # Handle single-digit day without leading zero
            return datetime.strptime(name, "%B %d, %Y").date()
        except ValueError:
            return None


# ──────────────────────────────────────────────
# GraphQL Queries
# ──────────────────────────────────────────────

LIST_PROFILES_QUERY = """
query ListDeliveryProfiles($cursor: String) {
  deliveryProfiles(first: 50, after: $cursor) {
    edges {
      cursor
      node {
        id
        name
        default
        profileItems(first: 250) {
          edges {
            node {
              product {
                id
                title
              }
              variants(first: 10) {
                edges {
                  node {
                    id
                    title
                  }
                }
              }
            }
          }
        }
      }
    }
    pageInfo {
      hasNextPage
    }
  }
}
"""

PROFILE_DETAIL_QUERY = """
query DeliveryProfileDetail($id: ID!) {
  deliveryProfile(id: $id) {
    id
    name
    default
    profileLocationGroups {
      locationGroup {
        id
      }
    }
    profileItems(first: 250) {
      edges {
        node {
          product {
            id
            title
          }
          variants(first: 10) {
            edges {
              node {
                id
                title
              }
            }
          }
        }
      }
    }
  }
}
"""

# Full zone/method/participant structure of a reference profile — the source
# of truth for cloning carrier participant config into new profiles.
REFERENCE_PROFILE_QUERY = """
query ReferenceProfileStructure($id: ID!) {
  deliveryProfile(id: $id) {
    id
    name
    profileLocationGroups {
      locationGroupZones(first: 20) {
        edges {
          node {
            zone {
              name
              countries {
                code {
                  countryCode
                  restOfWorld
                }
              }
            }
            methodDefinitions(first: 30) {
              edges {
                node {
                  name
                  active
                  rateProvider {
                    __typename
                    ... on DeliveryParticipant {
                      carrierService { id }
                      participantServices { name active }
                      fixedFee { amount currencyCode }
                      percentageOfRateFee
                      adaptToNewServicesFlag
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

PRODUCT_VARIANT_QUERY = """
query ProductVariant($id: ID!) {
  product(id: $id) {
    id
    title
    variants(first: 1) {
      edges {
        node {
          id
          title
        }
      }
    }
  }
}
"""

UPDATE_PROFILE_MUTATION = """
mutation deliveryProfileUpdate($id: ID!, $profile: DeliveryProfileInput!) {
  deliveryProfileUpdate(id: $id, profile: $profile) {
    profile {
      id
      name
    }
    userErrors {
      field
      message
    }
  }
}
"""

CREATE_PROFILE_MUTATION = """
mutation deliveryProfileCreate($profile: DeliveryProfileInput!) {
  deliveryProfileCreate(profile: $profile) {
    profile {
      id
      name
    }
    userErrors {
      field
      message
    }
  }
}
"""


# ──────────────────────────────────────────────
# Reference participant cloning
# ──────────────────────────────────────────────

def _fee_amount_is_positive(amount: Any) -> bool:
    try:
        return float(amount) > 0
    except (TypeError, ValueError):
        return False


def _zone_key(zone: Dict[str, Any]) -> Optional[str]:
    """Classify a reference zone as 'US' or 'World' by its countries.

    'US'    = any zone that includes the United States.
    'World' = the catch-all international zone, whether it uses Shopify's
              restOfWorld flag OR an explicit enumerated country list. This
              store's 'Rest of the World' zone lists ~188 countries rather than
              using the flag, so keying on the flag alone missed it.

    Keyed off countries (not the zone's display name) so it is robust to naming.
    """
    countries = zone.get("countries") or []
    has_us = False
    has_intl = False  # restOfWorld flag or any non-US country
    for c in countries:
        code = c.get("code") or {}
        if code.get("countryCode") == "US":
            has_us = True
        elif code.get("restOfWorld") or code.get("countryCode"):
            has_intl = True
    if has_us:
        return "US"
    if has_intl:
        return "World"
    return None


def _clone_participant(rate_provider: Dict[str, Any]) -> Dict[str, Any]:
    """Build a DeliveryParticipantInput from a reference DeliveryParticipant.

    Refuses (raises) if the reference participant has no active service and no
    adaptToNewServicesFlag — that is exactly the empty-participant state that
    returns no rate at checkout, and must never be propagated into a new
    profile.
    """
    carrier = rate_provider.get("carrierService") or {}
    carrier_id = carrier.get("id")
    if not carrier_id:
        raise ValueError("Reference participant has no carrierService id")

    services = [
        {"name": s.get("name"), "active": bool(s.get("active"))}
        for s in (rate_provider.get("participantServices") or [])
        if s.get("name")
    ]
    has_active_service = any(s["active"] for s in services)
    adapt = bool(rate_provider.get("adaptToNewServicesFlag"))

    if not has_active_service and not adapt:
        raise ValueError(
            f"Reference participant for carrier {carrier_id} has no active service "
            f"and adaptToNewServicesFlag is off — refusing to clone an unserviced "
            f"participant (this is the empty-profile bug)."
        )

    participant: Dict[str, Any] = {
        "carrierServiceId": carrier_id,
        "participantServices": services,
    }
    if adapt:
        participant["adaptToNewServicesFlag"] = True

    fee = rate_provider.get("fixedFee") or {}
    if _fee_amount_is_positive(fee.get("amount")):
        participant["fixedFee"] = {
            "amount": fee["amount"],
            "currencyCode": fee.get("currencyCode", "USD"),
        }

    pct = rate_provider.get("percentageOfRateFee")
    try:
        if pct is not None and float(pct) > 0:
            participant["percentageOfRateFee"] = pct
    except (TypeError, ValueError):
        pass

    return participant


def _reference_zone_methods_from_payload(profile: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Transform a reference deliveryProfile payload into cloned method-definition
    inputs, keyed by zone ('US' / 'World'). Only carrier-participant methods are
    cloned; flat DeliveryRateDefinition methods are skipped.
    """
    result: Dict[str, List[Dict[str, Any]]] = {}
    for lg in (profile.get("profileLocationGroups") or []):
        zone_edges = ((lg.get("locationGroupZones") or {}).get("edges")) or []
        for z_edge in zone_edges:
            znode = z_edge.get("node") or {}
            key = _zone_key(znode.get("zone") or {})
            if key not in ("US", "World"):
                continue
            method_edges = ((znode.get("methodDefinitions") or {}).get("edges")) or []
            for m_edge in method_edges:
                m = m_edge.get("node") or {}
                rp = m.get("rateProvider") or {}
                if rp.get("__typename") != "DeliveryParticipant":
                    continue
                participant = _clone_participant(rp)
                result.setdefault(key, []).append({
                    "name": m.get("name") or "shipping",
                    "active": bool(m.get("active", True)),
                    "participant": participant,
                })
    return result


def _build_profile_template_input(
    name: str,
    variant_gid: str,
    reference_zone_methods: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Build the DeliveryProfileInput replicating the canonical date-based profile:
    one location group with a US zone and a World zone, all carrier-calculated.

    Zone geography is fixed on the proven path: the US zone uses
    includeAllProvinces=True (Shopify requires the US country to have provinces;
    this is the intended mechanism and avoids enumerating 63 codes), and the
    World zone uses restOfWorld. The carrier methods (participant services /
    fees / flags) are cloned from the reference profile via
    reference_zone_methods.
    """
    logger.info(f"[build_input] name={name!r} variant_gid={variant_gid!r}")

    us_methods = reference_zone_methods.get("US")
    world_methods = reference_zone_methods.get("World")
    if not us_methods:
        raise ValueError(
            "No US-zone carrier methods cloned from reference profile — "
            "refusing to create an unserviced US zone."
        )
    if not world_methods:
        raise ValueError(
            "No World-zone carrier methods cloned from reference profile — "
            "refusing to create an unserviced World zone."
        )

    return {
        "name": name,
        "variantsToAssociate": [variant_gid],
        "locationGroupsToCreate": [
            {
                "locations": [LOCATION_GID],
                "zonesToCreate": [
                    {
                        "name": "US",
                        "countries": [
                            {
                                "code": "US",
                                "includeAllProvinces": True,
                            }
                        ],
                        "methodDefinitionsToCreate": us_methods,
                    },
                    {
                        "name": "World",
                        "countries": [{"restOfWorld": True}],
                        "methodDefinitionsToCreate": world_methods,
                    },
                ],
            }
        ],
    }


async def _fetch_reference_zone_methods(
    client: Any,
    reference_gid: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Read the reference profile and return cloned US + World method inputs.

    Raises if the reference is missing or does not yield serviced US and World
    zones — so a bad reference blocks creation rather than producing an empty
    profile.
    """
    result = await client.graphql(
        query=REFERENCE_PROFILE_QUERY,
        variables={"id": reference_gid},
    )
    profile = (result or {}).get("deliveryProfile")
    if not profile:
        raise ValueError(f"Reference delivery profile not found: {reference_gid}")

    zone_methods = _reference_zone_methods_from_payload(profile)
    if not zone_methods.get("US") or not zone_methods.get("World"):
        raise ValueError(
            f"Reference profile {reference_gid} did not yield serviced US and "
            f"World zones (got zones: {sorted(zone_methods)}). Refusing to clone."
        )
    logger.info(
        f"[reference] cloned {len(zone_methods['US'])} US + "
        f"{len(zone_methods['World'])} World methods from {reference_gid}"
    )
    return zone_methods


async def create_profile_from_template(
    client: Any,
    name: str,
    variant_gid: str,
    reference_gid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new delivery profile replicating the standard date-based structure,
    with the given product variant already associated. Carrier participant
    config is cloned from the reference profile.

    Returns the created profile dict (profile_gid, name).
    Raises ValueError on userErrors, missing reference, or an unserviced clone.
    """
    reference_gid = reference_gid or os.getenv(REFERENCE_PROFILE_GID_ENV)
    if not reference_gid:
        raise ValueError(
            f"{REFERENCE_PROFILE_GID_ENV} is not set — cannot clone carrier "
            f"participant config for new profile '{name}'."
        )

    zone_methods = await _fetch_reference_zone_methods(client, reference_gid)
    profile_input = _build_profile_template_input(name, variant_gid, zone_methods)

    result = await client.graphql(
        query=CREATE_PROFILE_MUTATION,
        variables={"profile": profile_input},
    )

    logger.info(f"[create_profile] raw result type={type(result)} value={result}")

    if result is None:
        raise ValueError("deliveryProfileCreate returned None from graphql client")

    payload = result.get("deliveryProfileCreate") or {}
    errors = payload.get("userErrors", [])
    if errors:
        logger.error(f"Failed to create profile '{name}': {errors}")
        raise ValueError(f"deliveryProfileCreate failed: {errors}")

    created = payload.get("profile")
    if not created:
        raise ValueError(f"deliveryProfileCreate returned no profile for '{name}' — payload={payload}")

    logger.info(f"Created new delivery profile '{name}' ({created['id']})")
    return {
        "profile_gid": created["id"],
        "profile_id": _extract_product_id(created["id"]),
        "name": created["name"],
        "pub_date": None,  # caller sets this
        "product_count": 1,
        "products": [],
    }


async def preview_reference_clone(
    client: Any,
    reference_gid: Optional[str] = None,
) -> Dict[str, Any]:
    """Read-only preview of what create_profile_from_template would clone.

    Resolves the reference GID (arg or SHIPPING_REFERENCE_PROFILE_GID), fetches
    the reference profile, and returns the exact US/World method inputs that
    would be written into a new profile — creating nothing. Raises the same way
    the create path would if the reference is missing or unserviced, so this
    doubles as a pre-flight check on the reference configuration.
    """
    reference_gid = reference_gid or os.getenv(REFERENCE_PROFILE_GID_ENV)
    if not reference_gid:
        raise ValueError(
            f"{REFERENCE_PROFILE_GID_ENV} is not set — nothing to preview."
        )
    zone_methods = await _fetch_reference_zone_methods(client, reference_gid)
    return {
        "reference_profile_gid": reference_gid,
        "zones": zone_methods,
    }


# ──────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────

def _extract_product_id(gid: str) -> int:
    return int(gid.split("/")[-1])


def _parse_profile_node(node: Dict) -> Dict[str, Any]:
    """Parse a delivery profile GraphQL node into a clean dict."""
    products = []
    for edge in node.get("profileItems", {}).get("edges", []):
        item = edge["node"]
        product = item.get("product", {})
        variants = []
        for v_edge in item.get("variants", {}).get("edges", []):
            variants.append({
                "variant_gid": v_edge["node"]["id"],
                "variant_title": v_edge["node"].get("title", "Default Title"),
            })
        products.append({
            "product_gid": product.get("id"),
            "product_id": _extract_product_id(product["id"]) if product.get("id") else None,
            "title": product.get("title"),
            "variants": variants,
        })

    profile_date = profile_name_to_date(node.get("name", ""))

    return {
        "profile_gid": node["id"],
        "profile_id": _extract_product_id(node["id"]),
        "name": node.get("name"),
        "is_default": node.get("default", False),
        "pub_date": profile_date.isoformat() if profile_date else None,
        "product_count": len(products),
        "products": products,
    }


# ──────────────────────────────────────────────
# Shopify operations
# ──────────────────────────────────────────────

async def list_shipping_profiles(client: Any) -> List[Dict[str, Any]]:
    """
    List all delivery profiles with their assigned products.
    Paginates through all profiles.
    """
    profiles = []
    cursor = None
    has_next = True

    while has_next:
        result = await client.graphql(
            query=LIST_PROFILES_QUERY,
            variables={"cursor": cursor},
        )
        edges = result.get("deliveryProfiles", {}).get("edges", [])
        for edge in edges:
            profiles.append(_parse_profile_node(edge["node"]))
        has_next = result.get("deliveryProfiles", {}).get("pageInfo", {}).get("hasNextPage", False)
        if has_next and edges:
            cursor = edges[-1]["cursor"]

    return profiles


async def get_profile_detail(client: Any, profile_gid: str) -> Dict[str, Any]:
    """Fetch a single profile with full detail."""
    result = await client.graphql(
        query=PROFILE_DETAIL_QUERY,
        variables={"id": profile_gid},
    )
    profile = result.get("deliveryProfile")
    if not profile:
        raise ValueError(f"Delivery profile not found: {profile_gid}")
    return _parse_profile_node(profile)


async def find_profile_for_date(client: Any, pub_date: date) -> Optional[Dict[str, Any]]:
    """
    Find a delivery profile matching the given pub date by name convention.
    Returns the profile dict or None if no match found.
    """
    target_name = pub_date_to_profile_name(pub_date)
    profiles = await list_shipping_profiles(client)

    for profile in profiles:
        if profile["name"] == target_name:
            return profile

    return None


async def get_variant_gid_for_product(client: Any, product_id: int) -> str:
    """Fetch the first variant GID for a product (books are single-variant)."""
    gid = f"gid://shopify/Product/{product_id}"
    result = await client.graphql(
        query=PRODUCT_VARIANT_QUERY,
        variables={"id": gid},
    )
    product = result.get("product")
    if not product:
        raise ValueError(f"Product not found: {product_id}")

    variant_edges = product.get("variants", {}).get("edges", [])
    if not variant_edges:
        raise ValueError(f"Product {product_id} has no variants")

    return variant_edges[0]["node"]["id"]


async def assign_product_to_profile(
    client: Any,
    profile_gid: str,
    product_id: int,
    variant_gid: Optional[str] = None,
) -> List[Dict]:
    """
    Assign a product (via its first variant) to a delivery profile.
    If the product is currently on a different profile, Shopify
    automatically moves it.
    Returns list of userErrors (empty on success).
    """
    if not variant_gid:
        variant_gid = await get_variant_gid_for_product(client, product_id)

    result = await client.graphql(
        query=UPDATE_PROFILE_MUTATION,
        variables={
            "id": profile_gid,
            "profile": {
                "variantsToAssociate": [variant_gid],
            },
        },
    )

    errors = result.get("deliveryProfileUpdate", {}).get("userErrors", [])
    if errors:
        logger.error(f"Failed to assign product {product_id} to profile {profile_gid}: {errors}")
    else:
        logger.info(f"Assigned product {product_id} to profile {profile_gid}")
    return errors


async def remove_product_from_profile(
    client: Any,
    profile_gid: str,
    product_id: int,
) -> List[Dict]:
    """
    Remove a product (via its first variant) from a delivery profile.
    The product falls back to the General profile.
    Returns list of userErrors (empty on success).
    """
    variant_gid = await get_variant_gid_for_product(client, product_id)

    result = await client.graphql(
        query=UPDATE_PROFILE_MUTATION,
        variables={
            "id": profile_gid,
            "profile": {
                "variantsToDissociate": [variant_gid],
            },
        },
    )

    errors = result.get("deliveryProfileUpdate", {}).get("userErrors", [])
    if errors:
        logger.error(f"Failed to remove product {product_id} from profile {profile_gid}: {errors}")
    else:
        logger.info(f"Removed product {product_id} from profile {profile_gid}")
    return errors


async def rename_profile(
    client: Any,
    profile_gid: str,
    new_name: str,
) -> List[Dict]:
    """
    Rename a delivery profile. Used to repurpose empty profiles for new pub dates.
    Returns list of userErrors (empty on success).
    """
    result = await client.graphql(
        query=UPDATE_PROFILE_MUTATION,
        variables={
            "id": profile_gid,
            "profile": {
                "name": new_name,
            },
        },
    )

    errors = result.get("deliveryProfileUpdate", {}).get("userErrors", [])
    if errors:
        logger.error(f"Failed to rename profile {profile_gid} to '{new_name}': {errors}")
    else:
        logger.info(f"Renamed profile {profile_gid} to '{new_name}'")
    return errors


async def find_or_create_profile_for_date(
    client: Any,
    pub_date: date,
    product_id: int,
    variant_gid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Find an existing profile for the given pub date, repurpose an empty
    historical profile, or create a new one from template.

    Strategy:
    1. Look for a profile named exactly "Month Day, Year" for the pub date.
    2. If not found, look for any non-default empty profile (0 products)
       whose name parses as a past date, and rename it to the pub date name.
    3. If no empty profiles available, create a new profile from template
       with the canonical US + World zone structure.

    Returns the profile dict.
    """
    target_name = pub_date_to_profile_name(pub_date)
    profiles = await list_shipping_profiles(client)

    # 1. Exact match
    for profile in profiles:
        if profile["name"] == target_name:
            return profile

    # 2. Find empty historical profile to repurpose
    today = date.today()
    empty_candidates = []
    for profile in profiles:
        if profile["is_default"]:
            continue
        if profile["product_count"] > 0:
            continue
        # Parse date from name
        profile_date = profile_name_to_date(profile["name"])
        if profile_date and profile_date < today:
            empty_candidates.append((profile_date, profile))

    if empty_candidates:
        # Use the oldest empty profile
        empty_candidates.sort(key=lambda x: x[0])
        _, chosen = empty_candidates[0]

        logger.info(f"Repurposing empty profile '{chosen['name']}' → '{target_name}'")
        errors = await rename_profile(client, chosen["profile_gid"], target_name)
        if errors:
            raise ValueError(f"Failed to rename profile: {errors}")

        # Refresh the profile data
        chosen["name"] = target_name
        chosen["pub_date"] = pub_date.isoformat()
        return chosen

    # 3. No empty profiles available — create a new one from template
    logger.info(f"[find_or_create] reached step 3 create for '{target_name}' product={product_id}")
    if not variant_gid:
        variant_gid = await get_variant_gid_for_product(client, product_id)
    logger.info(f"[find_or_create] variant_gid={variant_gid}")
    created = await create_profile_from_template(client, target_name, variant_gid)
    created["pub_date"] = pub_date.isoformat()
    return created
