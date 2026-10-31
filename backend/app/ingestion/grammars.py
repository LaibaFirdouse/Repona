"""Declarations and calls for languages other than Python and JavaScript.

Python, JavaScript, and TypeScript keep their own walk. Every other programming
language uses a tree-sitter grammar when one exists. Config and prose files stay
unparsed. A language with no grammar is stored as source text.
"""

from __future__ import annotations

import re

from tree_sitter_language_pack import (
    DownloadError,
    LanguageNotFoundError,
    ProcessConfig,
    detect_language_from_extension,
    get_parser,
    has_language,
    process,
)

from app.ingestion.inclusion import INCLUDED_FILENAMES, LANGUAGE_BY_EXTENSION
from app.ingestion.parse import (
    ImportBinding,
    ParsedFile,
    ParserFailure,
    RawMention,
    RawSymbol,
    _contiguous_import_spans,
    _decode,
    _go_import_bindings,
    _signature,
    _span_for,
    module_qualified_name,
)

# These are indexed, but they are not programming languages with declarations.
_UNPARSED = {"markdown", "json", "yaml", "toml", "html", "css", "scss", "sass", "less", "jupyter"}

_KIND = {
    "function": "function",
    "method": "method",
    "class": "class",
    "struct": "class",
    "interface": "interface",
    "enum": "enum",
    "trait": "interface",
    "impl": "class",
    "type": "type",
    "constant": "constant",
}

_CALL_TYPES = {
    "call_expression",
    "method_invocation",
    "function_call_expression",
    "invocation_expression",
    "macro_invocation",
    "call",
    "command",
}
_SIMPLE_CALL = re.compile(rb"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# Elixir and similar grammars use a call node for the declaration itself.
_SKIP_CALLEES = {
    "alias",
    "def",
    "defdelegate",
    "defguard",
    "defguardp",
    "defimpl",
    "defmacro",
    "defmacrop",
    "defmodule",
    "defp",
    "defprotocol",
    "defstruct",
    "fn",
    "import",
    "quote",
    "require",
    "unquote",
    "use",
}


class _ByteSpan:
    def __init__(self, start: int, end: int) -> None:
        self.start_byte = start
        self.end_byte = end


def parse_other(relative_path: str, payload: bytes) -> ParsedFile:
    grammar = _grammar_for(relative_path)
    language = _catalog_language(relative_path) or grammar
    if grammar is None:
        return ParsedFile(language=None, parse_status="not_parsed", parse_message=None)
    if not payload:
        qualified = module_qualified_name(relative_path)
        return ParsedFile(
            language=language,
            parse_status="parsed",
            parse_message=None,
            symbols=[
                RawSymbol(
                    kind="module",
                    name=qualified.rsplit(".", 1)[-1],
                    qualified_name=qualified,
                    signature="",
                    start_byte=0,
                    end_byte=0,
                    parent_index=None,
                )
            ],
        )
    try:
        tree = get_parser(grammar).parse(payload)
        result = process(
            payload.decode("utf-8", errors="replace"),
            ProcessConfig(language=grammar, structure=True, imports=True, symbols=True),
        )
    except (DownloadError, LanguageNotFoundError):
        return ParsedFile(
            language=language,
            parse_status="not_parsed",
            parse_message="grammar unavailable",
        )
    except Exception as exc:
        # Cache/lock/download failures must not abort the whole generation — keep source discoverable.
        name = type(exc).__name__
        if "Cache" in name or "Download" in name or "Lock" in name:
            return ParsedFile(
                language=language,
                parse_status="not_parsed",
                parse_message=f"grammar unavailable: {name}",
            )
        raise ParserFailure(f"parser failed for {relative_path}") from exc
    if tree is None:
        raise ParserFailure(f"parser returned no tree for {relative_path}")

    symbols = _declarations(result, payload, tree.root_node)
    if not symbols:
        symbols = _declarations_from_tree(tree.root_node, payload)
    mentions = _calls(tree.root_node, payload, symbols)
    import_nodes = []
    for item in result.imports:
        if item.span is None or not item.source.strip():
            continue
        text = item.source.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            text = text[1:-1]
        if not text:
            continue
        mentions.append(
            RawMention(
                relationship="IMPORTS",
                mentioned_name=text,
                start_byte=item.span.start_byte,
                end_byte=item.span.end_byte,
                source_symbol_index=None,
            )
        )
        import_nodes.append(_ByteSpan(item.span.start_byte, item.span.end_byte))
    import_spans = _contiguous_import_spans(payload, import_nodes)
    for mention in mentions:
        if mention.relationship == "IMPORTS":
            mention.import_span = _span_for(import_spans, mention.start_byte)
    bindings: list[ImportBinding] = []
    if language == "go":
        bindings = _go_bindings_from_tree(tree.root_node, payload)
    parsed = ParsedFile(
        language=language,
        parse_status="parsed",
        parse_message=None,
        symbols=symbols,
        mentions=mentions,
        import_spans=import_spans,
        import_bindings=bindings,
    )
    if tree.root_node.has_error:
        parsed.parse_status = "syntax_error"
        parsed.parse_message = "syntax error"
        if not parsed.symbols:
            parsed.mentions = []
            parsed.import_spans = []
        else:
            parsed.mentions = [
                mention
                for mention in parsed.mentions
                if mention.relationship == "IMPORTS" or mention.source_symbol_index is not None
            ]
        return parsed
    qualified = module_qualified_name(relative_path)
    parsed.symbols.append(
        RawSymbol(
            kind="module",
            name=qualified.rsplit(".", 1)[-1],
            qualified_name=qualified,
            signature="",
            start_byte=0,
            end_byte=len(payload),
            parent_index=None,
        )
    )
    return parsed


def _go_bindings_from_tree(root, payload: bytes) -> list[ImportBinding]:
    bindings: list[ImportBinding] = []

    def visit(node) -> None:
        if node.type == "import_declaration":
            bindings.extend(_go_import_bindings(payload, node))
            return
        for child in node.children:
            visit(child)

    visit(root)
    return bindings


def _grammar_for(relative_path: str) -> str | None:
    name = relative_path.rsplit("/", 1)[-1]
    if name in INCLUDED_FILENAMES:
        return None
    catalog = _catalog_language(relative_path)
    if catalog is None or catalog in _UNPARSED:
        return None
    extension = _extension(relative_path)
    if extension is None:
        return None
    detected = detect_language_from_extension(extension[1:])
    if not detected or detected in {"python", "javascript", "typescript", "tsx"} or not has_language(detected):
        return None
    return detected


def _catalog_language(relative_path: str) -> str | None:
    extension = _extension(relative_path)
    if extension is None:
        return None
    return LANGUAGE_BY_EXTENSION.get(extension)


def _extension(relative_path: str) -> str | None:
    name = relative_path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0:
        return None
    return name[dot:].lower()


def _declarations(result, payload: bytes, root) -> list[RawSymbol]:
    symbols: list[RawSymbol] = []
    seen: set[tuple[int, int, str]] = set()

    def add(kind: str, name: str | None, start: int, end: int, parent: int | None, signature: str, prefix: str | None = None) -> int | None:
        if not name or (start, end, name) in seen:
            return parent
        seen.add((start, end, name))
        node = _node_covering(root, start, end)
        if node is not None and not signature:
            signature = _signature(payload, node)
        qualified = name
        if parent is not None:
            qualified = f"{symbols[parent].qualified_name}.{name}"
        elif prefix:
            qualified = f"{prefix}.{name}"
        elif kind == "method" and node is not None:
            receiver = _receiver_type(node, payload)
            if receiver:
                qualified = f"{receiver}.{name}"
        symbols.append(
            RawSymbol(
                kind=kind,
                name=name,
                qualified_name=qualified,
                signature=signature,
                start_byte=start,
                end_byte=end,
                parent_index=parent,
            )
        )
        return len(symbols) - 1

    def walk(items, parent: int | None, prefix: str | None = None) -> None:
        for item in items:
            raw = str(item.kind).rsplit(".", 1)[-1].strip().lower()
            if raw == "impl":
                walk(item.children, parent, item.name or prefix)
                continue
            kind = _KIND.get(raw)
            span = item.span
            index = parent
            if kind and span is not None and item.name:
                added = add(kind, item.name, span.start_byte, span.end_byte, parent, "", prefix)
                if added is not None:
                    index = added
            walk(item.children, index)

    walk(result.structure, None)
    for info in result.symbols:
        kind = _KIND.get(str(info.kind).rsplit(".", 1)[-1].strip().lower())
        if not kind or info.span is None or not info.name:
            continue
        add(kind, info.name, info.span.start_byte, info.span.end_byte, None, "")
    return symbols


def _declarations_from_tree(root, payload: bytes) -> list[RawSymbol]:
    found: list[RawSymbol] = []

    def visit(node) -> None:
        kind = _kind_from_type(node.type)
        if kind is not None:
            name = _node_name(node, payload)
            if name:
                found.append(
                    RawSymbol(
                        kind=kind,
                        name=name,
                        qualified_name=name,
                        signature=_signature(payload, node),
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        parent_index=None,
                    )
                )
        for child in node.children:
            visit(child)

    visit(root)
    return _nest(found)


def _calls(root, payload: bytes, symbols: list[RawSymbol]) -> list[RawMention]:
    mentions: list[RawMention] = []

    def visit(node) -> None:
        if node.type in _CALL_TYPES or node.type.endswith("call_expression"):
            callee = _callee(node, payload)
            if callee and callee not in _SKIP_CALLEES:
                mentions.append(
                    RawMention(
                        relationship="CALLS",
                        mentioned_name=callee,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        source_symbol_index=_enclosing(symbols, node.start_byte),
                    )
                )
        elif not node.children:
            match = _SIMPLE_CALL.match(payload[node.start_byte : node.end_byte])
            if match is not None:
                mentions.append(
                    RawMention(
                        relationship="CALLS",
                        mentioned_name=match.group(1).decode("utf-8"),
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        source_symbol_index=_enclosing(symbols, node.start_byte),
                    )
                )
        else:
            callee = _argument_callee(node, payload)
            if callee and callee not in _SKIP_CALLEES:
                mentions.append(
                    RawMention(
                        relationship="CALLS",
                        mentioned_name=callee,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        source_symbol_index=_enclosing(symbols, node.start_byte),
                    )
                )
        for child in node.children:
            visit(child)

    visit(root)
    return mentions


def _argument_callee(node, payload: bytes) -> str | None:
    if not any("argument" in child.type.lower() or "call" in child.type.lower() for child in node.children):
        return None
    for child in node.children:
        if child.type.lower() in {"identifier", "type_identifier", "field_identifier"} or child.type == "IDENTIFIER":
            text = _decode(payload, child.start_byte, child.end_byte).strip()
            return text or None
    return None


def _callee(node, payload: bytes) -> str | None:
    if node.type == "command":
        for child in node.children:
            if child.type == "command_name":
                text = _decode(payload, child.start_byte, child.end_byte).strip()
                return text or None
    for field in ("function", "name", "method", "callee"):
        child = node.child_by_field_name(field)
        if child is not None:
            text = _decode(payload, child.start_byte, child.end_byte).strip()
            return text or None
    if node.type != "call" or not any(child.type == "arguments" for child in node.children):
        return None
    for child in node.children:
        if "identifier" in child.type:
            text = _decode(payload, child.start_byte, child.end_byte).strip()
            return text or None
    return None


def _kind_from_type(node_type: str) -> str | None:
    text = node_type.lower()
    if text in {"fnproto"} or "function" in text or text.endswith("_fn"):
        return "function"
    if "method" in text:
        return "method"
    if "class" in text or "struct" in text or text == "impl_item":
        return "class"
    if "interface" in text or "trait" in text:
        return "interface"
    if "enum" in text:
        return "enum"
    return None


def _node_name(node, payload: bytes) -> str | None:
    named = node.child_by_field_name("name")
    if named is not None:
        return _decode(payload, named.start_byte, named.end_byte).strip() or None
    for child in node.children:
        if child.type.lower() in {"identifier", "type_identifier", "field_identifier"}:
            text = _decode(payload, child.start_byte, child.end_byte).strip()
            if text:
                return text
    return None


def _nest(symbols: list[RawSymbol]) -> list[RawSymbol]:
    for index, symbol in enumerate(symbols):
        parent = None
        best = None
        for other_index, other in enumerate(symbols):
            if other_index == index:
                continue
            if other.start_byte <= symbol.start_byte and symbol.end_byte <= other.end_byte:
                if (other.start_byte, other.end_byte) == (symbol.start_byte, symbol.end_byte):
                    continue
                size = other.end_byte - other.start_byte
                if best is None or size < best:
                    best = size
                    parent = other_index
        symbol.parent_index = parent
        if parent is not None:
            symbol.qualified_name = f"{symbols[parent].qualified_name}.{symbol.name}"
    return symbols


def _enclosing(symbols: list[RawSymbol], start: int) -> int | None:
    best = None
    size = None
    for index, symbol in enumerate(symbols):
        if symbol.kind not in {"function", "method"}:
            continue
        if symbol.start_byte <= start < symbol.end_byte:
            span = symbol.end_byte - symbol.start_byte
            if size is None or span < size:
                best = index
                size = span
    return best


def _node_covering(root, start: int, end: int):
    found = None
    stack = [root]
    while stack:
        node = stack.pop()
        if node.start_byte == start and node.end_byte == end:
            return node
        if node.start_byte <= start and end <= node.end_byte:
            found = node
            stack.extend(node.children)
    return found


def _receiver_type(node, payload: bytes) -> str | None:
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None
    found = None
    stack = [receiver]
    while stack:
        current = stack.pop()
        if current.type in {"type_identifier", "identifier"}:
            found = _decode(payload, current.start_byte, current.end_byte).strip() or found
        stack.extend(current.children)
    return found
