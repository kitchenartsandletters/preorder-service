def split_refund_payload(payload, event_id):
    rows = []
    for rli in payload.get("refund_line_items", []):
        line_item = rli.get("line_item") or {}
        qty = int(rli.get("quantity") or 0)
        if qty <= 0:
            continue
        rows.append({
            "event_id": event_id,
            "topic": "refunds/create",
            "order_id": payload["order_id"],
            "line_item_id": line_item.get("id"),
            "product_id": line_item.get("product_id"),
            "variant_id": line_item.get("variant_id"),
            "quantity": qty,
        })
    return rows


def test_refund_split_single_line():
    payload = {
        "order_id": 111,
        "refund_line_items": [
            {
                "quantity": 2,
                "line_item": {
                    "id": 10,
                    "product_id": 999,
                    "variant_id": 888
                }
            }
        ]
    }

    rows = split_refund_payload(payload, event_id="abc")

    assert len(rows) == 1
    assert rows[0]["quantity"] == 2
    assert rows[0]["product_id"] == 999


def test_refund_split_multiple_lines():
    payload = {
        "order_id": 111,
        "refund_line_items": [
            {
                "quantity": 1,
                "line_item": {
                    "id": 10,
                    "product_id": 100,
                    "variant_id": 200
                }
            },
            {
                "quantity": 3,
                "line_item": {
                    "id": 11,
                    "product_id": 101,
                    "variant_id": 201
                }
            }
        ]
    }

    rows = split_refund_payload(payload, event_id="abc")

    assert len(rows) == 2
    assert rows[0]["quantity"] == 1
    assert rows[1]["quantity"] == 3
    assert rows[1]["product_id"] == 101


def test_refund_split_zero_quantity_ignored():
    payload = {
        "order_id": 111,
        "refund_line_items": [
            {
                "quantity": 0,
                "line_item": {
                    "id": 10,
                    "product_id": 100,
                    "variant_id": 200
                }
            }
        ]
    }

    rows = split_refund_payload(payload, event_id="abc")

    assert len(rows) == 0