const fs = require('fs')

// Read JSON
const rawData = fs.readFileSync('results2.json', 'utf8')
const data = JSON.parse(rawData)

const orders = data?.data?.orders?.edges || []

let csvRows = [
  'order_id,line_item_id,product_id,variant_id,qty,created_at'
]

orders.forEach(orderEdge => {

  const order = orderEdge.node
  const orderId = order.legacyResourceId

  order.lineItems.edges.forEach(itemEdge => {

    const item = itemEdge.node

    const lineItemId = item.id.split('/').pop()

    const productId =
      item.product?.legacyResourceId ?? 'NULL'

    const variantId =
      item.variant?.legacyResourceId ?? 'NULL'

    const row = [
      orderId,
      lineItemId,
      productId,
      variantId,
      item.quantity,
      order.createdAt
    ]

    csvRows.push(row.join(','))
  })
})

fs.writeFileSync(
  'shopify_orders_7213582123141.csv',
  csvRows.join('\n')
)

console.log(
  `Success! CSV created with ${csvRows.length - 1} line items.`
)