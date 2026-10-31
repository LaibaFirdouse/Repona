from pydantic import BaseModel, Field


class OrderCreate(BaseModel):
    sku: str = Field(min_length=1)
    quantity: int = Field(gt=0)
