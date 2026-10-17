from fastapi import APIRouter

from app.api.v1.routes import ask, client, health, repositories, repository

api_router = APIRouter()
api_router.include_router(client.router)
api_router.include_router(ask.router)
api_router.include_router(health.router)
api_router.include_router(repository.router)
api_router.include_router(repositories.router)
