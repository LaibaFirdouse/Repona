from fastapi import APIRouter

from app.api.v1.routes import ask, health, repository

api_router = APIRouter()
api_router.include_router(ask.router)
api_router.include_router(health.router)
api_router.include_router(repository.router)
