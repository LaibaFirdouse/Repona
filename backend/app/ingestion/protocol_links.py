"""Cross-language protocol correspondences (not verified CALLS).

Matching a frontend URL string to a backend route proves protocol-level
correspondence only. Resolution method is always PROTOCOL or HEURISTIC.
Ambiguous multi-handler matches are not emitted as verified protocol edges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.adapters.framework import DetectedRoute
from app.ingestion.ir import ResolutionMethod

_FETCH_URL = re.compile(
    r"""(?:fetch|axios\.(?:get|post|put|patch|delete)|httpx\.(?:get|post)|requests\.(?:get|post))\s*\(\s*(?P<q>[`'"])(?P<url>[^`'"]+)(?P=q)""",
    re.I,
)
_FETCH_TEMPLATE = re.compile(
    r"""(?:fetch|axios\.(?:get|post|put|patch|delete))\s*\(\s*`(?P<url>[^`]+)`""",
    re.I,
)
_STRING_PATH = re.compile(r"""[`'"](/[A-Za-z0-9_\-./{}]+)[`'"]""")
_ORIGIN = re.compile(r"^https?://[^/]+", re.I)
_PARAM_SEGMENT = re.compile(r"\{[^/]+\}|:[A-Za-z_][\w]*|\$\{[^}]+\}")


@dataclass(frozen=True)
class ProtocolLink:
    kind: str  # http_endpoint
    method: str
    path: str
    client_path: str
    server_path: str
    resolution: str
    evidence: str
    confidence: str = "verified_protocol"  # verified_protocol | inferred


def extract_client_http_paths(relative_path: str, text: str) -> list[tuple[str, str]]:
    """Return (method_hint, path) pairs from client-side call sites."""
    found: list[tuple[str, str]] = []
    for match in list(_FETCH_URL.finditer(text)) + list(_FETCH_TEMPLATE.finditer(text)):
        raw = match.group("url").split("?")[0]
        url = _strip_origin(raw)
        # Template interpolations become braces for matching.
        url = re.sub(r"\$\{[^}]+\}", "{param}", url)
        method = "GET"
        lowered = match.group(0).lower()
        for name in ("post", "put", "patch", "delete"):
            if name in lowered:
                method = name.upper()
                break
        # Also look at nearby method: "POST" in the same call expression window.
        window = text[match.start() : match.start() + 220]
        method_match = re.search(r"""method\s*:\s*['"](GET|POST|PUT|PATCH|DELETE)['"]""", window, re.I)
        if method_match:
            method = method_match.group(1).upper()
        if url.startswith("/"):
            found.append((method, url))
    if relative_path.endswith((".ts", ".tsx", ".js", ".jsx")):
        for match in _STRING_PATH.finditer(text):
            path = match.group(1).split("?")[0]
            if path.startswith("/api") or path.startswith("/v"):
                found.append(("*", path))
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for item in found:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def link_http_routes(
    client_hits: list[tuple[str, str, str]],
    server_routes: list[DetectedRoute],
) -> list[ProtocolLink]:
    """Link client (method, path, file) to server routes by normalised/templated path.

    Emits a protocol edge only when exactly one server route matches.
    """
    links: list[ProtocolLink] = []
    seen: set[tuple[str, str, str, str]] = set()
    for method, path, client_file in client_hits:
        key = _normalise_path(path)
        matches = [route for route in server_routes if _paths_compatible(key, route.path)]
        # Prefer exact path equality over template matches when both exist.
        exact = [route for route in matches if _normalise_path(route.path) == key]
        if exact:
            matches = exact
        # Wildcard client methods must still uniquely identify a server route+method.
        method_filtered = [
            route
            for route in matches
            if method in {"*", route.method} or route.method in {"*", method} or method == route.method
        ]
        if len(method_filtered) != 1:
            continue
        server = method_filtered[0]
        resolved_method = server.method if server.method != "*" else method
        if resolved_method == "*":
            continue
        dedupe = (client_file, resolved_method, key, server.relative_path)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        links.append(
            ProtocolLink(
                kind="http_endpoint",
                method=resolved_method,
                path=key,
                client_path=client_file,
                server_path=server.relative_path,
                resolution=ResolutionMethod.PROTOCOL.value,
                evidence=f"{client_file} -> {server.relative_path} via {key} ~ {server.path}",
                confidence="verified_protocol",
            )
        )
    return links


def _strip_origin(url: str) -> str:
    cleaned = url.strip()
    cleaned = _ORIGIN.sub("", cleaned)
    # Template base URLs: `${API_BASE}/api/...` → `/api/...`
    if not cleaned.startswith("/"):
        embedded = re.search(r"(/[A-Za-z0-9_\-./{}]+(?:\$\{[^}]+\})?[A-Za-z0-9_\-./{}]*)", cleaned)
        if embedded:
            cleaned = embedded.group(1)
    cleaned = re.sub(r"\$\{[^}]+\}", "{param}", cleaned)
    if not cleaned.startswith("/"):
        if cleaned.startswith("api/") or cleaned.startswith("v"):
            cleaned = "/" + cleaned
    return cleaned


def _normalise_path(path: str) -> str:
    cleaned = path.strip().split("?")[0]
    cleaned = _strip_origin(cleaned)
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    return cleaned.rstrip("/") or "/"


def _paths_compatible(client_path: str, server_path: str) -> bool:
    client = _normalise_path(client_path)
    server = _normalise_path(server_path)
    if client == server:
        return True
    client_rx = _path_to_regex(client)
    server_rx = _path_to_regex(server)
    if client_rx.match(server) or server_rx.match(client):
        return True
    # Segment-wise: treat param-like segments as wildcards on both sides.
    c_parts = [part for part in client.strip("/").split("/") if part]
    s_parts = [part for part in server.strip("/").split("/") if part]
    if len(c_parts) != len(s_parts):
        return False
    for left, right in zip(c_parts, s_parts):
        if left == right:
            continue
        if _PARAM_SEGMENT.fullmatch(left) or _PARAM_SEGMENT.fullmatch(right):
            continue
        if left.startswith("{") or right.startswith("{"):
            continue
        # Concrete dynamic client segment (numeric id) vs server template already handled;
        # allow opaque single-segment mismatch only when the other side is a template.
        if _looks_concrete_id(left) and _is_template_segment(right):
            continue
        if _looks_concrete_id(right) and _is_template_segment(left):
            continue
        return False
    return True


def _path_to_regex(path: str) -> re.Pattern[str]:
    parts = []
    for segment in _normalise_path(path).split("/"):
        if not segment:
            continue
        if _is_template_segment(segment) or _PARAM_SEGMENT.fullmatch(segment):
            parts.append(r"[^/]+")
        else:
            parts.append(re.escape(segment))
    return re.compile("^/" + "/".join(parts) + "$")


def _is_template_segment(segment: str) -> bool:
    return bool(re.fullmatch(r"\{[^/]+\}|:[A-Za-z_][\w]*|\$\{[^}]+\}|\{param\}", segment))


def _looks_concrete_id(segment: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F-]{1,64}", segment))
