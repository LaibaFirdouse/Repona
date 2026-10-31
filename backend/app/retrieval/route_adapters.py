"""Framework-specific HTTP route file/symbol recognisers.

Adapters only recognise layout conventions. They do not invent CALLS edges.
Ambiguous matches stay unresolved.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.catalog import Chunk, File, GenerationChunk, GenerationFile, IndexGeneration, Symbol
from app.retrieval.chunk_selection import is_test_path, pick_best_symbol_chunk
from app.retrieval.fusion import ChunkChoice

# Next.js App Router / Pages API
_NEXT_ROUTE_FILE = re.compile(
    r"(?:^|/)(?:app/)?api/.+/route\.(?:ts|tsx|js|jsx)$|(?:^|/)pages/api/.+\.(?:ts|tsx|js|jsx)$",
    re.I,
)
# FastAPI / Starlette style modules often live under routers/, api/, routes/
_FASTAPI_DECORATOR = re.compile(
    r"@(?:app|router)\.(get|post|put|patch|delete)\(\s*[`'\"]([^`'\"]+)[`'\"]",
    re.I,
)
_EXPRESS_ROUTE = re.compile(
    r"(?:app|router)\.(get|post|put|patch|delete)\(\s*[`'\"]([^`'\"]+)[`'\"]",
    re.I,
)


def is_next_route_file(path: str) -> bool:
    return bool(_NEXT_ROUTE_FILE.search(path.replace("\\", "/")))


def next_route_candidates(api_path: str) -> list[str]:
    cleaned = api_path.strip().split("?")[0].rstrip("/")
    if cleaned.startswith("/"):
        cleaned = cleaned[1:]
    return [
        f"app/{cleaned}/route.ts",
        f"app/{cleaned}/route.tsx",
        f"app/{cleaned}/route.js",
        f"app/{cleaned}/route.jsx",
        f"pages/{cleaned}.ts",
        f"pages/{cleaned}.tsx",
        f"pages/{cleaned}.js",
        f"pages/{cleaned}.jsx",
        f"{cleaned}/route.ts",
        f"{cleaned}.ts",
    ]


def files_for_http_path(db: Session, generation_id: str, api_path: str) -> list[File]:
    """Resolve a literal HTTP path to route implementation files via adapters."""
    cleaned = api_path.strip().split("?")[0].rstrip("/")
    if cleaned.startswith("/"):
        cleaned = cleaned[1:]
    found: list[File] = []
    seen: set[str] = set()

    def add(file: File | None) -> None:
        if file is None or file.id in seen:
            return
        seen.add(file.id)
        found.append(file)

    for relative in next_route_candidates(api_path):
        add(
            db.query(File)
            .join(GenerationFile, GenerationFile.file_id == File.id)
            .filter(GenerationFile.index_generation_id == generation_id, File.relative_path == relative)
            .first()
        )
    if found:
        return found

    needle = cleaned.lower()
    rows = (
        db.query(File)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(GenerationFile.index_generation_id == generation_id)
        .order_by(File.relative_path, File.id)
        .limit(500)
        .all()
    )
    for file in rows:
        path = file.relative_path.replace("\\", "/")
        if is_test_path(path):
            continue
        path_l = path.lower()
        if needle and needle in path_l and is_next_route_file(path):
            add(file)
        if len(found) >= 3:
            return found

    # FastAPI / Express: match decorator or router string in chunk lexical text.
    for file in rows:
        path = file.relative_path.replace("\\", "/")
        if is_test_path(path):
            continue
        if not path.endswith((".py", ".js", ".ts", ".tsx", ".jsx")):
            continue
        if _file_mentions_http_path(db, generation_id, file.id, api_path, cleaned):
            add(file)
        if len(found) >= 3:
            break
    return found


def _file_mentions_http_path(
    db: Session,
    generation_id: str,
    file_id: str,
    api_path: str,
    cleaned: str,
) -> bool:
    variants = {api_path, "/" + cleaned, cleaned}
    rows = (
        db.query(Chunk)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(
            GenerationChunk.index_generation_id == generation_id,
            Chunk.file_id == file_id,
            Chunk.kind.in_(("symbol_body", "symbol_part", "file_overview")),
        )
        .limit(12)
        .all()
    )
    for chunk in rows:
        # Lexical text is stored on GenerationChunk via search vector source; use span identity only.
        # Fall back to qualified path substring match on relative path stem.
        del chunk
    path_row = db.get(File, file_id)
    if path_row is None:
        return False
    path_l = path_row.relative_path.replace("\\", "/").lower()
    stem = cleaned.rsplit("/", 1)[-1].lower()
    if stem and len(stem) >= 3 and stem in path_l:
        return True
    # Symbol names that echo the path leaf (create_order for /orders).
    symbols = (
        db.query(Symbol)
        .filter(Symbol.file_id == file_id)
        .order_by(Symbol.qualified_name, Symbol.id)
        .limit(40)
        .all()
    )
    for symbol in symbols:
        name = (symbol.name or "").lower()
        if stem and stem.replace("-", "_") in name.replace("-", "_"):
            return True
        for variant in variants:
            token = variant.strip("/").replace("-", "_").lower()
            if token and token in name:
                return True
    return False


def handler_symbols_for_route(
    db: Session,
    generation: IndexGeneration,
    file: File,
    method: str,
) -> list[str]:
    """Prefer HTTP-method-named handlers; otherwise first few functions in the file."""
    names = {method.upper(), method.lower(), method.capitalize(), "handler", "default"}
    rows = (
        db.query(Symbol)
        .filter(
            Symbol.file_id == file.id,
            Symbol.repository_id == generation.repository_id,
            Symbol.parser_version == generation.parser_version,
            Symbol.kind.in_(("function", "method")),
        )
        .order_by(Symbol.qualified_name, Symbol.id)
        .all()
    )
    matched = [row.id for row in rows if row.name in names]
    if matched:
        return matched[:3]
    # FastAPI handlers are often uniquely named functions in the route module.
    return [row.id for row in rows[:3]]


def catalog_route_seed_choices(
    db: Session,
    generation: IndexGeneration,
    *,
    terms: list[str],
    limit: int,
) -> list[ChunkChoice]:
    """Seed route-like files whose paths overlap question/catalog terms."""
    rows = (
        db.query(File)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(GenerationFile.index_generation_id == generation.id)
        .order_by(File.relative_path, File.id)
        .all()
    )
    hints = [hint.lower() for hint in terms if len(hint) >= 3]
    matched: list[File] = []
    for file in rows:
        path = file.relative_path.replace("\\", "/")
        if is_test_path(path):
            continue
        path_l = path.lower()
        is_route_shaped = (
            is_next_route_file(path)
            or "/routers/" in path_l
            or "/routes/" in path_l
            or path_l.endswith(("router.py", "routes.py", "api.py", "main.py"))
        )
        if not is_route_shaped:
            continue
        if hints and not any(hint in path_l for hint in hints if len(hint) >= 4):
            continue
        matched.append(file)
        if len(matched) >= limit:
            break
    if not matched:
        for file in rows:
            path = file.relative_path.replace("\\", "/")
            if is_test_path(path):
                continue
            if is_next_route_file(path) or path.replace("\\", "/").lower().endswith(
                ("router.py", "routes.py")
            ):
                matched.append(file)
            if len(matched) >= limit:
                break
    from app.retrieval.catalog_context import chunk_choices_for_files

    return chunk_choices_for_files(db, generation.id, matched, limit=limit, bodies_per_file=2)


def extract_framework_routes_from_text(text: str) -> list[tuple[str, str]]:
    """Return (method, path) pairs declared in FastAPI/Express source text."""
    found: list[tuple[str, str]] = []
    for pattern in (_FASTAPI_DECORATOR, _EXPRESS_ROUTE):
        for match in pattern.finditer(text):
            method = match.group(1).upper()
            path = match.group(2).strip()
            if path.startswith("/"):
                found.append((method, path.split("?")[0]))
    return found
