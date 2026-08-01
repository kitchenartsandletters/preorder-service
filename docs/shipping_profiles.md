# Shipping Profile Management

How the preorder system creates and manages Shopify delivery profiles for
date-based preorder fulfillment. Lives in `services/shipping_profiles.py`,
exposed through `routes/admin_shipping.py`, and driven from the Preorders →
Shipping Profiles area of the admin-dashboard.

## Purpose

Preorder books are grouped onto date-based delivery profiles named
"Month Day, Year" (e.g. "October 21, 2026") so fulfillment costs can be
tracked per pub date. At pub date a product is removed from its date profile
and falls back to the General profile.

## Profile resolution order

`find_or_create_profile_for_date(client, pub_date, product_id, variant_gid=None)`:

1. **Exact match** — a profile already named for the pub date.
2. **Repurpose** — rename an empty, non-default profile whose name parses as a
   past date.
3. **Create from template** — mint a new profile with the canonical structure
   below (added because the store can run out of empty profiles to repurpose).

## Canonical profile structure

Every date-based preorder profile has an identical shape, verified from live
Shopify GraphQL. New profiles must replicate it exactly.

- **Location group:** single location
  `gid://shopify/Location/40052293765` (Kitchen Arts & Letters)
- **Zones:** two
  - **US** — country `US` with `includeAllProvinces: true` (see below);
    methods `ups_shipping`, `usps`
  - **World** — `restOfWorld: true`; methods `ups_shipping`, `usps`,
    `dhl_ecommerce`, `dhl_express`
- **Rates:** all `DeliveryParticipant` (carrier-calculated), referencing
  store-level carrier services.

### Carrier service IDs (store-level constants)

| Method name      | Carrier service GID                                    |
|------------------|--------------------------------------------------------|
| `ups_shipping`   | `gid://shopify/DeliveryCarrierService/33373421701`     |
| `usps`           | `gid://shopify/DeliveryCarrierService/33373356165`     |
| `dhl_ecommerce`  | `gid://shopify/DeliveryCarrierService/57607979141`     |
| `dhl_express`    | `gid://shopify/DeliveryCarrierService/33373388933`     |

These IDs are stable across all profiles in the store, so the create path can
reference them directly rather than reading them from a template profile.

### US province requirement

Shopify requires the US country in a shipping zone to have provinces
associated — creating a US zone with only `{"code": "US"}` fails with:

> Cannot save zone. Country: 'United States' must have at least one province
> associated.

The fix is to set `includeAllProvinces: true` on the US country input. This
tells Shopify to cover every US province without listing any:

```json
{"code": "US", "includeAllProvinces": true}
```

This is the intended mechanism, confirmed via `DeliveryCountryInput`
introspection. It is what `_build_profile_template_input` emits today. The
World zone (`restOfWorld: true`) needs no provinces.

> **Superseded approach.** An earlier fix enumerated all 63 province codes
> (50 states + DC + territories + armed forces + Pacific affiliated states) in
> a `provinces: [{"code": "AL"}, ...]` array, held in a `US_PROVINCE_CODES`
> constant. That satisfied Shopify but was brittle and verbose;
> `includeAllProvinces: true` replaces it. There is no longer a province-code
> list in `services/shipping_profiles.py`.

### Participant services (Path A)

New profiles are created with `participant: { carrierServiceId }` only, letting
each carrier account apply its default service selection. We deliberately do not
replicate each carrier's per-service active flags (e.g. USPS enabling only
"Priority Mail Express International" and "Priority Mail International"). Carrier
accounts are store-shared, so new profiles behave identically at checkout. If a
created profile ever diverges from existing ones at checkout, revisit this and
replicate `participantServices` explicitly.

## GraphQL reference

Read a profile's full zone/method structure:

```graphql
query {
  deliveryProfile(id: "gid://shopify/DeliveryProfile/<id>") {
    profileLocationGroups {
      locationGroupZones(first: 5) {
        edges { node { zone {
          name
          countries { code { countryCode restOfWorld } provinces { code name } }
        } } }
      }
    }
  }
}
```

Create a profile: `deliveryProfileCreate(profile: DeliveryProfileInput!)` — see
`CREATE_PROFILE_MUTATION` and `_build_profile_template_input` in
`services/shipping_profiles.py`.

## Auth & deployment

- Shopify auth via `shopify_token.get_token_sync()` (OAuth client-credentials);
  `SHOPIFY_ACCESS_TOKEN` is retired.
- `preorder-service` deploys to Railway from GitHub; changes land via PR.
- The admin-dashboard Shipping Profiles UI calls
  `POST /admin/preorders/shipping/profiles/{assign,remove,reconcile,rename}`.
