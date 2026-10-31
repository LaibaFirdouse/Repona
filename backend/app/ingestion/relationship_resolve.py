"""Language-independent relationship resolution coordinating adapter-aware strategies.

Optimises for verified accuracy, not edge count. Ambiguous and external targets stay
explicitly unresolved (or marked external) — never promoted to verified CALLS.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable

from app.ingestion.ir import IrDocument, IrRelationship, ResolutionMethod
from app.ingestion.parse import ImportBinding, ParsedFile, RawMention

# Names too common for unique-candidate / global resolution.
_AMBIGUOUS_CALL_NAMES = frozenset(
    {
        "create",
        "run",
        "execute",
        "handle",
        "call",
        "get",
        "set",
        "update",
        "delete",
        "init",
        "main",
        "process",
        "start",
        "stop",
        "open",
        "close",
        "read",
        "write",
        "load",
        "save",
        "send",
        "recv",
        "receive",
        "apply",
        "build",
        "make",
        "new",
        "parse",
        "format",
        "error",
        "log",
        "info",
        "debug",
        "print",
        "len",
        "str",
        "int",
        "list",
        "dict",
        "type",
        "id",
        "value",
        "data",
        "result",
        "item",
        "items",
        "keys",
        "values",
        "append",
        "push",
        "pop",
        "map",
        "filter",
        "reduce",
        "then",
        "catch",
        "json",
        "encode",
        "decode",
    }
)

_EXTERNAL_RECEIVERS = frozenset(
    {
        "console",
        "window",
        "document",
        "process",
        "Math",
        "JSON",
        "Object",
        "Array",
        "Promise",
        "Error",
        "Date",
        "Map",
        "Set",
        "Reflect",
        "React",
        "os",
        "sys",
        "re",
        "json",
        "http",
        "fmt",
        "log",
        "context",
        "time",
        "strings",
        "strconv",
        "errors",
        "io",
        "path",
        "filepath",
        "bytes",
        "sync",
        "atomic",
        "sql",
        "np",
        "pd",
        "torch",
        "tf",
        "plt",
        "requests",
        "httpx",
        "axios",
        "uuid",
        "datetime",
        "typing",
        "collections",
        "functools",
        "itertools",
        "asyncio",
        "logging",
        "NextResponse",
        "Response",
        "Request",
        # Python / JS builtins commonly appearing as call expressions.
        "abs",
        "hash",
        "round",
        "min",
        "max",
        "sum",
        "sorted",
        "enumerate",
        "zip",
        "range",
        "isinstance",
        "issubclass",
        "getattr",
        "setattr",
        "hasattr",
        "super",
        "property",
        "classmethod",
        "staticmethod",
        "fetch",
        "parseInt",
        "parseFloat",
        "encodeURIComponent",
        "decodeURIComponent",
    }
)


@dataclass
class ImportScope:
    """Local name → symbol ids, plus namespace alias → candidate module file paths."""

    names: dict[str, list[str]] = field(default_factory=dict)
    namespaces: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ResolvedTarget:
    target_id: str | None
    resolution: str  # resolved | unresolved | ambiguous | external | protocol
    method: str  # ResolutionMethod value
    strategy: str
    evidence: str = ""


@dataclass
class RelationshipMetrics:
    total_sites: int = 0
    verified_resolved: int = 0
    ambiguous: int = 0
    external: int = 0
    unresolved: int = 0
    protocol: int = 0

    def as_dict(self) -> dict:
        return {
            "total_sites": self.total_sites,
            "verified_resolved": self.verified_resolved,
            "ambiguous": self.ambiguous,
            "external": self.external,
            "unresolved": self.unresolved,
            "protocol": self.protocol,
        }

    def observe(self, resolution: str) -> None:
        self.total_sites += 1
        if resolution == "resolved":
            self.verified_resolved += 1
        elif resolution == "ambiguous":
            self.ambiguous += 1
        elif resolution == "external":
            self.external += 1
        elif resolution == "protocol":
            self.protocol += 1
        else:
            self.unresolved += 1


@dataclass
class ResolutionIndex:
    """Per-generation lookup structures for deterministic resolution."""

    paths: set[str]
    symbols_by_file: dict
    local_names: dict[str, dict[str, list[str]]]
    alias_rules: list[tuple[str, list[str]]] = field(default_factory=list)
    go_module: str = ""
    package_names: list[str] = field(default_factory=list)
    import_targets_fn: Callable | None = None


def build_import_scope(
    path: str,
    parsed: ParsedFile | IrDocument,
    index: ResolutionIndex,
    bindings: list | None = None,
) -> ImportScope:
    """Map local call names → candidate symbol ids from unique import targets."""
    scope = ImportScope()
    bindings = bindings if bindings is not None else list(getattr(parsed, "import_bindings", []) or [])
    resolve = index.import_targets_fn
    if resolve is None:
        return scope

    def resolve_targets(specifier: str) -> list[str]:
        try:
            return list(
                resolve(
                    path,
                    specifier,
                    index.paths,
                    alias_rules=index.alias_rules,
                    go_module=index.go_module,
                )
                or []
            )
        except TypeError:
            # Older callables may omit go_module.
            return list(resolve(path, specifier, index.paths, alias_rules=index.alias_rules) or [])

    named_from_bindings: dict[str, tuple[str, str]] = {}
    for binding in bindings:
        targets = resolve_targets(binding.module_specifier)
        if binding.kind == "namespace":
            # Package-scoped namespaces may span multiple source files (Go packages).
            if targets:
                scope.namespaces[binding.local_name] = list(targets)
            continue
        if len(targets) != 1:
            continue
        target_path = targets[0]
        if binding.kind == "named":
            named_from_bindings[binding.local_name] = (target_path, binding.imported_name)

    for local, (target_path, imported_name) in named_from_bindings.items():
        for symbol in index.symbols_by_file.get(target_path, []):
            if symbol.kind not in {"function", "method", "class", "constant"}:
                continue
            if symbol.name == imported_name or symbol.qualified_name.endswith(f".{imported_name}"):
                scope.names.setdefault(local, []).append(symbol.id)

    if isinstance(parsed, IrDocument):
        source_relationships = parsed.relationships
    else:
        source_relationships = parsed.mentions

    if not bindings:
        for mention in source_relationships:
            relationship_kind = (
                mention.kind
                if isinstance(mention, IrRelationship)
                else mention.relationship
            )
            if relationship_kind != "IMPORTS":
                continue
            targets = resolve_targets(mention.mentioned_name)
            if len(targets) != 1:
                continue
            for symbol in index.symbols_by_file[targets[0]]:
                if symbol.kind in {"function", "method"}:
                    scope.names.setdefault(symbol.name, []).append(symbol.id)
    elif not scope.names:
        for binding in bindings:
            if binding.kind not in {"named", "module"}:
                continue
            targets = resolve_targets(binding.module_specifier)
            if len(targets) != 1:
                continue
            for symbol in index.symbols_by_file[targets[0]]:
                if symbol.kind in {"function", "method"} and (
                    not binding.imported_name or symbol.name == binding.imported_name
                ):
                    scope.names.setdefault(binding.local_name or symbol.name, []).append(symbol.id)
    return scope


def resolve_call(
    *,
    path: str,
    mention: RawMention | IrRelationship,
    symbols_by_file: dict,
    local_names: dict[str, list[str]],
    import_scope: ImportScope,
) -> ResolvedTarget:
    """Resolve a CALLS/REFERENCES mention conservatively."""
    mentioned = mention.mentioned_name.strip()

    if isinstance(mention, IrRelationship):
        source_symbol_index = mention.attributes.get(
            "legacy_source_symbol_index"
        )
    else:
        source_symbol_index = mention.source_symbol_index

    if not mentioned:
        return ResolvedTarget(None, "unresolved", ResolutionMethod.UNRESOLVED.value, "empty")

    if "." in mentioned:
        receiver, _, leaf = mentioned.partition(".")
        if receiver in import_scope.namespaces and leaf and "." not in leaf:
            hits: list[str] = []
            for target_path in import_scope.namespaces[receiver]:
                hits.extend(
                    symbol.id
                    for symbol in symbols_by_file.get(target_path, [])
                    if symbol.kind in {"function", "method"} and symbol.name == leaf
                )
            return _from_candidates(hits, strategy="import_namespace", mentioned=mentioned)
        if receiver in {"self", "cls", "this"} and leaf and "." not in leaf:
            local = local_names.get(leaf) or []
            if source_symbol_index is None:
                return _from_candidates(local, strategy="self_method", mentioned=mentioned)
            source = symbols_by_file[path][source_symbol_index]
            owner = source.qualified_name.rsplit(".", 1)[0]
            by_id = {symbol.id: symbol for symbol in symbols_by_file[path]}
            same = [sid for sid in local if by_id[sid].qualified_name == f"{owner}.{leaf}"]
            return _from_candidates(same or local, strategy="self_method", mentioned=mentioned)
        if receiver in _EXTERNAL_RECEIVERS or _looks_external_receiver(receiver):
            return ResolvedTarget(
                None,
                "external",
                ResolutionMethod.UNRESOLVED.value,
                "external_receiver",
                evidence=mentioned,
            )
        return ResolvedTarget(
            None,
            "unresolved",
            ResolutionMethod.UNRESOLVED.value,
            "dynamic_or_typed_receiver",
            evidence=mentioned,
        )

    leaf = mentioned
    candidates = list(local_names.get(leaf) or [])
    strategy = "local_exact"
    if not candidates:
        candidates = list(import_scope.names.get(leaf) or [])
        strategy = "import_scoped"
    if not candidates and leaf in _EXTERNAL_RECEIVERS:
        return ResolvedTarget(None, "external", ResolutionMethod.UNRESOLVED.value, "builtin_or_host", evidence=mentioned)
    if leaf.lower() in _AMBIGUOUS_CALL_NAMES and not candidates:
        return ResolvedTarget(None, "unresolved", ResolutionMethod.UNRESOLVED.value, "common_name")
    return _from_candidates(candidates, strategy=strategy, mentioned=mentioned)


def resolve_import_targets(
    *,
    source_path: str,
    specifier: str,
    candidate_ids: list[str],
) -> ResolvedTarget:
    if not candidate_ids:
        if _looks_external_module(specifier):
            return ResolvedTarget(
                None,
                "external",
                ResolutionMethod.UNRESOLVED.value,
                "external_module",
                evidence=specifier,
            )
        return ResolvedTarget(None, "unresolved", ResolutionMethod.UNRESOLVED.value, "no_target", evidence=specifier)
    if len(candidate_ids) > 1:
        return ResolvedTarget(None, "ambiguous", ResolutionMethod.AMBIGUOUS.value, "multi_target", evidence=specifier)
    method = ResolutionMethod.CONFIG.value if _looks_aliased(specifier) else ResolutionMethod.STATIC.value
    return ResolvedTarget(candidate_ids[0], "resolved", method, "unique_module", evidence=specifier)


def go_import_targets(specifier: str, paths: set[str], go_module: str) -> list[str]:
    """Resolve Go import paths within the same module to .go files (package directory)."""
    if not go_module or not specifier.startswith(go_module):
        return []
    rest = specifier[len(go_module) :].lstrip("/")
    if not rest:
        # Root package — all .go files at repo root (excluding tests preference later).
        return sorted(path for path in paths if path.endswith(".go") and "/" not in path)
    prefix = rest.rstrip("/") + "/"
    found = sorted(path for path in paths if path.startswith(prefix) and path.endswith(".go"))
    return found


def aggregate_relationship_metrics(
    edges: list,
    *,
    files_by_id: dict[str, object] | None = None,
    symbols_by_id: dict[str, object] | None = None,
    language_by_path: dict[str, str | None] | None = None,
) -> dict[str, dict[str, dict]]:
    """Return {language: {relationship: metrics_dict}} from assembled edges."""
    language_by_path = language_by_path or {}
    files_by_id = files_by_id or {}
    symbols_by_id = symbols_by_id or {}
    bucket: dict[str, dict[str, RelationshipMetrics]] = defaultdict(lambda: defaultdict(RelationshipMetrics))

    for edge in edges:
        rel = getattr(edge, "relationship", "")
        if rel not in {"CALLS", "IMPORTS", "REFERENCES", "ROUTES_TO", "HANDLES"}:
            continue
        language = _edge_language(edge, files_by_id, symbols_by_id, language_by_path)
        bucket[language][rel].observe(getattr(edge, "resolution", "") or "unresolved")

    return {
        language: {rel: metrics.as_dict() for rel, metrics in rels.items()}
        for language, rels in sorted(bucket.items())
    }


def coverage_state(metrics: dict | None, *, adapter_claims: bool) -> tuple[bool, str]:
    """Measure verified in-repository relationships, excluding external sites."""
    if not metrics:
        return (False, "no_sites") if adapter_claims else (False, "unsupported")

    total = int(metrics.get("total_sites") or 0)
    verified = int(metrics.get("verified_resolved") or 0)
    external = int(metrics.get("external") or 0)
    protocol = int(metrics.get("protocol") or 0)

    if total <= 0:
        return False, "no_sites"

    # External dependencies and protocol links are not internal
    # CALLS/IMPORTS resolution failures.
    internal_total = max(0, total - external - protocol)

    if internal_total == 0:
        return False, "no_sites"

    if verified == 0:
        return False, "zero_verified"

    if verified < internal_total:
        return True, "partial"

    return True, "supported"


def _from_candidates(
    candidates: list[str],
    *,
    strategy: str,
    mentioned: str,
    external_hint: bool = False,
) -> ResolvedTarget:
    unique = list(dict.fromkeys(candidates))
    if not unique:
        if external_hint or _looks_external_callee(mentioned):
            return ResolvedTarget(None, "external", ResolutionMethod.UNRESOLVED.value, strategy or "external")
        return ResolvedTarget(None, "unresolved", ResolutionMethod.UNRESOLVED.value, strategy)
    if len(unique) > 1:
        return ResolvedTarget(None, "ambiguous", ResolutionMethod.AMBIGUOUS.value, strategy)
    return ResolvedTarget(unique[0], "resolved", ResolutionMethod.STATIC.value, strategy)


def _looks_external_module(specifier: str) -> bool:
    if specifier.startswith(".") or specifier.startswith("/"):
        return False
    if specifier.startswith("@/") or specifier.startswith("~/") or specifier.startswith("#/"):
        return False
    # Bare package names and URLs.
    if "/" in specifier and not specifier.startswith("app.") and ".py" not in specifier:
        # Could be workspace package — treat as external unless resolved.
        return True
    if re.match(r"^[A-Za-z_][\w.]*$", specifier) and "." not in specifier:
        return True
    return False


def _looks_aliased(specifier: str) -> bool:
    return specifier.startswith("@/") or specifier.startswith("~/") or specifier.startswith("#/") or "@" in specifier[:1]


def _looks_external_receiver(receiver: str) -> bool:
    if not receiver:
        return False
    if receiver[0].isupper() and receiver.lower() != receiver:
        # PascalCase often denotes imported type/class — not automatically external.
        return False
    return receiver in _EXTERNAL_RECEIVERS


def _looks_external_callee(mentioned: str) -> bool:
    if "." in mentioned:
        return mentioned.split(".", 1)[0] in _EXTERNAL_RECEIVERS
    return mentioned in _EXTERNAL_RECEIVERS


def _edge_language(edge, files_by_id, symbols_by_id, language_by_path: dict[str, str | None]) -> str:
    source = getattr(edge, "source_id", "")
    symbol = symbols_by_id.get(source)
    raw = None
    if symbol is not None:
        raw = getattr(symbol, "language", None)
        if not raw:
            path = getattr(symbol, "relative_path", None)
            if path:
                raw = language_by_path.get(path)
    if raw is None:
        file = files_by_id.get(source)
        if file is not None:
            path = getattr(file, "relative_path", "")
            raw = getattr(file, "language", None) or language_by_path.get(path)
    return _normalise_language(raw or "unknown")


def _normalise_language(language: str) -> str:
    key = (language or "unknown").lower()
    if key in {"tsx", "typescript"}:
        return "typescript"
    if key in {"jsx", "javascript", "mjs", "cjs"}:
        return "javascript"
    if key in {"csharp", "c_sharp"}:
        return "csharp"
    return key
