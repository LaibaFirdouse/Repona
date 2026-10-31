from fastapi import APIRouter, HTTPException

from app.schemas.order import OrderCreate
from app.services.order_service import place_customer_order

router = APIRouter(prefix="/orders")


@router.post("")
async def submit_customer_order(payload: OrderCreate):
    try:
        record = place_customer_order(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"order_id": record["id"], "status": record["status"]}
