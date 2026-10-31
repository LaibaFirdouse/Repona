"""Dependency-aware invalidation keys for relationship re-resolution.

Full incremental re-index still builds a new generation. These keys document which
manifest/config changes must invalidate import/alias/call resolution results so a
future incremental path can target only affected relationships.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.project_context import ProjectContext


@dataclass(frozen=True)
class InvalidationImpact:
    keys: tuple[str, ...]
    relationship_kinds: tuple[str, ...]
    reason: str


_MANIFEST_KIND = {
    "package.json": ("IMPORTS", "DEPENDS_ON", "CALLS", "ROUTES_TO"),
    "tsconfig.json": ("IMPORTS", "CALLS", "DEPENDS_ON"),
    "jsconfig.json": ("IMPORTS", "CALLS", "DEPENDS_ON"),
    "pyproject.toml": ("IMPORTS", "DEPENDS_ON", "CALLS"),
    "requirements.txt": ("IMPORTS", "DEPENDS_ON"),
    "go.mod": ("IMPORTS", "DEPENDS_ON", "CALLS"),
    "Cargo.toml": ("IMPORTS", "DEPENDS_ON"),
    "pom.xml": ("IMPORTS", "DEPENDS_ON", "ROUTES_TO"),
    "build.gradle": ("IMPORTS", "DEPENDS_ON", "ROUTES_TO"),
    "build.gradle.kts": ("IMPORTS", "DEPENDS_ON", "ROUTES_TO"),
}


def dependency_keys_for_context(context: ProjectContext) -> list[str]:
    keys: list[str] = []
    for path in sorted(context.manifests):
        keys.append(f"manifest:{path}")
    for rule in context.path_alias_rules:
        keys.append(f"alias:{rule.source_manifest}:{rule.pattern}")
    for name in context.package_names:
        keys.append(f"package:{name}")
    return list(dict.fromkeys(keys))


def impact_for_changed_paths(changed_relative_paths: list[str]) -> list[InvalidationImpact]:
    """Map changed paths to relationship kinds that must be re-resolved."""
    impacts: list[InvalidationImpact] = []
    for path in changed_relative_paths:
        name = path.rsplit("/", 1)[-1]
        if name in _MANIFEST_KIND:
            impacts.append(
                InvalidationImpact(
                    keys=(f"manifest:{path}",),
                    relationship_kinds=_MANIFEST_KIND[name],
                    reason=f"configuration change in {path}",
                )
            )
            continue
        if path.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rs", ".cs")):
            kinds = ["IMPORTS", "CALLS", "REFERENCES", "DECLARES", "DEPENDS_ON", "ROUTES_TO", "HANDLES"]
            impacts.append(
                InvalidationImpact(
                    keys=(f"source:{path}",),
                    relationship_kinds=tuple(kinds),
                    reason=f"source change in {path}",
                )
            )
    return impacts


def relationships_invalidated_by(changed_relative_paths: list[str]) -> set[str]:
    kinds: set[str] = set()
    for impact in impact_for_changed_paths(changed_relative_paths):
        kinds.update(impact.relationship_kinds)
    return kinds
