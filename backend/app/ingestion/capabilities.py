"""Build per-generation language/framework capability reports with measured coverage."""

from __future__ import annotations

from collections import Counter

from app.ingestion.adapters import builtin as _builtin_adapters  # noqa: F401 — register defaults
from app.ingestion.adapters.framework import framework_registry
from app.ingestion.adapters.language import language_registry
from app.ingestion.ir import (
    CapabilityLevel,
    FrameworkCapability,
    LanguageCapability,
    RepositoryCapabilityReport,
)
from app.ingestion.parse import LANGUAGE_BY_EXTENSION, language_for_path
from app.ingestion.project_context import ProjectContext, discover_project_context
from app.ingestion.relationship_resolve import aggregate_relationship_metrics, coverage_state


def build_capability_report(
    *,
    repository_id: str,
    generation_id: str,
    relative_paths: list[str],
    parse_status_by_path: dict[str, str] | None = None,
    languages_by_path: dict[str, str | None] | None = None,
    edges: list | None = None,
    blobs_by_path: dict[str, bytes] | None = None,
    context: ProjectContext | None = None,
    files: list | None = None,
    symbols: list | None = None,
) -> RepositoryCapabilityReport:
    parse_status_by_path = parse_status_by_path or {}
    languages_by_path = languages_by_path or {}
    edges = edges or []
    context = context or discover_project_context(relative_paths, blobs_by_path)

    lang_counts: Counter[str] = Counter()
    for path in relative_paths:
        language = languages_by_path.get(path) or language_for_path(path) or _extension_language(path) or "unknown"
        lang_counts[language] += 1

    files_by_id = {getattr(item, "id"): item for item in (files or [])}
    symbols_by_id = {getattr(item, "id"): item for item in (symbols or [])}
    metrics_by_lang = aggregate_relationship_metrics(
        edges,
        files_by_id=files_by_id,
        symbols_by_id=symbols_by_id,
        language_by_path=languages_by_path,
    )

    languages: list[LanguageCapability] = []
    for language, count in sorted(lang_counts.items()):
        adapter = language_registry.for_language(language)
        if adapter is None and language in {"javascript", "typescript", "tsx"}:
            adapter = language_registry.for_language("typescript")
        level = adapter.capability_level() if adapter is not None else CapabilityLevel.SOURCE_DISCOVERABLE
        parsed_ok = sum(
            1
            for path in relative_paths
            if (languages_by_path.get(path) or language_for_path(path) or _extension_language(path) or "unknown")
            == language
            and parse_status_by_path.get(path) in {"parsed", "syntax_error"}
        )
        syntax = level.value >= CapabilityLevel.SYNTAX_AWARE.value and parsed_ok > 0
        adapter_imports = level.value >= CapabilityLevel.STRUCTURAL_IMPORT.value
        adapter_calls = level.value >= CapabilityLevel.SEMANTIC_RELATIONSHIP.value
        if language == "unknown" or adapter is None:
            syntax = False
            adapter_imports = False
            adapter_calls = False
            level = CapabilityLevel.SOURCE_DISCOVERABLE

        lang_metrics = metrics_by_lang.get(language) or {}
        calls_metrics = lang_metrics.get("CALLS")
        imports_metrics = lang_metrics.get("IMPORTS")
        calls_ok, calls_coverage = coverage_state(calls_metrics, adapter_claims=adapter_calls)
        imports_ok, imports_coverage = coverage_state(imports_metrics, adapter_claims=adapter_imports)
        if not adapter_calls and calls_coverage == "no_sites":
            calls_coverage = "unsupported"
        if not adapter_imports and imports_coverage == "no_sites":
            imports_coverage = "unsupported"
        # Prefer measured demonstration over adapter claims for capability flags.
        import_resolution = imports_ok if imports_metrics else adapter_imports and parsed_ok > 0
        calls_resolution = calls_ok if calls_metrics else False
        if calls_metrics and calls_coverage == "partial":
            calls_resolution = True
        if imports_metrics and imports_coverage == "partial":
            import_resolution = True

        # Downgrade advertised level when measured CALLS coverage is weak.
        effective_level = level

        if parsed_ok == 0:
            effective_level = CapabilityLevel.SOURCE_DISCOVERABLE

        elif (
            level.value >= CapabilityLevel.SEMANTIC_RELATIONSHIP.value
            and calls_coverage in {
                "unsupported",
                "zero_verified",
                "no_sites",
            }
        ):
            effective_level = (
                CapabilityLevel.STRUCTURAL_IMPORT
                if import_resolution
                else CapabilityLevel.SYNTAX_AWARE
            )

        notes = ""
        if adapter is None:
            notes = "lexical/source retrieval only"
        elif parsed_ok == 0:
            notes = "adapter registered; no files parsed successfully"
        elif calls_metrics:
            notes = f"calls_coverage={calls_coverage}"

        languages.append(
            LanguageCapability(
                language=language,
                level=effective_level,
                file_count=count,
                parser_available=parsed_ok > 0,
                syntax_extraction=syntax,
                import_resolution=import_resolution,
                calls_resolution=calls_resolution,
                adapter_id=adapter.id if adapter is not None else "",
                notes=notes,
                relationship_metrics=lang_metrics,
                calls_coverage=calls_coverage if adapter_calls or calls_metrics else "unknown",
                imports_coverage=imports_coverage if adapter_imports or imports_metrics else "unknown",
            )
        )

    frameworks: list[FrameworkCapability] = []
    for capability in framework_registry.detect_all(context, relative_paths):
        frameworks.append(capability)

    total_calls = sum(1 for edge in edges if getattr(edge, "relationship", None) == "CALLS")
    unresolved_calls = sum(
        1
        for edge in edges
        if getattr(edge, "relationship", None) == "CALLS"
        and getattr(edge, "resolution", "") in {"unresolved", "ambiguous", "external"}
    )
    total_imports = sum(1 for edge in edges if getattr(edge, "relationship", None) == "IMPORTS")
    unresolved_imports = sum(
        1
        for edge in edges
        if getattr(edge, "relationship", None) == "IMPORTS"
        and getattr(edge, "resolution", "") in {"unresolved", "ambiguous", "external"}
    )

    notes = [
        "Capability coverage is measured from indexed edges, not adapter presence alone.",
        "SCIP/LSP semantic indexes are optional and not required for indexing.",
    ]
    if context.frameworks_hinted:
        notes.append("manifest_frameworks=" + ",".join(context.frameworks_hinted))
    invalidation = _invalidation_keys(context)
    if invalidation:
        notes.append("invalidation_keys=" + ",".join(invalidation[:12]))

    return RepositoryCapabilityReport(
        repository_id=repository_id,
        generation_id=generation_id,
        languages=languages,
        frameworks=frameworks,
        unresolved_calls=unresolved_calls,
        unresolved_imports=unresolved_imports,
        total_calls=total_calls,
        total_imports=total_imports,
        semantic_index=_detect_semantic_index_availability(),
        relationship_metrics=metrics_by_lang,
        resolver_version="relationship_resolve_v1",
        notes=notes,
    )


def _invalidation_keys(context: ProjectContext) -> list[str]:
    from app.ingestion.invalidation import dependency_keys_for_context

    return dependency_keys_for_context(context)


def _detect_semantic_index_availability() -> str:
    """Optional SCIP/LSP tooling — never required; report availability only."""
    import shutil

    if shutil.which("scip") or shutil.which("scip-typescript") or shutil.which("scip-python"):
        return "available_unwired"
    return "none"


def _extension_language(path: str) -> str | None:
    from app.ingestion.inclusion import INCLUDED_FILENAMES
    from app.ingestion.inclusion import LANGUAGE_BY_EXTENSION as inclusion_langs

    name = path.rsplit("/", 1)[-1]
    if name in INCLUDED_FILENAMES:
        return "configuration"
    dot = name.rfind(".")
    if dot <= 0:
        return None
    ext = name[dot:].lower()
    return LANGUAGE_BY_EXTENSION.get(ext) or inclusion_langs.get(ext)
