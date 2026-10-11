"""Turn included blob bytes into ids, chunks, and generation-scoped edges."""

from __future__ import annotations

import posixpath
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
) -> GenerationDraft:
    included = [blob for blob in blobs if is_included(blob.relative_path, blob.mode)]
    included.sort(key=lambda blob: blob.relative_path.encode("utf-8"))
    draft = GenerationDraft()
    parsed_by_path: dict[str, ParsedFile] = {}
    symbols_by_file: dict[str, list[SymbolRecord]] = {}
    file_by_path: dict[str, FileRecord] = {}
    directories: dict[str, DirectoryRecord] = {}

    for blob in included:
        digest = content_hash(blob.payload)
        repo_uuid = repository
        path = blob.relative_path
        directory = _ensure_directories(repo_uuid, path, directories)
        record = FileRecord(
            id=str(file_id(repo_uuid, path, digest)),
            directory_id=directory,
            logical_file_key=str(logical_file_key(repo_uuid, path)),
            relative_path=path,
            language=_language_name(path),
            extension=_extension(path),
            line_count=line_count(blob.payload),
            byte_size=len(blob.payload),
            content_hash=digest,
            git_blob_id=blob.git_blob_id,
            parse_status="not_parsed",
            parse_message=None,
        )
        parsed = parse_file(path, blob.payload)
        record.parse_status = parsed.parse_status
        record.parse_message = parsed.parse_message
        file_by_path[path] = record
        parsed_by_path[path] = parsed
        symbols_by_file[path] = _symbol_records(repo_uuid, record, parsed, blob.payload)
        draft.chunks.extend(
            _chunk_records(record, symbols_by_file[path], parsed, blob.payload, embedding_fingerprint)
        )
        draft.technologies.extend(_technologies(record, blob.payload))

    draft.files = list(file_by_path.values())
    draft.directories = list(directories.values())
    for symbols in symbols_by_file.values():
        draft.symbols.extend(symbols)
    draft.edges = _edges(index_generation, file_by_path, symbols_by_file, parsed_by_path, draft.directories)
    return draft


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
        start_line, end_line = line_labels(payload, symbol.start_byte, symbol.end_byte)
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
    for draft in drafts:
        symbol = by_key.get(draft.symbol_ordinal_key) if draft.symbol_ordinal_key else None
        span = payload[draft.start_byte : draft.end_byte]
        digest = content_hash(span)
        prefix = ""
        if symbol is not None:
            prefix = context_prefix(symbol.qualified_name, symbol.signature)
        start_line, end_line = line_labels(payload, draft.start_byte, draft.end_byte)
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
    for path, parsed in parsed_by_path.items():
        file = files[path]
        module = next((symbol for symbol in symbols_by_file[path] if symbol.kind == "module"), None)
        for mention in parsed.mentions:
            if mention.relationship == "IMPORTS":
                candidate_paths = _import_targets(path, mention.mentioned_name, paths)
                candidates = _endpoint_ids(candidate_paths, files, symbols_by_file)
                _add_resolved(add, "IMPORTS", file.id, candidates, mention.mentioned_name, files, symbols_by_file, module)
                continue
            if mention.source_symbol_index is None:
                source = module.id if module is not None else None
            else:
                source = symbols_by_file[path][mention.source_symbol_index].id
            if source is None:
                continue
            candidates = _symbol_targets(path, mention.mentioned_name, symbols_by_file, parsed_by_path, files)
            _add_resolved(
                add,
                mention.relationship,
                source,
                candidates,
                mention.mentioned_name,
                files,
                symbols_by_file,
                module,
                unresolved_target=file.id,
            )
    return edges


def _add_resolved(add, relationship, source, candidates, mentioned_name, files, symbols_by_file, module, unresolved_target=None):
    if not candidates:
        target = unresolved_target if unresolved_target is not None else source
        add(relationship, source, target, mentioned_name, "unresolved")
        return
    resolution = "resolved" if len(candidates) == 1 else "ambiguous"
    for candidate in candidates:
        add(relationship, source, candidate, mentioned_name, resolution)
        if relationship == "IMPORTS" and resolution == "resolved" and module is not None:
            target_module = _module_for_target(candidate, symbols_by_file)
            if target_module is not None:
                add("DEPENDS_ON", module.id, target_module, "", "")


def _endpoint_ids(candidate_paths: list[str], files: dict[str, FileRecord], symbols_by_file: dict[str, list[SymbolRecord]]) -> list[str]:
    endpoints = []
    for candidate in candidate_paths:
        module = next((symbol for symbol in symbols_by_file[candidate] if symbol.kind == "module"), None)
        endpoints.append(module.id if module is not None else files[candidate].id)
    return endpoints


def _module_for_target(target_id: str, symbols_by_file: dict[str, list[SymbolRecord]]) -> str | None:
    for symbols in symbols_by_file.values():
        for symbol in symbols:
            if symbol.id == target_id and symbol.kind == "module":
                return symbol.id
    return None


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


def _symbol_targets(path, mentioned_name, symbols_by_file, parsed_by_path, files) -> list[str]:
    matches = []
    for symbol in symbols_by_file[path]:
        if symbol.kind in {"function", "method"} and mentioned_name in {symbol.name, symbol.qualified_name}:
            matches.append(symbol.id)
    if matches:
        return matches
    imported = []
    for mention in parsed_by_path[path].mentions:
        if mention.relationship != "IMPORTS":
            continue
        targets = _import_targets(path, mention.mentioned_name, set(files))
        if len(targets) != 1:
            continue
        for symbol in symbols_by_file[targets[0]]:
            if symbol.kind in {"function", "method"} and symbol.name == mentioned_name:
                imported.append(symbol.id)
    return imported


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
    name = file.relative_path.rsplit("/", 1)[-1]
    text = payload.decode("utf-8", errors="replace")
    found: list[TechnologyRecord] = []
    if name == "requirements.txt":
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            package = stripped.split("==")[0].split(">=")[0].split("[")[0].strip()
            if package:
                found.append(TechnologyRecord(package, file.id, stripped))
    elif name == "package.json":
        for line in text.splitlines():
            stripped = line.strip().strip(",")
            if stripped.startswith('"') and ":" in stripped:
                package = stripped.split('"', 2)[1]
                if package not in {"name", "version", "dependencies", "devDependencies"}:
                    found.append(TechnologyRecord(package, file.id, stripped))
    elif name == "pyproject.toml":
        for line in text.splitlines():
            stripped = line.strip().strip(",").strip('"')
            if "==" in stripped or stripped.startswith("dependencies"):
                found.append(TechnologyRecord(stripped.split("==")[0].strip(" '\""), file.id, line.strip()))
    return [item for item in found if item.name]


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
