from fastapi import FastAPI

from app.routers.shipments import router as shipment_router

app = FastAPI(title="Shipment API")
app.include_router(shipment_router)
