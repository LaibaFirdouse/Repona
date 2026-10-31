"""FrameworkAdapter protocol and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.ingestion.ir import CapabilityLevel, FrameworkCapability
from app.ingestion.project_context import ProjectContext


@dataclass(frozen=True)
class DetectedRoute:
    method: str
    path: str
    handler_name: str
    relative_path: str
    evidence: str
    resolution: str = "heuristic"


@runtime_checkable
class FrameworkAdapter(Protocol):
    id: str
    display_name: str

    def detect(self, context: ProjectContext, relative_paths: list[str]) -> FrameworkCapability: ...

    def discover_routes(
        self,
        context: ProjectContext,
        *,
        relative_paths: list[str],
        file_texts: dict[str, str] | None = None,
    ) -> list[DetectedRoute]:
        ...

    def capability_level(self) -> CapabilityLevel: ...


@dataclass
class FrameworkRegistry:
    _adapters: list[FrameworkAdapter] = field(default_factory=list)

    def register(self, adapter: FrameworkAdapter) -> None:
        if any(existing.id == adapter.id for existing in self._adapters):
            return
        self._adapters.append(adapter)

    def all(self) -> list[FrameworkAdapter]:
        return list(self._adapters)

    def detect_all(self, context: ProjectContext, relative_paths: list[str]) -> list[FrameworkCapability]:
        return [adapter.detect(context, relative_paths) for adapter in self._adapters]


framework_registry = FrameworkRegistry()
