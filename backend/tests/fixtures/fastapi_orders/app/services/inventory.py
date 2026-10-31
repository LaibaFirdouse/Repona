def assert_sku_available(sku: str, quantity: int) -> None:
    if quantity < 1:
        raise ValueError("quantity must be positive")
    if not sku.strip():
        raise ValueError("sku required")
