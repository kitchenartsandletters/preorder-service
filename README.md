# Division of Responsibility  
**webhook-gateway vs preorder-service**

This section defines the strict boundary between the two systems.

---

# 1. webhook-gateway (Upstream Ingest Layer)

## Responsibilities:
- Receive Shopify webhooks
- Validate HMAC signatures
- Extract minimal factual fields:
  - order_id, product_id, variant_id, sku, quantity, line_item_id
  - event_id
  - raw payload
  - raw headers
- Insert into `preorder.tracking` with:
  - status = 'pending'
  - approved = false
  - processed = false

## Explicit Non-Responsibilities:
- No preorder classification
- No anomaly detection
- No pub date parsing
- No override logic
- No status mutation
- No approvals
- No inventory interpretation
- No Slack / GitHub / NYT flows

Gateway is a **pure logging and relay service**.

---

# 2. preorder-service (Classification + State Machine)

## Responsibilities:
- Classify products into:
  - active_preorder
  - historical_preorder
  - anomaly_*
- Determine effective_pub_date
- Detect and categorize all anomalies
- Maintain derived states in Supabase
- Apply override logic
- Prepare weekly release lists
- Serve data to Admin Dashboard
- Support approvals and administrative overrides

## Explicit Non-Responsibilities:
- No Shopify writes (unless future phase adds it)
- No Slack notifications (future phase)
- No GitHub issue creation
- No publishing/unpublishing decisions

Preorder-service is the **brain**.  
It transforms raw events into structured preorder intelligence.

---

# END