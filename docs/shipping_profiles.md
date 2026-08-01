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
  - **US** — country `US` (with full province list, see below); methods
    `ups_shipping`, `usps`
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

Shopify requires the US country in a shipping zone to enumerate its provinces
explicitly — creating a US zone with only `{"code": "US"}` fails with:

> Cannot save zone. Country: 'United States' must have at least one province
> associated.

The US zone country input must therefore be
`{"code": "US", "provinces": [{"code": "AL"}, ...]}`. The World zone
(`restOfWorld: true`) needs no provinces.

Canonical province set (63 entries — 50 states + DC + territories + armed
forces + Pacific affiliated states), pulled from a live date profile and
kept in `services/shipping_profiles.py` as `US_PROVINCE_CODES`:

```
AL, AK, AS, AZ, AR, AA, AE, AP, CA, CO, CT, DE, DC, FM, FL, GA, GU, HI, ID,
IL, IN, IA, KS, KY, LA, ME, MH, MD, MA, MI, MN, MS, MO, MT, NE, NV, NH, NJ,
NM, NY, NC, ND, MP, OH, OK, OR, PW, PA, PR, RI, SC, SD, TN, TX, UT, VT, VI,
VA, WA, WV, WI, WY
```

Breakdown: `AS` American Samoa, `GU` Guam, `MP` Northern Mariana Islands,
`PR` Puerto Rico, `VI` Virgin Islands (territories); `AA`/`AE`/`AP` armed
forces; `FM` Micronesia, `MH` Marshall Islands, `PW` Palau (Pacific affiliated
states); `DC` District of Columbia.

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
