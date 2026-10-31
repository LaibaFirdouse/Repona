def reserve_tracking_code(lane: str, weight_kg: float) -> str:
    token = abs(hash((lane, round(weight_kg, 2)))) % 10_000_000
    return f"{lane[:3].upper()}-{token:07d}"
