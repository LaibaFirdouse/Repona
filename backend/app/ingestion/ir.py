"""Language-agnostic intermediate representation contracts for Repona.

Adapters emit these structures; assemble/graph_store persist what the current
schema supports. New relationship kinds may be declared here before Neo4j
accepts them — only registered edge types are written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class CapabilityLevel(int, Enum):
    """Per-language (or per-framework) intelligence depth for one generation."""

    SOURCE_DISCOVERABLE = 0
    SYNTAX_AWARE = 1
    STRUCTURAL_IMPORT = 2
    SEMANTIC_RELATIONSHIP = 3
    FRAMEWORK_EXECUTION = 4


class ResolutionMethod(str, Enum):
    STATIC = "static"
    CONFIG = "config"
    PROTOCOL = "protocol"
    HEURISTIC = "heuristic"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


# Relationship kinds the product aims to support. Persist only those listed in
# graph_store._EDGE_TYPES for a given PARSER_VERSION.
RelationshipKind = Literal[
    "CONTAINS",
    "DECLARES",
    "DEFINES",
    "IMPORTS",
    "EXPORTS",
    "CALLS",
    "REFERENCES",
    "IMPLEMENTS",
    "EXTENDS",
    "DEPENDS_ON",
    "ROUTES_TO",
    "HANDLES",
    "READS_FROM",
    "WRITES_TO",
    "PUBLISHES",
    "SUBSCRIBES_TO",
    "SERIALIZES_TO",
    "HAS_GENERATION",
]

# Currently persisted by Neo4j writers (must stay in sync with graph_store).
PERSISTED_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        "CONTAINS",
        "DECLARES",
        "IMPORTS",
        "DEPENDS_ON",
        "CALLS",
        "REFERENCES",
        "HAS_GENERATION",
        "ROUTES_TO",
        "HANDLES",
        "READS_FROM",
        "WRITES_TO",
    }
)

EntityKind = Literal[
    "repository",
    "package",
    "module",
    "file",
    "class",
    "struct",
    "interface",
    "function",
    "method",
    "variable",
    "constant",
    "type",
    "enum",
    "import",
    "export",
    "configuration",
    "endpoint",
    "event_handler",
    "database_operation",
    "message_contract",
    "test",
    "symbol",
]


@dataclass(frozen=True)
class SourceRange:
    relative_path: str
    start_byte: int
    end_byte: int
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True)
class RelationshipProvenance:
    method: ResolutionMethod
    evidence: str = ""
    confidence: float = 1.0
    adapter_id: str = ""


@dataclass
class IrEntity:
    id: str
    kind: EntityKind
    name: str
    qualified_name: str
    language: str | None = None
    file_path: str | None = None
    range: SourceRange | None = None
    attributes: dict = field(default_factory=dict)


@dataclass
class IrRelationship:
    kind: RelationshipKind
    source_id: str
    target_id: str | None
    mentioned_name: str = ""
    source_range: SourceRange | None = None
    provenance: RelationshipProvenance = field(
        default_factory=lambda: RelationshipProvenance(ResolutionMethod.UNRESOLVED)
    )
    generation_id: str = ""
    attributes: dict = field(default_factory=dict)


@dataclass
class IrImportBinding:
    """Language-neutral representation of an import binding."""

    module_specifier: str
    local_name: str
    imported_name: str
    kind: str
    source_range: SourceRange | None = None


@dataclass
class IrDocument:
    """Normalized parse result for one source file.

    Entity IDs are local to this document. They are not persisted database IDs.
    Assembly remains responsible for assigning stable repository-level IDs.
    """

    relative_path: str
    language: str | None
    parse_status: str
    parse_message: str | None
    entities: list[IrEntity] = field(default_factory=list)
    relationships: list[IrRelationship] = field(default_factory=list)
    import_spans: list[tuple[int, int]] = field(default_factory=list)
    import_bindings: list[IrImportBinding] = field(default_factory=list)


@dataclass
class LanguageCapability:
    language: str
    level: CapabilityLevel
    file_count: int = 0
    parser_available: bool = False
    syntax_extraction: bool = False
    import_resolution: bool = False
    calls_resolution: bool = False
    adapter_id: str = ""
    notes: str = ""
    # Measured coverage from actual edges — not merely adapter claims.
    relationship_metrics: dict = field(default_factory=dict)
    calls_coverage: str = "unknown"  # supported | partial | unsupported | unknown
    imports_coverage: str = "unknown"


@dataclass
class FrameworkCapability:
    framework_id: str
    display_name: str
    level: CapabilityLevel
    detected: bool = False
    route_discovery: bool = False
    persistence_discovery: bool = False
    adapter_id: str = ""
    evidence: tuple[str, ...] = ()


@dataclass
class RepositoryCapabilityReport:
    """Machine-readable support report for one index generation."""

    repository_id: str
    generation_id: str
    languages: list[LanguageCapability] = field(default_factory=list)
    frameworks: list[FrameworkCapability] = field(default_factory=list)
    unresolved_calls: int = 0
    unresolved_imports: int = 0
    total_calls: int = 0
    total_imports: int = 0
    semantic_index: str = "none"  # none | planned_scip | planned_lsp | available_unwired
    relationship_metrics: dict = field(default_factory=dict)
    resolver_version: str = "relationship_resolve_v1"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "repository_id": self.repository_id,
            "generation_id": self.generation_id,
            "languages": [
                {
                    "language": item.language,
                    "level": int(item.level),
                    "level_name": item.level.name,
                    "file_count": item.file_count,
                    "parser_available": item.parser_available,
                    "syntax_extraction": item.syntax_extraction,
                    "import_resolution": item.import_resolution,
                    "calls_resolution": item.calls_resolution,
                    "calls_coverage": item.calls_coverage,
                    "imports_coverage": item.imports_coverage,
                    "relationship_metrics": item.relationship_metrics,
                    "adapter_id": item.adapter_id,
                    "notes": item.notes,
                }
                for item in self.languages
            ],
            "frameworks": [
                {
                    "framework_id": item.framework_id,
                    "display_name": item.display_name,
                    "level": int(item.level),
                    "detected": item.detected,
                    "route_discovery": item.route_discovery,
                    "persistence_discovery": item.persistence_discovery,
                    "adapter_id": item.adapter_id,
                    "evidence": list(item.evidence),
                }
                for item in self.frameworks
            ],
            "unresolved_calls": self.unresolved_calls,
            "unresolved_imports": self.unresolved_imports,
            "total_calls": self.total_calls,
            "total_imports": self.total_imports,
            "semantic_index": self.semantic_index,
            "relationship_metrics": self.relationship_metrics,
            "resolver_version": self.resolver_version,
            "notes": list(self.notes),
        }



def parsed_file_to_ir(
    relative_path: str,
    parsed,
    *,
    adapter_id: str = "",
) -> IrDocument:
    """Normalize the current ParsedFile contract into language-neutral IR.

    Legacy parser metadata is retained in attributes so the IR can be
    projected back without changing existing assembly behavior.
    """
    from app.ingestion.parse import ImportBinding

    entities: list[IrEntity] = []
    entity_ids: dict[int, str] = {}

    for index, symbol in enumerate(parsed.symbols):
        entity_ids[index] = f"{relative_path}::entity:{index}"

    for index, symbol in enumerate(parsed.symbols):
        parent_id = entity_ids.get(symbol.parent_index)

        entities.append(
            IrEntity(
                id=entity_ids[index],
                kind=symbol.kind,
                name=symbol.name,
                qualified_name=symbol.qualified_name,
                language=parsed.language,
                file_path=relative_path,
                range=SourceRange(
                    relative_path=relative_path,
                    start_byte=symbol.start_byte,
                    end_byte=symbol.end_byte,
                ),
                attributes={
                    "signature": symbol.signature,
                    "legacy_symbol_index": index,
                    "legacy_parent_index": symbol.parent_index,
                    "parent_entity_id": parent_id,
                },
            )
        )

    # Import mentions without an enclosing symbol originate at the module.
    module = next(
        (entity for entity in entities if entity.kind == "module"),
        None,
    )

    needs_module = any(
        mention.source_symbol_index is None
        for mention in parsed.mentions
    )

    if module is None and needs_module:
        module_id = f"{relative_path}::module"
        module_name = relative_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

        module = IrEntity(
            id=module_id,
            kind="module",
            name=module_name,
            qualified_name=relative_path.rsplit(".", 1)[0].replace("/", "."),
            language=parsed.language,
            file_path=relative_path,
            range=SourceRange(relative_path, 0, 0),
            attributes={"synthetic_for_ir": True},
        )
        entities.append(module)

    relationships: list[IrRelationship] = []

    for mention in parsed.mentions:
        source_index = mention.source_symbol_index
        source_id = entity_ids.get(source_index)

        if source_id is None:
            source_id = module.id if module is not None else f"{relative_path}::module"

        relationships.append(
            IrRelationship(
                kind=mention.relationship,
                source_id=source_id,
                target_id=None,
                mentioned_name=mention.mentioned_name,
                source_range=SourceRange(
                    relative_path=relative_path,
                    start_byte=mention.start_byte,
                    end_byte=mention.end_byte,
                ),
                provenance=RelationshipProvenance(
                    method=ResolutionMethod.UNRESOLVED,
                    adapter_id=adapter_id,
                ),
                attributes={
                    "legacy_source_symbol_index": source_index,
                    "legacy_import_span": mention.import_span,
                },
            )
        )

    import_bindings = [
        IrImportBinding(
            module_specifier=binding.module_specifier,
            local_name=binding.local_name,
            imported_name=binding.imported_name,
            kind=binding.kind,
            source_range=SourceRange(
                relative_path=relative_path,
                start_byte=binding.start_byte,
                end_byte=binding.end_byte,
            ),
        )
        for binding in parsed.import_bindings
    ]

    return IrDocument(
        relative_path=relative_path,
        language=parsed.language,
        parse_status=parsed.parse_status,
        parse_message=parsed.parse_message,
        entities=entities,
        relationships=relationships,
        import_spans=list(parsed.import_spans),
        import_bindings=import_bindings,
    )


def ir_to_parsed_file(document: IrDocument):
    """Compatibility projection from normalized IR to the legacy parser model."""
    from app.ingestion.parse import (
        ImportBinding,
        ParsedFile,
        RawMention,
        RawSymbol,
    )

    source_entities = [
        entity
        for entity in document.entities
        if not entity.attributes.get("synthetic_for_ir")
    ]

    source_entities.sort(
        key=lambda entity: entity.attributes.get(
            "legacy_symbol_index",
            document.entities.index(entity),
        )
    )

    symbols = []
    for entity in source_entities:
        source_range = entity.range
        attributes = entity.attributes

        symbols.append(
            RawSymbol(
                kind=entity.kind,
                name=entity.name,
                qualified_name=entity.qualified_name,
                signature=attributes.get("signature", ""),
                start_byte=source_range.start_byte if source_range else 0,
                end_byte=source_range.end_byte if source_range else 0,
                parent_index=attributes.get("legacy_parent_index"),
            )
        )

    mentions = []
    for relationship in document.relationships:
        source_range = relationship.source_range
        attributes = relationship.attributes

        mentions.append(
            RawMention(
                relationship=relationship.kind,
                mentioned_name=relationship.mentioned_name,
                start_byte=source_range.start_byte if source_range else 0,
                end_byte=source_range.end_byte if source_range else 0,
                source_symbol_index=attributes.get(
                    "legacy_source_symbol_index"
                ),
                import_span=attributes.get("legacy_import_span"),
            )
        )

    import_bindings = [
        ImportBinding(
            module_specifier=binding.module_specifier,
            local_name=binding.local_name,
            imported_name=binding.imported_name,
            kind=binding.kind,
            start_byte=(
                binding.source_range.start_byte
                if binding.source_range else 0
            ),
            end_byte=(
                binding.source_range.end_byte
                if binding.source_range else 0
            ),
        )
        for binding in document.import_bindings
    ]

    return ParsedFile(
        language=document.language,
        parse_status=document.parse_status,
        parse_message=document.parse_message,
        symbols=symbols,
        mentions=mentions,
        import_spans=list(document.import_spans),
        import_bindings=import_bindings,
    )
