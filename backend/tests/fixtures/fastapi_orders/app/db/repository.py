_ORDERS: list[dict] = []


def persist_order_record(*, sku: str, quantity: int) -> dict:
    record = {"id": f"ord-{len(_ORDERS) + 1}", "sku": sku, "quantity": quantity, "status": "accepted"}
    _ORDERS.append(record)
    return record


def unused_lookup(order_id: str) -> dict | None:
    """Imported nowhere in the request path."""
    for row in _ORDERS:
        if row["id"] == order_id:
            return row
    return None
