from pydantic import BaseModel, Field


class ServerLogEntry(BaseModel):
    ts: str
    level: str
    logger: str
    message: str


class ServerLogsResponse(BaseModel):
    buffer_max: int = Field(description="Maximum entries kept in memory")
    count: int = Field(description="Entries returned in this response")
    buffered_total: int = Field(description="Entries currently held in memory")
    entries: list[ServerLogEntry]
