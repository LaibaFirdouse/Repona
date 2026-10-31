"""Turn included blob bytes into ids, chunks, and generation-scoped edges."""

from __future__ import annotations

import multiprocessing
import os
import posixpath
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field

from app.ingestion.chunking import Span, build_chunks
from app.ingestion.ir import IrDocument, ir_to_parsed_file, parsed_file_to_ir
from app.ingestion.inclusion import INCLUDED_FILENAMES, LANGUAGE_BY_EXTENSION, is_included
from app.ingestion.parse import ParsedFile, parse_file
from app.ingestion.project_context import ProjectContext, discover_project_context
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
    resolution_method: str = ""
    strategy: str = ""
    start_byte: int | None = None
    end_byte: int | None = None
    evidence: str = ""


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
    deferred_chunks: list[ChunkRecord] = field(default_factory=list)
    edges: list[EdgeRecord] = field(default_factory=list)
    technologies: list[TechnologyRecord] = field(default_factory=list)
    project_context: ProjectContext | None = None
    capability_report: dict | None = None


PROGRESSIVE_DEFER_KINDS = frozenset({"symbol_body", "symbol_part"})


def assemble_generation(
    repository: str,
    commit_sha: str,
    blobs: list[SourceBlob],
    embedding_fingerprint: str,
    index_generation: str,
    on_progress=None,
    workers: int | None = None,
    cache=None,
    progressive: bool = False,
) -> GenerationDraft:
    included = [blob for blob in blobs if is_included(blob.relative_path, blob.mode)]
    included.sort(key=lambda blob: blob.relative_path.encode("utf-8"))
    draft = GenerationDraft()
    ir_by_path: dict[str, IrDocument] = {}
    symbols_by_file: dict[str, list[SymbolRecord]] = {}
    file_by_path: dict[str, FileRecord] = {}
    directories: dict[str, DirectoryRecord] = {}
    products = _file_products(repository, included, embedding_fingerprint, on_progress, workers, cache)

    for blob, (record, document, symbols, chunks, technologies) in zip(included, products, strict=True):
        record.directory_id = _ensure_directories(repository, blob.relative_path, directories)
        file_by_path[blob.relative_path] = record
        ir_by_path[blob.relative_path] = document
        symbols_by_file[blob.relative_path] = symbols
        draft.chunks.extend(chunks)
        draft.technologies.extend(technologies)

    draft.files = list(file_by_path.values())
    draft.directories = list(directories.values())
    for symbols in symbols_by_file.values():
        draft.symbols.extend(symbols)
    blobs_by_path = {blob.relative_path: blob.payload for blob in included}
    draft.project_context = discover_project_context(list(file_by_path), blobs_by_path)
    alias_rules = draft.project_context.alias_rule_tuples() or _load_js_path_alias_rules(
        file_by_path, blobs_by_path
    )
    draft.edges = _edges(
        index_generation,
        file_by_path,
        symbols_by_file,
        ir_by_path,
        draft.directories,
        on_progress,
        alias_rules=alias_rules,
        project_context=draft.project_context,
        blobs_by_path=blobs_by_path,
    )
    from app.ingestion.capabilities import build_capability_report

    draft.capability_report = build_capability_report(
        repository_id=repository,
        generation_id=index_generation,
        relative_paths=list(file_by_path),
        parse_status_by_path={path: file.parse_status for path, file in file_by_path.items()},
        languages_by_path={path: file.language for path, file in file_by_path.items()},
        edges=draft.edges,
        blobs_by_path=blobs_by_path,
        context=draft.project_context,
        files=draft.files,
        symbols=draft.symbols,
    ).as_dict()
    if progressive and draft.chunks:
        deferred = [chunk for chunk in draft.chunks if chunk.kind in PROGRESSIVE_DEFER_KINDS]
        draft.chunks = [chunk for chunk in draft.chunks if chunk.kind not in PROGRESSIVE_DEFER_KINDS]
        draft.deferred_chunks = deferred
    return draft



def _normalize_cached_file_product(path: str, product):
    """Upgrade old ParsedFile cache products to the IR representation.

    Parser semantics and stable symbol identities are unchanged, so the
    parser version does not need to be bumped.
    """
    if not isinstance(product, tuple) or len(product) != 5:
        return None

    record, parsed_or_document, symbols, chunks, technologies = product

    if isinstance(parsed_or_document, IrDocument):
        return product

    if isinstance(parsed_or_document, ParsedFile):
        document = parsed_file_to_ir(
            path,
            parsed_or_document,
            adapter_id="legacy_parse_cache",
        )
        return record, document, symbols, chunks, technologies

    return None



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
            normalized = _normalize_cached_file_product(
                blob.relative_path, loaded
            )
            if normalized is None:
                # Invalid or obsolete cache entry: rebuild this file.
                missing.append(index)
            else:
                products[index] = normalized
                if normalized is not loaded and cache is not None:
                    # Replace the legacy cache entry with the normalized IR.
                    cache.store(
                        blob.relative_path,
                        blob.payload,
                        fingerprint,
                        normalized,
                    )
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




def _parse_with_language_adapter(path: str, payload: bytes) -> IrDocument:
    """Return the normalized IR document produced by the selected adapter."""
    from app.ingestion.adapters import builtin as _builtin  # noqa: F401
    from app.ingestion.adapters.language import language_registry

    adapter = language_registry.for_path(path, _language_name(path))

    if adapter is None:
        parsed = parse_file(path, payload)
        return parsed_file_to_ir(
            path, parsed, adapter_id="legacy_fallback"
        )

    parse_ir = getattr(adapter, "parse_ir", None)

    if callable(parse_ir):
        document = parse_ir(path, payload)
        if not isinstance(document, IrDocument):
            raise TypeError(
                f"Adapter {adapter.id} returned an invalid IR document "
                f"for {path}"
            )
        return document

    # Compatibility for existing third-party adapters.
    parsed = adapter.parse(path, payload)
    return parsed_file_to_ir(
        path,
        parsed,
        adapter_id=getattr(adapter, "id", "legacy_adapter"),
    )



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
    document = _parse_with_language_adapter(path, payload)

    record.parse_status = document.parse_status
    record.parse_message = document.parse_message

    # Temporary compatibility projection for existing indexing functions.
    parsed = ir_to_parsed_file(document)

    symbols = _symbol_records(repository, record, parsed, payload)
    chunks = _chunk_records(record, symbols, parsed, payload, fingerprint)
    technologies = _technologies(record, payload)

    return record, document, symbols, chunks, technologies


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
    *,
    only_symbol_id: str | None = None,
    chunk_kinds: frozenset[str] | None = None,
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
        if chunk_kinds is not None and draft.kind not in chunk_kinds:
            continue
        symbol = by_key.get(draft.symbol_ordinal_key) if draft.symbol_ordinal_key else None
        if only_symbol_id is not None and (symbol is None or symbol.id != only_symbol_id):
            continue
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
    ir_by_path: dict[str, IrDocument],
    directories: list[DirectoryRecord],
    on_progress=None,
    alias_rules: list[tuple[str, list[str]]] | None = None,
    project_context=None,
    blobs_by_path: dict[str, bytes] | None = None,
) -> list[EdgeRecord]:
    from app.ingestion.relationship_resolve import (
        ResolutionIndex,
        build_import_scope,
        resolve_call,
        resolve_import_targets,
    )

    edges: list[EdgeRecord] = []
    seen: set[tuple] = set()

    def add(
        relationship: str,
        source: str,
        target: str,
        mentioned_name: str = "",
        resolution: str = "",
        *,
        resolution_method: str = "",
        strategy: str = "",
        start_byte: int | None = None,
        end_byte: int | None = None,
        evidence: str = "",
    ) -> None:
        key = (relationship, source, target, mentioned_name, resolution, strategy)
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
                resolution_method=resolution_method,
                strategy=strategy,
                start_byte=start_byte,
                end_byte=end_byte,
                evidence=evidence,
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
    go_module = ""
    if project_context is not None:
        for name in getattr(project_context, "package_names", []) or []:
            if "/" in name or name.startswith("example.com") or "." in name:
                go_module = name
                break
        if not go_module:
            for name in getattr(project_context, "package_names", []) or []:
                go_module = name
                break

    index = ResolutionIndex(
        paths=paths,
        symbols_by_file=symbols_by_file,
        local_names=local_names,
        alias_rules=alias_rules or [],
        go_module=go_module,
        package_names=list(getattr(project_context, "package_names", []) or []) if project_context else [],
        import_targets_fn=_import_targets,
    )

    renewed = time.monotonic()
    for path, document in ir_by_path.items():
        renewed = _renew_if_due(on_progress, renewed)
        file = files[path]
        module = next(
            (
                symbol
                for symbol in symbols_by_file[path]
                if symbol.kind == "module"
            ),
            None,
        )

        # Resolve IR entity IDs to the existing stable graph symbol IDs.
        # These stable IDs are still generated by _symbol_records().
        entity_by_id = {
            entity.id: entity
            for entity in document.entities
        }

        import_scope = build_import_scope(path, document, index)

        for relationship in document.relationships:
            source_range = relationship.source_range
            start_byte = (
                source_range.start_byte if source_range else None
            )
            end_byte = (
                source_range.end_byte if source_range else None
            )

            if relationship.kind == "IMPORTS":
                candidate_paths = _import_targets(
                    path,
                    relationship.mentioned_name,
                    paths,
                    alias_rules=alias_rules,
                    go_module=go_module,
                )

                if len(candidate_paths) > 1:
                    candidates = []
                else:
                    candidates = _endpoint_ids(
                        candidate_paths,
                        files,
                        symbols_by_file,
                    )

                resolved = resolve_import_targets(
                    source_path=path,
                    specifier=relationship.mentioned_name,
                    candidate_ids=candidates,
                )

                target = resolved.target_id or file.id

                add(
                    "IMPORTS",
                    file.id,
                    target,
                    relationship.mentioned_name,
                    resolved.resolution,
                    resolution_method=resolved.method,
                    strategy=resolved.strategy,
                    start_byte=start_byte,
                    end_byte=end_byte,
                    evidence=resolved.evidence,
                )

                if (
                    resolved.resolution == "resolved"
                    and module is not None
                    and resolved.target_id
                ):
                    target_module = modules_by_id.get(
                        resolved.target_id
                    )
                    if target_module is not None:
                        add(
                            "DEPENDS_ON",
                            module.id,
                            target_module,
                            "",
                            "resolved",
                            resolution_method="static",
                            strategy="import_module",
                        )

                continue

            if relationship.kind not in {"CALLS", "REFERENCES"}:
                continue

            source_entity = entity_by_id.get(relationship.source_id)
            if source_entity is None:
                continue

            legacy_index = source_entity.attributes.get(
                "legacy_symbol_index"
            )

            if legacy_index is None:
                source = module.id if module is not None else None
            else:
                source = symbols_by_file[path][legacy_index].id

            if source is None:
                continue

            resolved = resolve_call(
                path=path,
                mention=relationship,
                symbols_by_file=symbols_by_file,
                local_names=local_names[path],
                import_scope=import_scope,
            )

            target = resolved.target_id or file.id

            add(
                relationship.kind,
                source,
                target,
                relationship.mentioned_name,
                resolved.resolution,
                resolution_method=resolved.method,
                strategy=resolved.strategy,
                start_byte=start_byte,
                end_byte=end_byte,
                evidence=resolved.evidence,
            )

    _add_protocol_route_edges(
        add,
        files=files,
        symbols_by_file=symbols_by_file,
        blobs_by_path=blobs_by_path or {},
        project_context=project_context,
    )
    return edges


def _add_protocol_route_edges(add, *, files, symbols_by_file, blobs_by_path, project_context) -> None:
    """Emit ROUTES_TO edges for HTTP protocol correspondence (never CALLS)."""
    if not blobs_by_path:
        return
    from app.ingestion.adapters import builtin as _builtin  # noqa: F401
    from app.ingestion.adapters.framework import framework_registry
    from app.ingestion.protocol_links import extract_client_http_paths, link_http_routes
    from app.ingestion.project_context import discover_project_context

    relative_paths = list(files)
    context = project_context or discover_project_context(relative_paths, blobs_by_path)
    texts = {
        path: payload.decode("utf-8", errors="replace")
        for path, payload in blobs_by_path.items()
        if path in files
    }
    server_routes = []
    for adapter in framework_registry.all():
        capability = adapter.detect(context, relative_paths)
        if not capability.detected or not capability.route_discovery:
            continue
        server_routes.extend(adapter.discover_routes(context, relative_paths=relative_paths, file_texts=texts))
    if not server_routes:
        return
    client_hits: list[tuple[str, str, str]] = []
    for path, text in texts.items():
        for method, route_path in extract_client_http_paths(path, text):
            client_hits.append((method, route_path, path))
    for link in link_http_routes(client_hits, server_routes):
        client_file = files.get(link.client_path)
        server_file = files.get(link.server_path)
        if client_file is None or server_file is None:
            continue
        client_module = next((s for s in symbols_by_file.get(link.client_path, []) if s.kind == "module"), None)
        server_module = next((s for s in symbols_by_file.get(link.server_path, []) if s.kind == "module"), None)
        source = client_module.id if client_module is not None else client_file.id
        target = server_module.id if server_module is not None else server_file.id
        add(
            "ROUTES_TO",
            source,
            target,
            f"{link.method} {link.path}",
            "protocol",
            resolution_method="protocol",
            strategy="http_endpoint",
            evidence=link.evidence,
        )


def _endpoint_ids(candidate_paths: list[str], files: dict[str, FileRecord], symbols_by_file: dict[str, list[SymbolRecord]]) -> list[str]:
    endpoints = []
    for candidate in candidate_paths:
        module = next((symbol for symbol in symbols_by_file[candidate] if symbol.kind == "module"), None)
        endpoints.append(module.id if module is not None else files[candidate].id)
    return endpoints


_JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx")
# Conservative defaults when tsconfig/jsconfig is absent (Next.js/Vite conventions).
_JS_ALIAS_PREFIXES = ("@/", "~/", "#/")


def _import_targets(
    source_path: str,
    specifier: str,
    paths: set[str],
    alias_rules: list[tuple[str, list[str]]] | None = None,
    go_module: str = "",
) -> list[str]:
    if source_path.endswith(".py"):
        return _python_targets(source_path, specifier, paths)
    if source_path.endswith(".go"):
        from app.ingestion.relationship_resolve import go_import_targets

        return go_import_targets(specifier, paths, go_module)
    if specifier.startswith("./") or specifier.startswith("../"):
        return _relative_targets(source_path, specifier, paths)
    alias = _js_alias_targets(specifier, paths, alias_rules=alias_rules)
    if alias:
        return alias
    return []


def _load_js_path_alias_rules(files: dict[str, FileRecord], blobs_by_path: dict[str, bytes] | None = None) -> list[tuple[str, list[str]]]:
    """Parse tsconfig/jsconfig compilerOptions.paths when those files are in the generation."""
    rules: list[tuple[str, list[str]]] = []
    for name in ("tsconfig.json", "jsconfig.json"):
        payload = None
        if blobs_by_path and name in blobs_by_path:
            payload = blobs_by_path[name]
        elif name in files:
            # Config may be indexed without retaining payload here; skip until blobs provided.
            continue
        if payload is None:
            continue
        rules.extend(_parse_tsconfig_paths(payload))
    return rules


def _parse_tsconfig_paths(payload: bytes) -> list[tuple[str, list[str]]]:
    import json
    import re

    try:
        text = payload.decode("utf-8", errors="replace")
        # Strip // comments that often appear in tsconfig.
        text = re.sub(r"//.*?$", "", text, flags=re.M)
        data = json.loads(text)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return []
    options = data.get("compilerOptions") if isinstance(data, dict) else None
    if not isinstance(options, dict):
        return []
    base_url = str(options.get("baseUrl") or ".").strip().replace("\\", "/").strip("./")
    paths_map = options.get("paths")
    if not isinstance(paths_map, dict):
        return []
    rules: list[tuple[str, list[str]]] = []
    for pattern, targets in paths_map.items():
        if not isinstance(pattern, str) or not isinstance(targets, list):
            continue
        mapped: list[str] = []
        for target in targets:
            if not isinstance(target, str):
                continue
            stem = target.replace("\\", "/").replace("*", "").strip("./")
            if base_url and base_url not in {".", ""}:
                stem = f"{base_url}/{stem}".replace("//", "/").strip("./")
            if stem:
                mapped.append(stem)
        if mapped:
            rules.append((pattern.replace("\\", "/"), mapped))
    return rules


def _js_alias_targets(
    specifier: str,
    paths: set[str],
    alias_rules: list[tuple[str, list[str]]] | None = None,
) -> list[str]:
    """Resolve path aliases using tsconfig rules when present; else conservative defaults."""
    found: list[str] = []
    if alias_rules:
        for pattern, targets in alias_rules:
            matched = _match_ts_path_pattern(pattern, specifier)
            if matched is None:
                continue
            for target_root in targets:
                root = target_root.rstrip("/")
                if matched:
                    stem = f"{root}/{matched.lstrip('/')}".replace("//", "/")
                else:
                    stem = root
                found.extend(_path_candidates(stem.strip("/"), paths, _JS_EXTENSIONS))
            # Prefer a unique resolution; keep ambiguity as multiple candidates for the caller.
            break
        if found:
            return _unique_preserve(found)
        return []

    stem = None
    for prefix in _JS_ALIAS_PREFIXES:
        if specifier.startswith(prefix):
            stem = specifier[len(prefix) :].lstrip("/")
            break
    if not stem:
        return []
    found = _path_candidates(stem, paths, _JS_EXTENSIONS)
    if found:
        return _unique_preserve(found)
    # Conservative catalogue fallbacks when config is missing.
    for root in ("src", "app", "lib"):
        rooted = _path_candidates(f"{root}/{stem}", paths, _JS_EXTENSIONS)
        if rooted:
            return _unique_preserve(rooted)
    return []


def _match_ts_path_pattern(pattern: str, specifier: str) -> str | None:
    """Return the wildcard capture for a tsconfig paths pattern, or '' for exact match."""
    if "*" in pattern:
        prefix, _, suffix = pattern.partition("*")
        if not specifier.startswith(prefix):
            return None
        rest = specifier[len(prefix) :]
        if suffix:
            if not rest.endswith(suffix):
                return None
            rest = rest[: -len(suffix)]
        return rest
    if pattern == specifier:
        return ""
    return None


def _unique_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


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
    return _path_candidates(joined, paths, _JS_EXTENSIONS)


def _path_candidates(stem: str, paths: set[str], extensions: tuple[str, ...]) -> list[str]:
    found = []
    for extension in extensions:
        candidate = f"{stem}{extension}"
        if candidate in paths:
            found.append(candidate)
        init = f"{stem}/__init__{extension}"
        if init in paths:
            found.append(init)
        # JS/TS package entrypoints.
        index = f"{stem}/index{extension}"
        if index in paths:
            found.append(index)
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
    if extension is None:
        return None
    return LANGUAGE_BY_EXTENSION.get(extension)


def _extension(relative_path: str) -> str | None:
    name = relative_path.rsplit("/", 1)[-1]
    if name in INCLUDED_FILENAMES:
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
