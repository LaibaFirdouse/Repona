from app.db.repository import persist_order_record
from app.schemas.order import OrderCreate
from app.services.inventory import assert_sku_available


def place_customer_order(payload: OrderCreate) -> dict:
    assert_sku_available(payload.sku, payload.quantity)
    return persist_order_record(sku=payload.sku, quantity=payload.quantity)


def preview_customer_order(payload: OrderCreate) -> dict:
    """Similar name, never called from the HTTP route."""
    return {"id": "preview", "sku": payload.sku, "quantity": payload.quantity}
