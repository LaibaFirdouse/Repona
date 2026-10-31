"""Generic project/manifest discovery for module roots, aliases, and frameworks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath


_TSCONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
_MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "tsconfig.json",
        "jsconfig.json",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "composer.json",
        "Gemfile",
        "Package.swift",
        "pubspec.yaml",
        "Pipfile",
        "setup.py",
        "setup.cfg",
    }
)


@dataclass
class PathAliasRule:
    pattern: str
    targets: list[str]
    source_manifest: str


@dataclass
class ProjectContext:
    """Authoritative configuration discovered from indexed manifests."""

    manifests: dict[str, bytes] = field(default_factory=dict)
    path_alias_rules: list[PathAliasRule] = field(default_factory=list)
    package_names: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    workspace_roots: list[str] = field(default_factory=list)
    module_roots: list[str] = field(default_factory=list)
    languages_hinted: list[str] = field(default_factory=list)
    frameworks_hinted: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def alias_rule_tuples(self) -> list[tuple[str, list[str]]]:
        return [(rule.pattern, list(rule.targets)) for rule in self.path_alias_rules]


def discover_project_context(
    relative_paths: list[str] | set[str],
    payloads: dict[str, bytes] | None = None,
) -> ProjectContext:
    """Build project context from relative paths and optional file payloads."""
    payloads = payloads or {}
    context = ProjectContext()
    paths = sorted({path.replace("\\", "/") for path in relative_paths})
    for path in paths:
        name = PurePosixPath(path).name
        if (
            name in _MANIFEST_NAMES
            or name in _TSCONFIG_NAMES
            or name.lower().endswith(".csproj")
           ):
            if path in payloads:
                context.manifests[path] = payloads[path]
            elif name in payloads:
                context.manifests[path] = payloads[name]

    for path, payload in list(context.manifests.items()):
        name = PurePosixPath(path).name
        if name in _TSCONFIG_NAMES:
            manifest_dir = str(PurePosixPath(path).parent)
            if manifest_dir == ".":
                manifest_dir = ""
            for pattern, targets in _parse_tsconfig_paths(payload):
                rooted = [
                    f"{manifest_dir}/{target}".replace("//", "/").strip("/")
                    if manifest_dir
                    else target
                    for target in targets
                ]
                context.path_alias_rules.append(
                    PathAliasRule(pattern=pattern, targets=rooted, source_manifest=path)
                )
        elif name == "package.json":
            _ingest_package_json(context, path, payload)
        elif name == "pyproject.toml":
            _ingest_pyproject(context, payload)
        elif name == "requirements.txt":
            _ingest_requirements(context, payload)
        elif name == "go.mod":
            _ingest_go_mod(context, payload)
        elif name == "Cargo.toml":
            _ingest_cargo(context, payload)
        elif name.endswith("pom.xml"):
            context.languages_hinted.append("java")
            text = payload.decode("utf-8", errors="replace").lower()
            if "spring" in text:
                context.frameworks_hinted.append("spring_boot")
                context.dependencies.append("spring-boot")
        elif name.endswith(".csproj"):
            context.languages_hinted.append("csharp")
            text = payload.decode("utf-8", errors="replace").lower()
            if "aspnetcore" in text or "microsoft.aspnetcore" in text:
                context.frameworks_hinted.append("aspnet")
        elif "gradle" in name:
            context.languages_hinted.append("java")
            text = payload.decode("utf-8", errors="replace").lower()
            if "spring" in text:
                context.frameworks_hinted.append("spring_boot")

    # Derive module roots from aliases and common layouts present in the tree.
    path_set = set(paths)
    for root in ("src", "app", "lib", "backend", "frontend", "packages", "services"):
        if any(item == root or item.startswith(root + "/") for item in path_set):
            context.module_roots.append(root)
    context.languages_hinted = list(dict.fromkeys(context.languages_hinted))
    context.frameworks_hinted = list(dict.fromkeys(context.frameworks_hinted))
    context.module_roots = list(dict.fromkeys(context.module_roots))
    return context


def _parse_tsconfig_paths(payload: bytes) -> list[tuple[str, list[str]]]:
    try:
        text = payload.decode("utf-8", errors="replace")
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


def _ingest_package_json(context: ProjectContext, path: str, payload: bytes) -> None:
    try:
        data = json.loads(payload.decode("utf-8", errors="replace"))
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        context.package_names.append(name.strip())
    deps: list[str] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(key)
        if isinstance(block, dict):
            deps.extend(str(item) for item in block)
    context.dependencies.extend(deps)
    context.languages_hinted.append("javascript")
    if any(item.endswith((".ts", ".tsx")) or item == "typescript" for item in deps) or "typescript" in deps:
        context.languages_hinted.append("typescript")
    _hint_js_frameworks(context, deps)
    workspaces = data.get("workspaces")
    if isinstance(workspaces, list):
        context.workspace_roots.extend(str(item).rstrip("/*") for item in workspaces)
    elif isinstance(workspaces, dict) and isinstance(workspaces.get("packages"), list):
        context.workspace_roots.extend(str(item).rstrip("/*") for item in workspaces["packages"])
    del path


def _hint_js_frameworks(context: ProjectContext, deps: list[str]) -> None:
    lowered = {item.lower() for item in deps}
    mapping = {
        "next": "nextjs",
        "react": "react",
        "vue": "vue",
        "nuxt": "nuxt",
        "@angular/core": "angular",
        "svelte": "svelte",
        "express": "express",
        "@nestjs/core": "nestjs",
        "fastify": "fastify",
    }
    for package, framework in mapping.items():
        if package in lowered:
            context.frameworks_hinted.append(framework)


def _ingest_pyproject(context: ProjectContext, payload: bytes) -> None:
    text = payload.decode("utf-8", errors="replace").lower()
    context.languages_hinted.append("python")
    for framework, token in (
        ("fastapi", "fastapi"),
        ("django", "django"),
        ("flask", "flask"),
        ("sqlalchemy", "sqlalchemy"),
    ):
        if token in text:
            context.frameworks_hinted.append(framework)
            context.dependencies.append(token)


def _ingest_requirements(context: ProjectContext, payload: bytes) -> None:
    context.languages_hinted.append("python")
    for raw in payload.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[=<>!~\[]", line, maxsplit=1)[0].strip().lower()
        if name:
            context.dependencies.append(name)
        if name in {"fastapi", "django", "flask", "sqlalchemy"}:
            context.frameworks_hinted.append(name)


def _ingest_go_mod(context: ProjectContext, payload: bytes) -> None:
    context.languages_hinted.append("go")
    text = payload.decode("utf-8", errors="replace")
    for match in re.finditer(r"(?m)^module\s+(\S+)", text):
        context.package_names.append(match.group(1))
    if "gin-gonic/gin" in text:
        context.frameworks_hinted.append("gin")
    if "labstack/echo" in text:
        context.frameworks_hinted.append("echo")


def _ingest_cargo(context: ProjectContext, payload: bytes) -> None:
    context.languages_hinted.append("rust")
    text = payload.decode("utf-8", errors="replace").lower()
    if "axum" in text:
        context.frameworks_hinted.append("axum")
    if "actix-web" in text or "actix_web" in text:
        context.frameworks_hinted.append("actix")
