"""Generation-scoped capability profile for investigation planning.

Normalises the ingestion capability report (and safe catalogue fallbacks) into
planner-facing facet states. Does not invent adapters or rescan manifests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.models.catalog import File, GenerationFile, IndexGeneration


class CapabilityState(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CapabilityFacet:
    key: str
    state: CapabilityState
    detail: str = ""
    evidence: tuple[str, ...] = ()


@dataclass
class CapabilityProfile:
    """Planner view of one index generation's intelligence depth."""

    generation_id: str
    repository_id: str = ""
    source: str = "missing"  # report | catalogue_fallback | missing
    languages: dict[str, int] = field(default_factory=dict)
    frameworks: tuple[str, ...] = ()
    facets: dict[str, CapabilityFacet] = field(default_factory=dict)
    max_language_level: int = 0
    unresolved_calls: int = 0
    total_calls: int = 0
    notes: list[str] = field(default_factory=list)

    def state(self, key: str) -> CapabilityState:
        facet = self.facets.get(key)
        return facet.state if facet is not None else CapabilityState.UNKNOWN

    def is_usable(self, key: str) -> bool:
        return self.state(key) in {CapabilityState.SUPPORTED, CapabilityState.PARTIAL}

    def as_trace_dict(self) -> dict:
        return {
            "generation_id": self.generation_id,
            "repository_id": self.repository_id,
            "source": self.source,
            "languages": dict(self.languages),
            "frameworks": list(self.frameworks),
            "max_language_level": self.max_language_level,
            "facets": {
                key: {"state": facet.state.value, "detail": facet.detail}
                for key, facet in sorted(self.facets.items())
            },
            "unresolved_calls": self.unresolved_calls,
            "total_calls": self.total_calls,
            "notes": self.notes[:8],
        }


def profile_from_report(report: dict[str, Any] | None, *, expected_generation_id: str) -> CapabilityProfile:
    """Build a profile from a persisted/in-memory capability report dict."""
    if not report:
        return unknown_profile(expected_generation_id, reason="missing_capability_report")

    report_generation = str(report.get("generation_id") or "")
    if report_generation and report_generation != expected_generation_id:
        return unknown_profile(
            expected_generation_id,
            reason="generation_mismatch",
            notes=[f"ignored report for generation {report_generation}"],
        )

    languages = {
        str(item.get("language")): int(item.get("file_count") or 0)
        for item in report.get("languages") or []
        if item.get("language")
    }
    frameworks = tuple(
        str(item.get("framework_id"))
        for item in report.get("frameworks") or []
        if item.get("detected")
    )
    max_level = 0
    for item in report.get("languages") or []:
        try:
            max_level = max(max_level, int(item.get("level") or 0))
        except (TypeError, ValueError):
            continue

    facets = _facets_from_report(report, languages, frameworks, max_level)
    return CapabilityProfile(
        generation_id=expected_generation_id,
        repository_id=str(report.get("repository_id") or ""),
        source="report",
        languages=languages,
        frameworks=frameworks,
        facets=facets,
        max_language_level=max_level,
        unresolved_calls=int(report.get("unresolved_calls") or 0),
        total_calls=int(report.get("total_calls") or 0),
        notes=list(report.get("notes") or [])[:8],
    )


def load_capability_profile(db: Session, generation: IndexGeneration) -> CapabilityProfile:
    """Load the generation's capability profile. Never uses another generation's report."""
    raw = getattr(generation, "capability_report_json", None)
    if raw:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError, UnicodeError):
            payload = None
        if isinstance(payload, dict):
            profile = profile_from_report(payload, expected_generation_id=generation.id)
            if profile.source == "report":
                return profile
            # generation_mismatch already returned as unknown
            if profile.notes and any("ignored report" in note for note in profile.notes):
                return profile

    # Safe catalogue fallback — language counts only; facets stay UNKNOWN where undemonstrated.
    return catalogue_fallback_profile(db, generation)


def catalogue_fallback_profile(db: Session, generation: IndexGeneration) -> CapabilityProfile:
    rows = (
        db.query(File.language)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(GenerationFile.index_generation_id == generation.id)
        .all()
    )
    counts: dict[str, int] = {}
    for (language,) in rows:
        key = (language or "unknown").lower()
        counts[key] = counts.get(key, 0) + 1
    facets = {
        "syntax_extraction": CapabilityFacet("syntax_extraction", CapabilityState.UNKNOWN, "no persisted report"),
        "symbol_extraction": CapabilityFacet("symbol_extraction", CapabilityState.UNKNOWN, "no persisted report"),
        "import_resolution": CapabilityFacet("import_resolution", CapabilityState.UNKNOWN, "no persisted report"),
        "calls_resolution": CapabilityFacet("calls_resolution", CapabilityState.UNKNOWN, "no persisted report"),
        "http_route_discovery": CapabilityFacet("http_route_discovery", CapabilityState.UNKNOWN, "no persisted report"),
        "protocol_links": CapabilityFacet("protocol_links", CapabilityState.UNKNOWN, "no persisted report"),
        "database_orm": CapabilityFacet("database_orm", CapabilityState.UNKNOWN, "no persisted report"),
        "event_messaging": CapabilityFacet(
            "event_messaging", CapabilityState.UNSUPPORTED, "no event adapters registered"
        ),
        "frontend_ui": _frontend_facet_from_languages(counts, ()),
        "multi_language": _multi_language_facet(counts),
        "execution_tracing": CapabilityFacet(
            "execution_tracing",
            CapabilityState.UNKNOWN,
            "capability report missing; use conservative lexical investigation",
        ),
    }
    return CapabilityProfile(
        generation_id=generation.id,
        repository_id=generation.repository_id,
        source="catalogue_fallback",
        languages=counts,
        frameworks=(),
        facets=facets,
        max_language_level=0,
        notes=["capability_report_missing"],
    )


def unknown_profile(generation_id: str, *, reason: str, notes: list[str] | None = None) -> CapabilityProfile:
    detail = reason
    facets = {
        key: CapabilityFacet(key, CapabilityState.UNKNOWN, detail)
        for key in (
            "syntax_extraction",
            "symbol_extraction",
            "import_resolution",
            "calls_resolution",
            "http_route_discovery",
            "protocol_links",
            "database_orm",
            "event_messaging",
            "frontend_ui",
            "multi_language",
            "execution_tracing",
        )
    }
    return CapabilityProfile(
        generation_id=generation_id,
        source="missing",
        facets=facets,
        notes=[reason, *(notes or [])][:8],
    )


def _facets_from_report(
    report: dict,
    languages: dict[str, int],
    frameworks: tuple[str, ...],
    max_level: int,
) -> dict[str, CapabilityFacet]:
    code_langs = {
        name: count
        for name, count in languages.items()
        if name not in {"configuration", "json", "markdown", "yaml", "toml", "html", "css", "unknown"}
    }
    syntax = _aggregate_bool_facet(
        report,
        "syntax_extraction",
        true_state=CapabilityState.SUPPORTED,
        false_when_code=CapabilityState.UNSUPPORTED,
        has_code=bool(code_langs),
    )
    imports = _aggregate_bool_facet(
        report,
        "import_resolution",
        true_state=CapabilityState.SUPPORTED,
        false_when_code=CapabilityState.UNSUPPORTED,
        has_code=bool(code_langs),
    )
    calls = _calls_facet(report, code_langs)
    route_fw = [item for item in report.get("frameworks") or [] if item.get("detected") and item.get("route_discovery")]
    http = (
        CapabilityFacet(
            "http_route_discovery",
            CapabilityState.SUPPORTED if route_fw else CapabilityState.NOT_APPLICABLE,
            ",".join(str(item.get("framework_id")) for item in route_fw) if route_fw else "no route-capable framework detected",
            evidence=tuple(str(item.get("framework_id")) for item in route_fw),
        )
    )
    multi = _multi_language_facet(code_langs)
    protocol = CapabilityFacet(
        "protocol_links",
        CapabilityState.PARTIAL if multi.state != CapabilityState.NOT_APPLICABLE and http.state == CapabilityState.SUPPORTED else (
            CapabilityState.NOT_APPLICABLE if multi.state == CapabilityState.NOT_APPLICABLE else CapabilityState.UNSUPPORTED
        ),
        "HTTP endpoint correspondence when client and server routes are both discoverable",
    )
    persistence_fw = [
        item for item in report.get("frameworks") or [] if item.get("detected") and item.get("persistence_discovery")
    ]
    # ORM adapters are not yet registered — do not claim UNSUPPORTED for every repo; leave UNKNOWN
    # unless a persistence-capable framework is detected (none today) or no code languages exist.
    if persistence_fw:
        database = CapabilityFacet(
            "database_orm",
            CapabilityState.SUPPORTED,
            ",".join(str(item.get("framework_id")) for item in persistence_fw),
        )
    elif not code_langs:
        database = CapabilityFacet("database_orm", CapabilityState.NOT_APPLICABLE, "no code languages")
    else:
        database = CapabilityFacet(
            "database_orm",
            CapabilityState.UNKNOWN,
            "no persistence adapter demonstrated; database stages rely on source evidence",
        )

    if max_level >= 3 and calls.state in {CapabilityState.SUPPORTED, CapabilityState.PARTIAL}:
        tracing = CapabilityFacet("execution_tracing", CapabilityState.SUPPORTED, f"max_language_level={max_level}")
    elif max_level >= 1:
        tracing = CapabilityFacet("execution_tracing", CapabilityState.PARTIAL, f"max_language_level={max_level}")
    elif code_langs:
        tracing = CapabilityFacet(
            "execution_tracing",
            CapabilityState.PARTIAL,
            "source/lexical only",
        )
    else:
        tracing = CapabilityFacet("execution_tracing", CapabilityState.UNSUPPORTED, "no code languages indexed")

    return {
        "syntax_extraction": syntax,
        "symbol_extraction": syntax,
        "import_resolution": imports,
        "calls_resolution": calls,
        "http_route_discovery": http,
        "protocol_links": protocol,
        "database_orm": database,
        "event_messaging": CapabilityFacet(
            "event_messaging", CapabilityState.UNSUPPORTED, "no event/message adapters registered"
        ),
        "frontend_ui": _frontend_facet_from_languages(languages, frameworks),
        "multi_language": multi,
        "execution_tracing": tracing,
    }


def _aggregate_bool_facet(
    report: dict,
    field_name: str,
    *,
    true_state: CapabilityState,
    false_when_code: CapabilityState,
    has_code: bool,
) -> CapabilityFacet:
    values = [bool(item.get(field_name)) for item in report.get("languages") or [] if item.get("language")]
    if not values:
        return CapabilityFacet(field_name, CapabilityState.UNKNOWN if has_code else CapabilityState.NOT_APPLICABLE)
    if any(values):
        state = true_state if all(values) else CapabilityState.PARTIAL
        return CapabilityFacet(field_name, state)
    return CapabilityFacet(field_name, false_when_code if has_code else CapabilityState.NOT_APPLICABLE)


def _calls_facet(report: dict, code_langs: dict[str, int]) -> CapabilityFacet:
    if not code_langs:
        return CapabilityFacet(
            "calls_resolution",
            CapabilityState.NOT_APPLICABLE,
            "no code languages",
        )

    # Only evaluate actual programming languages.
    langs = [
        item
        for item in report.get("languages") or []
        if str(item.get("language") or "") in code_langs
    ]

    if not langs:
        return CapabilityFacet(
            "calls_resolution",
            CapabilityState.UNKNOWN,
            "no language-level CALLS measurements",
        )

    entries: list[tuple[str, str, dict]] = []

    for item in langs:
        language = str(item.get("language") or "unknown")
        coverage = str(item.get("calls_coverage") or "").lower()
        level = int(item.get("level") or 0)

        # Preserve explicit absence of internal call sites.
        if coverage == "no_sites":
            entries.append((language, "no_sites", item))
            continue

        # Handle older reports and languages whose adapter does not
        # provide semantic CALLS resolution.
        if coverage in {"", "unknown"}:
            if item.get("calls_resolution"):
                coverage = "supported"
            elif level < 3:
                coverage = "unsupported"
            else:
                coverage = "unknown"

        if coverage not in {
            "supported",
            "partial",
            "unsupported",
            "zero_verified",
            "unknown",
        }:
            coverage = "unknown"

        entries.append((language, coverage, item))

    # No internal call sites means there is nothing to measure.
    measurable = [
        entry for entry in entries
        if entry[1] != "no_sites"
    ]

    if not measurable:
        return CapabilityFacet(
            "calls_resolution",
            CapabilityState.UNKNOWN,
            "no internal CALLS sites measured",
        )

    statuses = [entry[1] for entry in measurable]

    if all(status == "unknown" for status in statuses):
        state = CapabilityState.UNKNOWN
        detail = "CALLS coverage unknown"

    elif all(status in {"unsupported", "zero_verified"} for status in statuses):
        state = CapabilityState.UNSUPPORTED
        detail = "no verified internal CALLS resolution"

    elif all(status == "supported" for status in statuses):
        state = CapabilityState.SUPPORTED
        detail = "all measurable languages have supported CALLS coverage"

    else:
        state = CapabilityState.PARTIAL
        detail = "mixed CALLS coverage across languages"

    # Build explanations from internal relationship metrics.
    # External dependencies and protocol links are excluded.
    metric_details = []

    for language, coverage, item in measurable:
        metrics = (item.get("relationship_metrics") or {}).get("CALLS")
        if not metrics:
            continue

        total = int(metrics.get("total_sites") or 0)
        verified = int(metrics.get("verified_resolved") or 0)
        external = int(metrics.get("external") or 0)
        protocol = int(metrics.get("protocol") or 0)

        internal_total = max(0, total - external - protocol)
        verified = min(verified, internal_total)

        metric_details.append(
            f"{language}: {verified}/{internal_total} internal calls"
        )

    if metric_details:
        detail += " (" + "; ".join(metric_details) + ")"

    return CapabilityFacet(
        "calls_resolution",
        state,
        detail,
    )

def _frontend_facet_from_languages(languages: dict[str, int], frameworks: tuple[str, ...]) -> CapabilityFacet:
    ui_langs = {"javascript", "typescript", "tsx", "jsx", "vue", "svelte", "dart"}
    present = sorted(name for name in languages if name in ui_langs)
    ui_fw = sorted(name for name in frameworks if name in {"nextjs", "react", "vue", "angular", "svelte", "nuxt"})
    if ui_fw or present:
        return CapabilityFacet(
            "frontend_ui",
            CapabilityState.SUPPORTED if ui_fw or present else CapabilityState.PARTIAL,
            ",".join(ui_fw or present),
            evidence=tuple(ui_fw or present),
        )
    return CapabilityFacet("frontend_ui", CapabilityState.NOT_APPLICABLE, "no frontend languages/frameworks detected")


def _multi_language_facet(languages: dict[str, int]) -> CapabilityFacet:
    code = [
        name
        for name, count in languages.items()
        if count > 0 and name not in {"configuration", "json", "markdown", "yaml", "toml", "html", "css", "unknown"}
    ]
    if len(code) >= 2:
        return CapabilityFacet("multi_language", CapabilityState.SUPPORTED, ",".join(sorted(code)))
    if len(code) == 1:
        return CapabilityFacet("multi_language", CapabilityState.NOT_APPLICABLE, code[0])
    return CapabilityFacet("multi_language", CapabilityState.UNKNOWN, "no code languages")
