from fastapi import APIRouter, Header, HTTPException, Query

from app.core.config import settings
from app.core.log_buffer import get_log_buffer
from app.schemas.logs import ServerLogEntry, ServerLogsResponse

router = APIRouter(tags=["logs"])


def _authorize_logs(authorization: str | None, logs_token: str | None) -> None:
    expected = settings.server_logs_token.strip()
    if not expected:
        return
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    token = (logs_token or bearer).strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing server logs token")


@router.get("/logs", response_model=ServerLogsResponse)
def list_server_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    level: str = Query(default="INFO", description="Minimum log level"),
    logger_prefix: str | None = Query(default=None, description="Filter by logger name prefix"),
    authorization: str | None = Header(default=None),
    x_server_logs_token: str | None = Header(default=None, alias="X-Server-Logs-Token"),
) -> ServerLogsResponse:
    if not settings.server_logs_enabled:
        raise HTTPException(status_code=404, detail="server logs are disabled")
    _authorize_logs(authorization, x_server_logs_token)

    buffer = get_log_buffer()
    rows = buffer.fetch(limit=limit, min_level=level, logger_prefix=logger_prefix)
    return ServerLogsResponse(
        retention_hours=buffer.retention_hours,
        count=len(rows),
        buffered_total=buffer.count(),
        entries=[ServerLogEntry(**row) for row in rows],
    )
