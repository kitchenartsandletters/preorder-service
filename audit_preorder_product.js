import fetch from "node-fetch"
import { createClient } from "@supabase/supabase-js"
import 'dotenv/config'

const SHOPIFY_STORE = process.env.SHOP_URL
const SHOPIFY_TOKEN = process.env.SHOPIFY_ACCESS_TOKEN

const SUPABASE_URL = process.env.SUPABASE_URL
const SUPABASE_SERVICE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY

// Optional product id via CLI
// node audit_preorder_product.js 7213582123141  → single product
// node audit_preorder_product.js                → ALL products
const PRODUCT_ID = process.argv[2] || null

const supabase = createClient(
  SUPABASE_URL,
  SUPABASE_SERVICE_KEY
)

// IMPORTANT:
// We intentionally do NOT use Shopify order search filters.
// Shopify search can silently omit orders depending on indexing and query rules.
// Instead we paginate across the entire order history and locally filter
// line items for the product we are auditing. This guarantees full coverage
// of historical orders.
async function fetchOrdersPage(cursor = null) {

  const query = `
  query OrdersPage($cursor: String) {
    orders(first: 250, after: $cursor, sortKey: CREATED_AT) {
      pageInfo {
        hasNextPage
        endCursor
      }
      edges {
        node {
          legacyResourceId
          createdAt
          lineItems(first: 50) {
            edges {
              node {
                id
                quantity
                product {
                  legacyResourceId
                }
                variant {
                  legacyResourceId
                }
              }
            }
          }
        }
      }
    }
  }`

  const variables = {
    cursor
  }

  const response = await fetch(
    `https://${SHOPIFY_STORE}/admin/api/2025-01/graphql.json`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_TOKEN
      },
      body: JSON.stringify({ query, variables })
    }
  )

  const json = await response.json()

  return json.data.orders
}

async function fetchAllOrders() {

  let allEdges = []
  let cursor = null
  let page = 1

  while (true) {

    const orders = await fetchOrdersPage(cursor)

    console.log(`Fetched page ${page} — ${orders.edges.length} orders`)

    allEdges.push(...orders.edges)

    if (!orders.pageInfo.hasNextPage) break

    cursor = orders.pageInfo.endCursor

    page++
  }

  return allEdges
}

async function run() {

  console.log(
    PRODUCT_ID
      ? `Auditing product ${PRODUCT_ID}`
      : `Auditing ALL products across full order history`
  )

  const orders = await fetchAllOrders()

  const rows = []

  for (const orderEdge of orders) {

    const order = orderEdge.node
    const orderId = order.legacyResourceId

    for (const itemEdge of order.lineItems.edges) {

      const item = itemEdge.node

      const productId = item.product?.legacyResourceId

      // If auditing a specific product, filter.
      // Otherwise capture every product.
      if (PRODUCT_ID && productId != PRODUCT_ID) continue

      rows.push({
        order_id: orderId,
        line_item_id: item.id.split("/").pop(),
        product_id: productId,
        variant_id: item.variant?.legacyResourceId ?? null,
        qty: item.quantity,
        created_at: order.createdAt
      })
    }
  }

  console.log(`\nExtracted ${rows.length} matching line items`)

  if (rows.length === 0) {
    console.log("No rows found — check product ID or query filter")
    return
  }

  if (PRODUCT_ID) {
    console.log("Clearing previous staging rows for this product...")

    await supabase
      .from("shopify_orders_stage")
      .delete()
      .eq("product_id", PRODUCT_ID)
  } else {
    console.log("Clearing entire staging table (full audit mode)...")

    await supabase
      .from("shopify_orders_stage")
      .delete()
      .neq("product_id", -1)
  }

  console.log(`Inserting ${rows.length} rows into Supabase`)

  const { error } = await supabase
    .from("shopify_orders_stage")
    .insert(rows)

  if (error) {
    console.error(error)
    process.exit(1)
  }

  console.log("\nAudit load complete.")
}

run()