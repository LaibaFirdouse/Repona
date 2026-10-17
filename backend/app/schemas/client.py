from pydantic import BaseModel, Field


class ClientSessionResponse(BaseModel):
    client_id: str = Field(..., min_length=36, max_length=36)
    ttl_hours: int = Field(..., ge=1, le=24 * 14)
