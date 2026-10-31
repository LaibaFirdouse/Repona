"""Built-in language and framework adapters registered at import time."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from app.ingestion.adapters.framework import DetectedRoute, FrameworkRegistry, framework_registry
from app.ingestion.adapters.language import LanguageRegistry, language_registry
from app.ingestion.ir import CapabilityLevel, FrameworkCapability, IrDocument, parsed_file_to_ir
from app.ingestion.parse import LANGUAGE_BY_EXTENSION, ParsedFile, language_for_path, parse_file
from app.ingestion.project_context import ProjectContext


class TreeSitterLanguageAdapter:
    """Wraps the existing tree-sitter parse_file pipeline for Py/JS/TS."""

    def __init__(
        self,
        adapter_id: str,
        languages: tuple[str, ...],
        extensions: tuple[str, ...],
        level: CapabilityLevel = CapabilityLevel.SEMANTIC_RELATIONSHIP,
    ) -> None:
        self.id = adapter_id
        self.languages = languages
        self.extensions = extensions
        self._level = level

    def can_parse(self, relative_path: str, language: str | None) -> bool:
        if language and language.lower() in {item.lower() for item in self.languages}:
            return True
        lower = relative_path.lower()
        return any(lower.endswith(ext) for ext in self.extensions)

    def parse(self, relative_path: str, payload: bytes) -> ParsedFile:
        return parse_file(relative_path, payload)

    def parse_ir(self, relative_path: str, payload: bytes) -> IrDocument:
        parsed = self.parse(relative_path, payload)
        return parsed_file_to_ir(
            relative_path,
            parsed,
            adapter_id=self.id,
        )

    def capability_level(self) -> CapabilityLevel:
        return self._level

    def resolve_import_targets(
        self,
        source_path: str,
        specifier: str,
        paths: set[str],
        *,
        alias_rules: list[tuple[str, list[str]]] | None = None,
    ) -> list[str]:
        from app.ingestion.assemble import _import_targets

        return _import_targets(source_path, specifier, paths, alias_rules=alias_rules)


class LanguagePackAdapter:
    """Generic tree-sitter-language-pack coverage (syntax + structural, not full CALLS)."""

    def __init__(self, adapter_id: str, languages: tuple[str, ...], extensions: tuple[str, ...]) -> None:
        self.id = adapter_id
        self.languages = languages
        self.extensions = extensions

    def can_parse(self, relative_path: str, language: str | None) -> bool:
        if language and language.lower() in {item.lower() for item in self.languages}:
            return True
        lower = relative_path.lower()
        return any(lower.endswith(ext) for ext in self.extensions)

    def parse(self, relative_path: str, payload: bytes) -> ParsedFile:
        return parse_file(relative_path, payload)

    def parse_ir(self, relative_path: str, payload: bytes) -> IrDocument:
        parsed = self.parse(relative_path, payload)
        return parsed_file_to_ir(
            relative_path,
            parsed,
            adapter_id=self.id,
        )

    def capability_level(self) -> CapabilityLevel:
        return CapabilityLevel.STRUCTURAL_IMPORT

    def resolve_import_targets(
        self,
        source_path: str,
        specifier: str,
        paths: set[str],
        *,
        alias_rules: list[tuple[str, list[str]]] | None = None,
    ) -> list[str]:
        del alias_rules
        # Conservative: only relative paths for pack languages until dedicated resolvers exist.
        if specifier.startswith("./") or specifier.startswith("../"):
            from app.ingestion.assemble import _relative_targets

            return _relative_targets(source_path, specifier, paths)
        return []


class SourceOnlyAdapter:
    """LEVEL 0 fallback: files remain discoverable without semantic claims."""

    id = "source_only"
    languages = ("unknown",)
    extensions: tuple[str, ...] = ()

    def can_parse(self, relative_path: str, language: str | None) -> bool:
        # Registered after specialized adapters; catches everything else.
        del relative_path, language
        return True

    def parse(self, relative_path: str, payload: bytes) -> ParsedFile:
        return parse_file(relative_path, payload)

    def parse_ir(self, relative_path: str, payload: bytes) -> IrDocument:
        parsed = self.parse(relative_path, payload)
        return parsed_file_to_ir(
            relative_path,
            parsed,
            adapter_id=self.id,
        )

    def capability_level(self) -> CapabilityLevel:
        return CapabilityLevel.SOURCE_DISCOVERABLE

    def resolve_import_targets(
        self,
        source_path: str,
        specifier: str,
        paths: set[str],
        *,
        alias_rules: list[tuple[str, list[str]]] | None = None,
    ) -> list[str]:
        del source_path, specifier, paths, alias_rules
        return []


_FASTAPI_DECORATOR = re.compile(
    r"@(?P<receiver>app|router|[A-Za-z_][\w]*)\.(get|post|put|patch|delete)\(\s*[`'\"](?P<path>[^`'\"]*)[`'\"]",
    re.I,
)
_FASTAPI_ROUTER_PREFIX = re.compile(
    r"""(?P<name>[A-Za-z_][\w]*)\s*=\s*APIRouter\s*\([^)]*prefix\s*=\s*[`'\"](?P<prefix>[^`'\"]+)[`'\"]""",
    re.I,
)
_EXPRESS_ROUTE = re.compile(
    r"(?:app|router)\.(get|post|put|patch|delete)\(\s*[`'\"]([^`'\"]+)[`'\"]",
    re.I,
)
_NEXT_ROUTE_FILE = re.compile(
    r"(?:^|/)(?:app/)?api/.+/route\.(?:ts|tsx|js|jsx)$|(?:^|/)pages/api/.+\.(?:ts|tsx|js|jsx)$",
    re.I,
)
_NEXT_ROUTE_METHOD_EXPORT = re.compile(
    r"(?m)^[ \t]*export\s+(?:async\s+)?(?:"
    r"function\s+(?P<function>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b"
    r"|(?:const|let|var)\s+"
    r"(?P<variable>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b"
    r"(?:\s*:[^=\n]+)?\s*="
    r")"
)
_SPRING_MAPPING = re.compile(
    r"@(?P<annotation>Get|Post|Put|Patch|Delete|Request)Mapping"
    r"\s*\((?P<arguments>[^)]*)\)",
    re.I,
)
_GO_HANDLE = re.compile(
    r"(?:HandleFunc|Handle|Methods\([^\)]*\)\.Path)\(\s*[\"']([^\"']+)[\"']",
    re.I,
)


class FastAPIFrameworkAdapter:
    id = "fastapi"
    display_name = "FastAPI"

    def detect(self, context: ProjectContext, relative_paths: list[str]) -> FrameworkCapability:
        evidence: list[str] = []
        if "fastapi" in {item.lower() for item in context.dependencies} or "fastapi" in context.frameworks_hinted:
            evidence.append("dependency:fastapi")
        if any(path.endswith(".py") and ("router" in path.lower() or path.endswith("main.py")) for path in relative_paths):
            evidence.append("python_route_layout")
        detected = bool(evidence) and (
            "fastapi" in context.frameworks_hinted
            or "fastapi" in {item.lower() for item in context.dependencies}
            or any(path.endswith("requirements.txt") or path.endswith("pyproject.toml") for path in relative_paths)
        )
        # Prefer explicit dependency evidence; layout alone is not enough.
        detected = "fastapi" in context.frameworks_hinted or "fastapi" in {
            item.lower() for item in context.dependencies
        }
        return FrameworkCapability(
            framework_id=self.id,
            display_name=self.display_name,
            level=CapabilityLevel.FRAMEWORK_EXECUTION if detected else CapabilityLevel.SOURCE_DISCOVERABLE,
            detected=detected,
            route_discovery=detected,
            persistence_discovery=False,
            adapter_id=self.id,
            evidence=tuple(evidence) if detected else (),
        )



    def discover_routes(
        self,
        context: ProjectContext,
        *,
        relative_paths: list[str],
        file_texts: dict[str, str] | None = None,
    ) -> list[DetectedRoute]:
        del context
        file_texts = file_texts or {}
        routes: list[DetectedRoute] = []

        for path in relative_paths:
            if not path.endswith(".py"):
                continue

            text = file_texts.get(path, "")
            prefixes = {
                match.group("name"): match.group("prefix").rstrip("/")
                for match in _FASTAPI_ROUTER_PREFIX.finditer(text)
            }

            for match in _FASTAPI_DECORATOR.finditer(text):
                receiver = match.group("receiver")
                route_path = match.group("path") or ""
                prefix = prefixes.get(receiver, "")

                if receiver == "app":
                    prefix = ""

                full = f"{prefix}/{route_path.lstrip('/')}".replace("//", "/")
                if not full.startswith("/"):
                    full = "/" + full

                full = full.rstrip("/") or "/"

                routes.append(
                    DetectedRoute(
                        method=match.group(2).upper(),
                        path=full,
                        handler_name="",
                        relative_path=path,
                        evidence=match.group(0)[:120],
                        resolution="config",
                    )
                )

        return routes

    def capability_level(self) -> CapabilityLevel:
        return CapabilityLevel.FRAMEWORK_EXECUTION


class NextJsFrameworkAdapter:
    id = "nextjs"
    display_name = "Next.js"

    def detect(self, context: ProjectContext, relative_paths: list[str]) -> FrameworkCapability:
        detected = "nextjs" in context.frameworks_hinted or "next" in {
            item.lower() for item in context.dependencies
        }
        evidence = ("dependency:next",) if detected else ()
        if any(_NEXT_ROUTE_FILE.search(path.replace("\\", "/")) for path in relative_paths):
            if not detected:
                # Route file layout alone is a weak signal — report as detected only with package.json next.
                pass
            else:
                evidence = evidence + ("app_router_files",)
        return FrameworkCapability(
            framework_id=self.id,
            display_name=self.display_name,
            level=CapabilityLevel.FRAMEWORK_EXECUTION if detected else CapabilityLevel.SOURCE_DISCOVERABLE,
            detected=detected,
            route_discovery=detected,
            persistence_discovery=False,
            adapter_id=self.id,
            evidence=evidence,
        )


    def discover_routes(
        self,
        context: ProjectContext,
        *,
        relative_paths: list[str],
        file_texts: dict[str, str] | None = None,
    ) -> list[DetectedRoute]:
        del context
        file_texts = file_texts or {}
        routes: list[DetectedRoute] = []

        for path in relative_paths:
            norm = path.replace("\\", "/")

            if not _NEXT_ROUTE_FILE.search(norm):
                continue

            marker = "/api/"
            if marker not in norm:
                continue

            after = norm.split(marker, 1)[1]
            route_file = PurePosixPath(after)

            if route_file.name.lower().startswith("route."):
                route_dir = str(route_file.parent)
                api_path = "/api" if route_dir == "." else f"/api/{route_dir}"
            else:
                # Preserve nested pages/api paths.
                api_path = "/api/" + str(route_file.with_suffix(""))

            text = file_texts.get(path, "")

            methods = {
                match.group("function") or match.group("variable")
                for match in _NEXT_ROUTE_METHOD_EXPORT.finditer(text)
            }

            # Never invent methods when source exports cannot be verified.
            for method in sorted(methods):
                routes.append(
                    DetectedRoute(
                        method=method,
                        path=api_path,
                        handler_name=method,
                        relative_path=path,
                        evidence=f"exported {method} handler in {path}",
                        resolution="static",
                    )
                )

        return routes

    def capability_level(self) -> CapabilityLevel:
        return CapabilityLevel.FRAMEWORK_EXECUTION


class ExpressFrameworkAdapter:
    id = "express"
    display_name = "Express"

    def detect(self, context: ProjectContext, relative_paths: list[str]) -> FrameworkCapability:
        del relative_paths
        detected = "express" in context.frameworks_hinted or "express" in {
            item.lower() for item in context.dependencies
        }
        return FrameworkCapability(
            framework_id=self.id,
            display_name=self.display_name,
            level=CapabilityLevel.FRAMEWORK_EXECUTION if detected else CapabilityLevel.SOURCE_DISCOVERABLE,
            detected=detected,
            route_discovery=detected,
            persistence_discovery=False,
            adapter_id=self.id,
            evidence=("dependency:express",) if detected else (),
        )

    def discover_routes(
        self,
        context: ProjectContext,
        *,
        relative_paths: list[str],
        file_texts: dict[str, str] | None = None,
    ) -> list[DetectedRoute]:
        del context
        file_texts = file_texts or {}
        routes: list[DetectedRoute] = []
        for path in relative_paths:
            if not path.endswith((".js", ".ts", ".mjs", ".cjs")):
                continue
            text = file_texts.get(path, "")
            for match in _EXPRESS_ROUTE.finditer(text):
                routes.append(
                    DetectedRoute(
                        method=match.group(1).upper(),
                        path=match.group(2),
                        handler_name="",
                        relative_path=path,
                        evidence=match.group(0)[:120],
                        resolution="heuristic",
                    )
                )
        return routes

    def capability_level(self) -> CapabilityLevel:
        return CapabilityLevel.FRAMEWORK_EXECUTION


class SpringBootFrameworkAdapter:
    id = "spring_boot"
    display_name = "Spring Boot"

    def detect(self, context: ProjectContext, relative_paths: list[str]) -> FrameworkCapability:
        has_pom = any(
            path.endswith("pom.xml") or path.endswith("build.gradle") or path.endswith("build.gradle.kts")
            for path in relative_paths
        )
        java_files = any(path.endswith(".java") for path in relative_paths)
        hinted = "spring_boot" in context.frameworks_hinted or any(
            "spring" in dep.lower() for dep in context.dependencies
        )
        controllers = any(path.endswith("Controller.java") for path in relative_paths)
        detected = bool(java_files and (hinted or (has_pom and controllers)))
        evidence: list[str] = []
        if hinted:
            evidence.append("manifest:spring")
        if controllers:
            evidence.append("java_controller_layout")
        return FrameworkCapability(
            framework_id=self.id,
            display_name=self.display_name,
            level=CapabilityLevel.FRAMEWORK_EXECUTION if detected else CapabilityLevel.SOURCE_DISCOVERABLE,
            detected=detected,
            route_discovery=detected,
            persistence_discovery=False,
            adapter_id=self.id,
            evidence=tuple(evidence),
        )


    def discover_routes(
        self,
        context: ProjectContext,
        *,
        relative_paths: list[str],
        file_texts: dict[str, str] | None = None,
    ) -> list[DetectedRoute]:
        del context
        file_texts = file_texts or {}
        routes: list[DetectedRoute] = []

        annotation_methods = {
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "patch": "PATCH",
            "delete": "DELETE",
        }
        supported_methods = "GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE"

        for path in relative_paths:
            if not path.endswith(".java"):
                continue

            text = file_texts.get(path, "")
            for match in _SPRING_MAPPING.finditer(text):
                annotation = match.group("annotation").lower()
                arguments = match.group("arguments")

                path_match = re.search(
                    r"\b(?:value|path)\s*=\s*(?:\{\s*)?['\"]([^'\"]+)['\"]",
                    arguments,
                )
                if path_match is None:
                    path_match = re.search(
                        r"^\s*['\"]([^'\"]+)['\"]",
                        arguments,
                    )
                if path_match is None:
                    continue

                route_path = path_match.group(1)

                if annotation != "request":
                    methods = [annotation_methods[annotation]]
                elif re.search(r"\bmethod\s*=", arguments):
                    methods = [
                        method.upper()
                        for method in re.findall(
                            rf"\bRequestMethod\.({supported_methods})\b",
                            arguments,
                            re.I,
                        )
                    ]
                    if not methods:
                        continue
                else:
                    # Bare @RequestMapping does not constrain the method.
                    methods = ["*"]

                for method in methods:
                    routes.append(
                        DetectedRoute(
                            method=method,
                            path=route_path,
                            handler_name="",
                            relative_path=path,
                            evidence=match.group(0)[:120],
                            resolution="heuristic",
                        )
                    )

        return routes

    def capability_level(self) -> CapabilityLevel:
        return CapabilityLevel.FRAMEWORK_EXECUTION


class GoNetHttpFrameworkAdapter:
    id = "go_net_http"
    display_name = "Go net/http"

    def detect(self, context: ProjectContext, relative_paths: list[str]) -> FrameworkCapability:
        detected = "go" in context.languages_hinted and any(path.endswith(".go") for path in relative_paths)
        return FrameworkCapability(
            framework_id=self.id,
            display_name=self.display_name,
            level=CapabilityLevel.STRUCTURAL_IMPORT if detected else CapabilityLevel.SOURCE_DISCOVERABLE,
            detected=detected,
            route_discovery=detected,
            persistence_discovery=False,
            adapter_id=self.id,
            evidence=("go.mod",) if detected else (),
        )


    def discover_routes(
        self,
        context: ProjectContext,
        *,
        relative_paths: list[str],
        file_texts: dict[str, str] | None = None,
    ) -> list[DetectedRoute]:
        del context
        file_texts = file_texts or {}
        routes: list[DetectedRoute] = []

        supported_methods = {
            "GET", "POST", "PUT", "PATCH", "DELETE",
            "HEAD", "OPTIONS", "TRACE", "CONNECT",
        }

        for path in relative_paths:
            if not path.endswith(".go"):
                continue

            text = file_texts.get(path, "")
            for match in _GO_HANDLE.finditer(text):
                declaration = match.group(0)
                route_pattern = match.group(1).strip()

                # Gorilla Mux: Methods("GET").Path("/items")
                gorilla_methods = re.search(
                    r"\bMethods\s*\((?P<methods>[^)]*)\)",
                    declaration,
                    re.I,
                )

                if gorilla_methods:
                    methods = [
                        method.upper()
                        for method in re.findall(
                            r"""['"]([A-Z]+)['"]""",
                            gorilla_methods.group("methods"),
                            re.I,
                        )
                        if method.upper() in supported_methods
                    ]
                    method_paths = [
                        (method, route_pattern) for method in methods
                    ] or [("*", route_pattern)]
                else:
                    # Go 1.22 ServeMux: "GET /items".
                    parts = route_pattern.split(maxsplit=1)
                    if (
                        len(parts) == 2
                        and parts[0].upper() in supported_methods
                    ):
                        method_paths = [(parts[0].upper(), parts[1])]
                    else:
                        # HandleFunc("/items", ...) is method-agnostic.
                        method_paths = [("*", route_pattern)]

                for method, route_path in method_paths:
                    routes.append(
                        DetectedRoute(
                            method=method,
                            path=route_path,
                            handler_name="",
                            relative_path=path,
                            evidence=declaration[:120],
                            resolution="heuristic",
                        )
                    )

        return routes

    def capability_level(self) -> CapabilityLevel:
        return CapabilityLevel.STRUCTURAL_IMPORT


def register_builtin_adapters(
    languages: LanguageRegistry | None = None,
    frameworks: FrameworkRegistry | None = None,
) -> None:
    languages = languages or language_registry
    frameworks = frameworks or framework_registry

    languages.register(
        TreeSitterLanguageAdapter(
            "python_treesitter",
            ("python",),
            (".py",),
            CapabilityLevel.SEMANTIC_RELATIONSHIP,
        )
    )
    languages.register(
        TreeSitterLanguageAdapter(
            "javascript_treesitter",
            ("javascript",),
            (".js", ".jsx", ".mjs", ".cjs"),
            CapabilityLevel.SEMANTIC_RELATIONSHIP,
        )
    )
    languages.register(
        TreeSitterLanguageAdapter(
            "typescript_treesitter",
            ("typescript", "tsx"),
            (".ts", ".tsx"),
            CapabilityLevel.SEMANTIC_RELATIONSHIP,
        )
    )
    languages.register(
        LanguagePackAdapter(
            "go_language_pack",
            ("go",),
            (".go",),
        )
    )
    languages.register(
        LanguagePackAdapter(
            "rust_language_pack",
            ("rust",),
            (".rs",),
        )
    )
    languages.register(
        LanguagePackAdapter(
            "java_language_pack",
            ("java",),
            (".java",),
        )
    )
    languages.register(
        LanguagePackAdapter(
            "csharp_language_pack",
            ("c_sharp", "csharp"),
            (".cs",),
        )
    )
    languages.register(SourceOnlyAdapter())

    frameworks.register(FastAPIFrameworkAdapter())
    frameworks.register(NextJsFrameworkAdapter())
    frameworks.register(ExpressFrameworkAdapter())
    frameworks.register(SpringBootFrameworkAdapter())
    frameworks.register(GoNetHttpFrameworkAdapter())


register_builtin_adapters()
