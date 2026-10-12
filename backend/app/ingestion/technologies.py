"""Technology rows cite one manifest line each."""

from __future__ import annotations

import json
import tomllib

from app.ingestion.assemble import TechnologyRecord


def technologies_for_manifest(relative_path: str, payload: bytes, file_id: str) -> list[TechnologyRecord]:
    name = relative_path.rsplit("/", 1)[-1]
    if name == "requirements.txt":
        return _requirements(payload, file_id)
    if name == "package.json":
        return _package_json(payload, file_id)
    if name == "pyproject.toml":
        return _pyproject(payload, file_id)
    return []


def _requirements(payload: bytes, file_id: str) -> list[TechnologyRecord]:
    rows = []
    for raw in payload.decode("utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        package = stripped.split(";", 1)[0].strip()
        for separator in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            package = package.split(separator, 1)[0]
        package = package.split("[", 1)[0].strip()
        if package:
            rows.append(TechnologyRecord(package, file_id, stripped))
    return rows


def _package_json(payload: bytes, file_id: str) -> list[TechnologyRecord]:
    try:
        document = json.loads(payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    if not isinstance(document, dict):
        return []
    rows = []
    lines = payload.decode("utf-8", errors="replace").splitlines()
    for section in ("dependencies", "devDependencies"):
        entries = document.get(section) or {}
        if not isinstance(entries, dict):
            continue
        for package, spec in entries.items():
            evidence = next((line.strip() for line in lines if f'"{package}"' in line), f"{package}: {spec}")
            rows.append(TechnologyRecord(str(package), file_id, evidence))
    return rows


def _pyproject(payload: bytes, file_id: str) -> list[TechnologyRecord]:
    try:
        document = tomllib.loads(payload.decode("utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        return []
    project = document.get("project") or {}
    rows = _requirement_rows(project.get("dependencies"), file_id)
    rows.extend(_group_rows(project.get("optional-dependencies"), file_id))
    rows.extend(_group_rows(document.get("dependency-groups"), file_id))
    poetry = ((document.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    if isinstance(poetry, dict):
        for package, spec in poetry.items():
            if str(package).lower() == "python":
                continue
            rows.append(TechnologyRecord(str(package), file_id, f"{package} = {spec}"))
    return rows


def _group_rows(groups: object, file_id: str) -> list[TechnologyRecord]:
    if not isinstance(groups, dict):
        return []
    rows: list[TechnologyRecord] = []
    for group, entries in groups.items():
        for row in _requirement_rows(entries, file_id):
            rows.append(TechnologyRecord(row.name, file_id, f"{group}: {row.evidence_text}"))
    return rows


def _requirement_rows(entries: object, file_id: str) -> list[TechnologyRecord]:
    if not isinstance(entries, list):
        return []
    rows = []
    for entry in entries:
        text = str(entry).strip()
        package = _package_name(text)
        if package and package.lower() != "python":
            rows.append(TechnologyRecord(package, file_id, text))
    return rows


def _package_name(text: str) -> str:
    package = text.split(";", 1)[0]
    for separator in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        package = package.split(separator, 1)[0]
    return package.split("[", 1)[0].strip().strip("'\"")
