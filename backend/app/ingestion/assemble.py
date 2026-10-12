"""Turn included blob bytes into ids, chunks, and generation-scoped edges."""

from __future__ import annotations

import multiprocessing
import os
import posixpath
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field

from app.ingestion.chunking import Span, build_chunks
from app.ingestion.inclusion import is_included
from app.ingestion.parse import ParsedFile, parse_file
from app.ingestion.versions import (
    CHUNKING_VERSION,
    EMBEDDING_INPUT_VERSION,
    INCLUSION_RULES_VERSION,
    LEXICAL_INDEX_VERSION,
    PARSER_VERSION,
)
from app.storage.identity import (
    chunk_id,
    content_hash,
    context_prefix,
    directory_id,
    edge_id,
    embedding_input_hash,
    file_id,
    identity_key,
    lexical_input,
    line_count,
    line_labels,
    line_spans,
    logical_file_key,
    symbol_id,
)


@dataclass(frozen=True)
class SourceBlob:
    relative_path: str
    mode: str
    git_blob_id: str
    payload: bytes


@dataclass
class SymbolRecord:
    id: str
    file_id: str
    parent_symbol_id: str | None
    identity_key: str
    kind: str
    name: str
    qualified_name: str
    signature: str
    ordinal: int
    language: str
    start_byte: int
    end_byte: int
    start_line: int | None
    end_line: int | None
    relative_path: str


@dataclass
class ChunkRecord:
    id: str
    file_id: str
    symbol_id: str | None
    kind: str
    ordinal: int
    start_byte: int
    end_byte: int
    start_line: int | None
    end_line: int | None
    span_hash: str
    context_prefix: str
    embedding_input_hash: str
    lexical_text: str


@dataclass
class FileRecord:
    id: str
    directory_id: str
    logical_file_key: str
    relative_path: str
    language: str | None
    extension: str | None
    line_count: int
    byte_size: int
    content_hash: str
    git_blob_id: str
    parse_status: str
    parse_message: str | None


@dataclass
class DirectoryRecord:
    id: str
    parent_directory_id: str | None
    relative_path: str
    name: str
    depth: int


@dataclass
class EdgeRecord:
    relationship: str
    source_id: str
    target_id: str
    mentioned_name: str
    resolution: str
    edge_id: str


@dataclass
class TechnologyRecord:
    name: str
    evidence_file_id: str
    evidence_text: str


@dataclass
class GenerationDraft:
    files: list[FileRecord] = field(default_factory=list)
    directories: list[DirectoryRecord] = field(default_factory=list)
    symbols: list[SymbolRecord] = field(default_factory=list)
    chunks: list[ChunkRecord] = field(default_factory=list)
    edges: list[EdgeRecord] = field(default_factory=list)
    technologies: list[TechnologyRecord] = field(default_factory=list)


def assemble_generation(
    repository: str,
    commit_sha: str,
    blobs: list[SourceBlob],
    embedding_fingerprint: str,
    index_generation: str,
    on_progress=None,
    workers: int | None = None,
    cache=None,
) -> GenerationDraft:
    included = [blob for blob in blobs if is_included(blob.relative_path, blob.mode)]
    included.sort(key=lambda blob: blob.relative_path.encode("utf-8"))
    draft = GenerationDraft()
    parsed_by_path: dict[str, ParsedFile] = {}
    symbols_by_file: dict[str, list[SymbolRecord]] = {}
    file_by_path: dict[str, FileRecord] = {}
    directories: dict[str, DirectoryRecord] = {}
    products = _file_products(repository, included, embedding_fingerprint, on_progress, workers, cache)

    for blob, (record, parsed, symbols, chunks, technologies) in zip(included, products, strict=True):
        record.directory_id = _ensure_directories(repository, blob.relative_path, directories)
        file_by_path[blob.relative_path] = record
        parsed_by_path[blob.relative_path] = parsed
        symbols_by_file[blob.relative_path] = symbols
        draft.chunks.extend(chunks)
        draft.technologies.extend(technologies)

    draft.files = list(file_by_path.values())
    draft.directories = list(directories.values())
    for symbols in symbols_by_file.values():
        draft.symbols.extend(symbols)
    draft.edges = _edges(
        index_generation,
        file_by_path,
        symbols_by_file,
        parsed_by_path,
        draft.directories,
        on_progress,
    )
    return draft


def _file_products(repository: str, included: list[SourceBlob], fingerprint: str, on_progress, workers: int | None, cache):
    count = 1 if workers is None and len(included) < 32 else workers
    if count is None:
        count = min(4, os.cpu_count() or 2)
    count = max(1, count)
    if count == 1 or len(included) < 2:
        products = []
        renewed = time.monotonic()
        for blob in included:
            renewed = _renew_if_due(on_progress, renewed)
            products.append(_build_file(repository, blob.relative_path, blob.payload, blob.git_blob_id, fingerprint))
        return products
    products: list = [None] * len(included)
    missing: list[int] = []
    renewed = time.monotonic()
    for index, blob in enumerate(included):
        renewed = _renew_if_due(on_progress, renewed)
        loaded = None if cache is None else cache.load(blob.relative_path, blob.payload, fingerprint)
        if loaded is None:
            missing.append(index)
        else:
            products[index] = loaded
    if not missing:
        return products
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=count, mp_context=context) as pool:
        pending = {}
        cursor = 0
        renewed = time.monotonic()
        limit = count * 2
        while cursor < len(missing) or pending:
            while cursor < len(missing) and len(pending) < limit:
                index = missing[cursor]
                blob = included[index]
                future = pool.submit(
                    _build_file,
                    repository,
                    blob.relative_path,
                    blob.payload,
                    blob.git_blob_id,
                    fingerprint,
                )
                pending[future] = index
                cursor += 1
            if not pending:
                break
            done, _ = wait(pending, timeout=30, return_when=FIRST_COMPLETED)
            renewed = _renew_if_due(on_progress, renewed)
            for future in done:
                index = pending.pop(future)
                product = future.result()
                blob = included[index]
                if cache is not None:
                    cache.store(blob.relative_path, blob.payload, fingerprint, product)
                products[index] = product
        return products


def _renew_if_due(on_progress, renewed: float) -> float:
    if on_progress is None or time.monotonic() - renewed < 30:
        return renewed
    on_progress()
    return time.monotonic()


def _build_file(repository: str, path: str, payload: bytes, git_blob_id: str, fingerprint: str):
    digest = content_hash(payload)
    record = FileRecord(
        id=str(file_id(repository, path, digest)),
        directory_id="",
        logical_file_key=str(logical_file_key(repository, path)),
        relative_path=path,
        language=_language_name(path),
        extension=_extension(path),
        line_count=line_count(payload),
        byte_size=len(payload),
        content_hash=digest,
        git_blob_id=git_blob_id,
        parse_status="not_parsed",
        parse_message=None,
    )
    parsed = parse_file(path, payload)
    record.parse_status = parsed.parse_status
    record.parse_message = parsed.parse_message
    symbols = _symbol_records(repository, record, parsed, payload)
    chunks = _chunk_records(record, symbols, parsed, payload, fingerprint)
    technologies = _technologies(record, payload)
    return record, parsed, symbols, chunks, technologies


def _symbol_records(repository: str, file: FileRecord, parsed: ParsedFile, payload: bytes) -> list[SymbolRecord]:
    grouped: dict[tuple[str, str, str], list] = {}
    for symbol in parsed.symbols:
        grouped.setdefault((symbol.kind, symbol.qualified_name, symbol.signature), []).append(symbol)
    ordinals = {}
    for key, group in grouped.items():
        for ordinal, symbol in enumerate(sorted(group, key=lambda item: item.start_byte)):
            ordinals[id(symbol)] = ordinal
    records = []
    index_to_id: dict[int, str] = {}
    spans = line_spans(payload)
    for index, symbol in enumerate(parsed.symbols):
        ordinal = ordinals[id(symbol)]
        sid = str(
            symbol_id(
                file.id,
                PARSER_VERSION,
                symbol.kind,
                symbol.qualified_name,
                symbol.signature,
                ordinal,
            )
        )
        index_to_id[index] = sid
        start_line, end_line = line_labels(payload, symbol.start_byte, symbol.end_byte, spans)
        parent_id = None
        if symbol.parent_index is not None:
            parent_id = index_to_id[symbol.parent_index]
        records.append(
            SymbolRecord(
                id=sid,
                file_id=file.id,
                parent_symbol_id=parent_id,
                identity_key=str(
                    identity_key(
                        repository,
                        file.relative_path,
                        symbol.kind,
                        symbol.qualified_name,
                        symbol.signature,
                    )
                ),
                kind=symbol.kind,
                name=symbol.name,
                qualified_name=symbol.qualified_name,
                signature=symbol.signature,
                ordinal=ordinal,
                language=parsed.language or "",
                start_byte=symbol.start_byte,
                end_byte=symbol.end_byte,
                start_line=start_line,
                end_line=end_line,
                relative_path=file.relative_path,
            )
        )
    return records


def _chunk_records(
    file: FileRecord,
    symbols: list[SymbolRecord],
    parsed: ParsedFile,
    payload: bytes,
    fingerprint: str,
) -> list[ChunkRecord]:
    by_key = {(symbol.kind, symbol.qualified_name, symbol.signature, symbol.ordinal): symbol for symbol in symbols}
    symbol_spans = []
    for symbol in symbols:
        key = (symbol.kind, symbol.qualified_name, symbol.signature, symbol.ordinal)
        symbol_spans.append((key, symbol.kind, Span(symbol.start_byte, symbol.end_byte)))
    drafts = build_chunks(payload, symbol_spans, [Span(start, end) for start, end in parsed.import_spans])
    records = []
    spans = line_spans(payload)
    for draft in drafts:
        symbol = by_key.get(draft.symbol_ordinal_key) if draft.symbol_ordinal_key else None
        span = payload[draft.start_byte : draft.end_byte]
        digest = content_hash(span)
        prefix = ""
        if symbol is not None:
            prefix = context_prefix(symbol.qualified_name, symbol.signature)
        start_line, end_line = line_labels(payload, draft.start_byte, draft.end_byte, spans)
        records.append(
            ChunkRecord(
                id=str(
                    chunk_id(
                        draft.anchor,
                        symbol.id if symbol else None,
                        file.id,
                        PARSER_VERSION,
                        CHUNKING_VERSION,
                        draft.kind,
                        draft.ordinal,
                        digest,
                    )
                ),
                file_id=file.id,
                symbol_id=symbol.id if symbol else None,
                kind=draft.kind,
                ordinal=draft.ordinal,
                start_byte=draft.start_byte,
                end_byte=draft.end_byte,
                start_line=start_line,
                end_line=end_line,
                span_hash=digest,
                context_prefix=prefix,
                embedding_input_hash=embedding_input_hash(prefix, span),
                lexical_text=lexical_input(prefix, span).decode("utf-8", errors="replace"),
            )
        )
    return records


def _edges(
    generation: str,
    files: dict[str, FileRecord],
    symbols_by_file: dict[str, list[SymbolRecord]],
    parsed_by_path: dict[str, ParsedFile],
    directories: list[DirectoryRecord],
    on_progress=None,
) -> list[EdgeRecord]:
    edges: list[EdgeRecord] = []
    seen: set[tuple] = set()

    def add(relationship: str, source: str, target: str, mentioned_name: str = "", resolution: str = "") -> None:
        key = (relationship, source, target, mentioned_name, resolution)
        if key in seen:
            return
        seen.add(key)
        edges.append(
            EdgeRecord(
                relationship=relationship,
                source_id=source,
                target_id=target,
                mentioned_name=mentioned_name,
                resolution=resolution,
                edge_id=str(edge_id(relationship, generation, source, target, mentioned_name, resolution)),
            )
        )

    for directory in directories:
        add("CONTAINS", generation, directory.id)
        if directory.parent_directory_id:
            add("CONTAINS", directory.parent_directory_id, directory.id)
    for path, file in files.items():
        add("CONTAINS", generation, file.id)
        add("CONTAINS", file.directory_id, file.id)
        for symbol in symbols_by_file[path]:
            add("DECLARES", file.id, symbol.id)
            if symbol.parent_symbol_id:
                add("DECLARES", symbol.parent_symbol_id, symbol.id)

    paths = set(files)
    modules_by_id = {
        symbol.id: symbol.id
        for symbols in symbols_by_file.values()
        for symbol in symbols
        if symbol.kind == "module"
    }
    local_names = {path: _function_names(symbols) for path, symbols in symbols_by_file.items()}
    renewed = time.monotonic()
    for path, parsed in parsed_by_path.items():
        renewed = _renew_if_due(on_progress, renewed)
        file = files[path]
        module = next((symbol for symbol in symbols_by_file[path] if symbol.kind == "module"), None)
        imported_names = _imported_function_names(path, parsed, symbols_by_file, paths)
        for mention in parsed.mentions:
            if mention.relationship == "IMPORTS":
                candidate_paths = _import_targets(path, mention.mentioned_name, paths)
                candidates = _endpoint_ids(candidate_paths, files, symbols_by_file)
                _add_resolved(add, "IMPORTS", file.id, candidates, mention.mentioned_name, files, symbols_by_file, module, modules_by_id)
                continue
            if mention.source_symbol_index is None:
                source = module.id if module is not None else None
            else:
                source = symbols_by_file[path][mention.source_symbol_index].id
            if source is None:
                continue
            candidates = _call_candidates(path, mention, symbols_by_file, local_names, imported_names)
            _add_resolved(
                add,
                mention.relationship,
                source,
                candidates,
                mention.mentioned_name,
                files,
                symbols_by_file,
                module,
                modules_by_id,
                unresolved_target=file.id,
            )
    return edges


def _add_resolved(add, relationship, source, candidates, mentioned_name, files, symbols_by_file, module, modules_by_id, unresolved_target=None):
    if not candidates:
        target = unresolved_target if unresolved_target is not None else source
        add(relationship, source, target, mentioned_name, "unresolved")
        return
    resolution = "resolved" if len(candidates) == 1 else "ambiguous"
    for candidate in candidates:
        add(relationship, source, candidate, mentioned_name, resolution)
        if relationship == "IMPORTS" and resolution == "resolved" and module is not None:
            target_module = modules_by_id.get(candidate)
            if target_module is not None:
                add("DEPENDS_ON", module.id, target_module, "", "")


def _endpoint_ids(candidate_paths: list[str], files: dict[str, FileRecord], symbols_by_file: dict[str, list[SymbolRecord]]) -> list[str]:
    endpoints = []
    for candidate in candidate_paths:
        module = next((symbol for symbol in symbols_by_file[candidate] if symbol.kind == "module"), None)
        endpoints.append(module.id if module is not None else files[candidate].id)
    return endpoints


def _import_targets(source_path: str, specifier: str, paths: set[str]) -> list[str]:
    if source_path.endswith(".py"):
        return _python_targets(source_path, specifier, paths)
    if specifier.startswith("./") or specifier.startswith("../"):
        return _relative_targets(source_path, specifier, paths)
    return []


def _python_targets(source_path: str, specifier: str, paths: set[str]) -> list[str]:
    if specifier.startswith("."):
        dots = len(specifier) - len(specifier.lstrip("."))
        rest = specifier[dots:]
        parts = source_path.split("/")[:-1]
        up = dots - 1
        if up > len(parts):
            return []
        base = parts[: len(parts) - up]
        if rest:
            base.extend(part for part in rest.split(".") if part)
        return _path_candidates("/".join(base), paths, (".py",))
    return _path_candidates(specifier.replace(".", "/"), paths, (".py",))


def _relative_targets(source_path: str, specifier: str, paths: set[str]) -> list[str]:
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), specifier))
    if joined in paths:
        return [joined]
    return _path_candidates(joined, paths, (".js", ".jsx", ".ts", ".tsx"))


def _path_candidates(stem: str, paths: set[str], extensions: tuple[str, ...]) -> list[str]:
    found = []
    for extension in extensions:
        candidate = f"{stem}{extension}"
        if candidate in paths:
            found.append(candidate)
        init = f"{stem}/__init__{extension}"
        if init in paths:
            found.append(init)
    return found


def _call_candidates(path, mention, symbols_by_file, local_names, imported_names) -> list[str]:
    mentioned = mention.mentioned_name
    candidates = local_names[path].get(mentioned) or imported_names.get(mentioned) or []
    if candidates or "." not in mentioned:
        return candidates
    receiver, leaf = mentioned.split(".", 1)
    if receiver not in {"self", "cls"} or not leaf or "." in leaf:
        return []
    local = local_names[path].get(leaf) or []
    if mention.source_symbol_index is None:
        return local
    source = symbols_by_file[path][mention.source_symbol_index]
    owner = source.qualified_name.rsplit(".", 1)[0]
    by_id = {symbol.id: symbol for symbol in symbols_by_file[path]}
    same_class = [symbol_id for symbol_id in local if by_id[symbol_id].qualified_name == f"{owner}.{leaf}"]
    return same_class or local


def _function_names(symbols: list[SymbolRecord]) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for symbol in symbols:
        if symbol.kind not in {"function", "method"}:
            continue
        keys = [symbol.name]
        if symbol.qualified_name != symbol.name:
            keys.append(symbol.qualified_name)
        for key in keys:
            names.setdefault(key, []).append(symbol.id)
    return names


def _imported_function_names(path: str, parsed: ParsedFile, symbols_by_file: dict[str, list[SymbolRecord]], paths: set[str]) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for mention in parsed.mentions:
        if mention.relationship != "IMPORTS":
            continue
        targets = _import_targets(path, mention.mentioned_name, paths)
        if len(targets) != 1:
            continue
        for symbol in symbols_by_file[targets[0]]:
            if symbol.kind in {"function", "method"}:
                names.setdefault(symbol.name, []).append(symbol.id)
    return names


def _ensure_directories(repository: str, relative_path: str, directories: dict[str, DirectoryRecord]) -> str:
    parts = relative_path.split("/")[:-1]
    parent = None
    walked = []
    for part in parts:
        walked.append(part)
        path = "/".join(walked)
        if path not in directories:
            directories[path] = DirectoryRecord(
                id=str(directory_id(repository, path)),
                parent_directory_id=parent,
                relative_path=path,
                name=part,
                depth=len(walked),
            )
        parent = directories[path].id
    if parent is None:
        root_key = ""
        if root_key not in directories:
            directories[root_key] = DirectoryRecord(
                id=str(directory_id(repository, "")),
                parent_directory_id=None,
                relative_path="",
                name="",
                depth=0,
            )
        return directories[root_key].id
    return parent


def _technologies(file: FileRecord, payload: bytes) -> list[TechnologyRecord]:
    from app.ingestion.technologies import technologies_for_manifest

    return technologies_for_manifest(file.relative_path, payload, file.id)


def _language_name(relative_path: str) -> str | None:
    extension = _extension(relative_path)
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".md": "markdown",
        ".json": "json",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".toml": "toml",
        ".sql": "sql",
        ".sh": "shell",
    }.get(extension or "")


def _extension(relative_path: str) -> str | None:
    name = relative_path.rsplit("/", 1)[-1]
    if name in {"Dockerfile", "requirements.txt"}:
        return None
    dot = name.rfind(".")
    if dot == -1:
        return None
    return name[dot:].lower()


# Imported so callers can see the versions used by this builder.
INDEX_VERSIONS = {
    "parser_version": PARSER_VERSION,
    "chunking_version": CHUNKING_VERSION,
    "lexical_index_version": LEXICAL_INDEX_VERSION,
    "inclusion_rules_version": INCLUSION_RULES_VERSION,
    "embedding_input_version": EMBEDDING_INPUT_VERSION,
}
