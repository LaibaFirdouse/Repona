from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.v1.router import api_router
from app.core.config import PROJECT_ROOT, settings
from app.db.session import create_database_tables
from app.middleware.rate_limit import RateLimitMiddleware

PAGE = PROJECT_ROOT / "frontend" / "index.html"


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
    )
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    app.add_middleware(RateLimitMiddleware)

    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(PAGE)

    @app.on_event("startup")
    def on_startup() -> None:
        create_database_tables()

    return app


app = create_app()
