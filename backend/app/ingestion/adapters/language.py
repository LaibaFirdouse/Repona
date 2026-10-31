"""LanguageAdapter protocol and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.ingestion.ir import CapabilityLevel, IrDocument
from app.ingestion.parse import ParsedFile


@runtime_checkable
class LanguageAdapter(Protocol):
    """Language-specific syntax and module intelligence."""

    id: str
    languages: tuple[str, ...]
    extensions: tuple[str, ...]

    def can_parse(self, relative_path: str, language: str | None) -> bool: ...

    def parse(self, relative_path: str, payload: bytes) -> ParsedFile: ...

    def parse_ir(self, relative_path: str, payload: bytes) -> IrDocument: ...

    def capability_level(self) -> CapabilityLevel: ...

    def resolve_import_targets(
        self,
        source_path: str,
        specifier: str,
        paths: set[str],
        *,
        alias_rules: list[tuple[str, list[str]]] | None = None,
    ) -> list[str]:
        """Return candidate relative paths for an import specifier (may be empty/ambiguous)."""
        ...


@dataclass
class LanguageRegistry:
    _adapters: list[LanguageAdapter] = field(default_factory=list)

    def register(self, adapter: LanguageAdapter) -> None:
        if any(existing.id == adapter.id for existing in self._adapters):
            return
        self._adapters.append(adapter)

    def all(self) -> list[LanguageAdapter]:
        return list(self._adapters)

    def for_path(self, relative_path: str, language: str | None = None) -> LanguageAdapter | None:
        for adapter in self._adapters:
            if adapter.can_parse(relative_path, language):
                return adapter
        return None

    def for_language(self, language: str) -> LanguageAdapter | None:
        key = language.lower()
        for adapter in self._adapters:
            if key in {item.lower() for item in adapter.languages}:
                return adapter
        return None


language_registry = LanguageRegistry()
