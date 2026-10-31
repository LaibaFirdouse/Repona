from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter(prefix="/api/v2"))


@router.get("/users/{user_id}")
def read_user(user_id: str) -> dict:
    return {"id": user_id}


@router.post("/users")
def write_user() -> dict:
    return {"ok": True}


@router.get("/items")
def list_items_a() -> list:
    return []


@router.get("/items")
def list_items_b() -> list:
    return []


app.include_router(router)
