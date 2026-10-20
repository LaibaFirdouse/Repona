from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.v1.router import api_router
from app.core.config import PROJECT_ROOT, settings
from app.core.logging_setup import configure_logging
from app.db.session import create_database_tables
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_logging import RequestLoggingMiddleware

PAGE = PROJECT_ROOT / "frontend" / "index.html"


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
    )
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(PAGE)

    @app.on_event("startup")
    def on_startup() -> None:
        create_database_tables()
        if settings.embed_warmup_on_startup:
            from app.services.refresh_service import _embed

            _embed("warmup")

    return app


app = create_app()
