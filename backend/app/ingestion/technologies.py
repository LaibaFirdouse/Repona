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
    rows = []
    project = document.get("project") or {}
    for entry in project.get("dependencies") or []:
        text = str(entry).strip()
        package = text.split(";", 1)[0]
        for separator in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            package = package.split(separator, 1)[0]
        package = package.split("[", 1)[0].strip().strip("'\"")
        if package:
            rows.append(TechnologyRecord(package, file_id, text))
    poetry = ((document.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    if isinstance(poetry, dict):
        for package, spec in poetry.items():
            if package == "python":
                continue
            rows.append(TechnologyRecord(str(package), file_id, f"{package} = {spec}"))
    return rows
