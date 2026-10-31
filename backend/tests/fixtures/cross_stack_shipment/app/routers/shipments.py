from fastapi import APIRouter

from app.services.dispatcher import reserve_tracking_code

router = APIRouter(prefix="/api/v1")


@router.post("/shipments")
def create_shipment(lane: str, weight_kg: float) -> dict:
    code = reserve_tracking_code(lane, weight_kg)
    return {"trackingCode": code}
