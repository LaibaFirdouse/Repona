"""Pluggable language and framework intelligence."""

from app.ingestion.adapters.framework import FrameworkAdapter, FrameworkRegistry, framework_registry
from app.ingestion.adapters.language import LanguageAdapter, LanguageRegistry, language_registry
from app.ingestion.adapters import builtin  # noqa: F401 — register defaults

__all__ = [
    "FrameworkAdapter",
    "FrameworkRegistry",
    "LanguageAdapter",
    "LanguageRegistry",
    "framework_registry",
    "language_registry",
]
