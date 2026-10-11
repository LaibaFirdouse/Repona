"""Parse included blob bytes into symbols and mentions.

Supported files use tree-sitter. A parser exception fails the generation.
A syntax error keeps recovered declarations and does not invent a module symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Language, Parser

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript

DECLARATION_KINDS = {
    "function_definition": "function",
    "class_definition": "class",
    "function_declaration": "function",
    "class_declaration": "class",
    "method_definition": "method",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}
IMPORT_TYPES = {"import_statement", "import_from_statement"}
CALL_TYPES = {"call", "call_expression"}
LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "tsx",
    ".ts": "typescript",
    ".tsx": "tsx",
}


class ParserFailure(RuntimeError):
    pass


@dataclass
class RawSymbol:
    kind: str
    name: str
    qualified_name: str
    signature: str
    start_byte: int
    end_byte: int
    parent_index: int | None


@dataclass
class RawMention:
    relationship: str
    mentioned_name: str
    start_byte: int
    end_byte: int
    source_symbol_index: int | None
    import_span: tuple[int, int] | None = None


@dataclass
class ParsedFile:
    language: str | None
    parse_status: str
    parse_message: str | None
    symbols: list[RawSymbol] = field(default_factory=list)
    mentions: list[RawMention] = field(default_factory=list)
    import_spans: list[tuple[int, int]] = field(default_factory=list)


_PARSERS: dict[str, Parser] | None = None


def _parsers() -> dict[str, Parser]:
    global _PARSERS
    if _PARSERS is None:
        try:
            _PARSERS = {
                "python": Parser(Language(tspython.language())),
                "javascript": Parser(Language(tsjavascript.language())),
                "typescript": Parser(Language(tstypescript.language_typescript())),
                "tsx": Parser(Language(tstypescript.language_tsx())),
            }
        except Exception as exc:
            raise ParserFailure("parser bundle failed to load") from exc
    return _PARSERS


def language_for_path(relative_path: str) -> str | None:
    dot = relative_path.rfind(".")
    if dot == -1:
        return None
    return LANGUAGE_BY_EXTENSION.get(relative_path[dot:].lower())


def module_qualified_name(relative_path: str) -> str:
    path = relative_path
    dot = path.rfind(".")
    if dot != -1 and "/" not in path[dot:]:
        path = path[:dot]
    return path.replace("/", ".")


def parse_file(relative_path: str, payload: bytes) -> ParsedFile:
    language = language_for_path(relative_path)
    if language is None:
        return ParsedFile(language=None, parse_status="not_parsed", parse_message=None)
    if not payload:
        stem = module_qualified_name(relative_path).rsplit(".", 1)[-1]
        return ParsedFile(
            language=language,
            parse_status="parsed",
            parse_message=None,
            symbols=[
                RawSymbol(
                    kind="module",
                    name=stem,
                    qualified_name=module_qualified_name(relative_path),
                    signature="",
                    start_byte=0,
                    end_byte=0,
                    parent_index=None,
                )
            ],
        )
    try:
        tree = _parsers()[language].parse(payload)
    except Exception as exc:
        raise ParserFailure(f"parser failed for {relative_path}") from exc
    if tree is None:
        raise ParserFailure(f"parser returned no tree for {relative_path}")
    parsed = _walk(relative_path, payload, language, tree.root_node)
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
    parsed.parse_status = "parsed"
    return parsed


def _decode(payload: bytes, start: int, end: int) -> str:
    return payload[start:end].decode("utf-8", errors="replace")


def _walk(relative_path: str, payload: bytes, language: str, root) -> ParsedFile:
    symbols: list[RawSymbol] = []
    mentions: list[RawMention] = []
    import_nodes = []
    stack: list[int] = []

    def enclosing_symbol() -> int | None:
        return stack[-1] if stack else None

    def visit(node, parameter_depth: int = 0) -> None:
        if node.type in {"parameters", "formal_parameters"}:
            parameter_depth += 1
        if node.type in IMPORT_TYPES:
            import_nodes.append(node)
            for name in _import_names(payload, node):
                mentions.append(
                    RawMention(
                        relationship="IMPORTS",
                        mentioned_name=name,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        source_symbol_index=None,
                        import_span=_import_run_later,
                    )
                )
        if node.type in DECLARATION_KINDS or _is_constant(node):
            kind = "constant" if node.type == "lexical_declaration" else DECLARATION_KINDS[node.type]
            if kind == "function" and _inside_class(stack, symbols):
                kind = "method"
            name_node = node.child_by_field_name("name")
            if kind == "constant":
                declarator = next((child for child in node.children if child.type == "variable_declarator"), None)
                name_node = declarator.child_by_field_name("name") if declarator is not None else None
            if name_node is not None and not (kind == "constant" and _inside_function(stack, symbols)):
                name = _decode(payload, name_node.start_byte, name_node.end_byte)
                parent = stack[-1] if stack else None
                qualified = name if parent is None else f"{symbols[parent].qualified_name}.{name}"
                signature = _signature(payload, node)
                symbols.append(
                    RawSymbol(
                        kind=kind,
                        name=name,
                        qualified_name=qualified,
                        signature=signature,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        parent_index=parent,
                    )
                )
                stack.append(len(symbols) - 1)
                for child in node.children:
                    visit(child, parameter_depth)
                stack.pop()
                return
        if node.type in CALL_TYPES:
            function = node.child_by_field_name("function")
            if function is not None:
                mentions.append(
                    RawMention(
                        relationship="CALLS",
                        mentioned_name=_decode(payload, function.start_byte, function.end_byte),
                        start_byte=function.start_byte,
                        end_byte=function.end_byte,
                        source_symbol_index=enclosing_symbol(),
                    )
                )
        elif (
            node.type == "identifier"
            and parameter_depth == 0
            and not _is_declaration_name(node)
            and not _is_call_function(node)
        ):
            mentions.append(
                RawMention(
                    relationship="REFERENCES",
                    mentioned_name=_decode(payload, node.start_byte, node.end_byte),
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    source_symbol_index=enclosing_symbol(),
                )
            )
        for child in node.children:
            visit(child, parameter_depth)

    visit(root)
    import_spans = _contiguous_import_spans(payload, import_nodes)
    for mention in mentions:
        if mention.relationship == "IMPORTS":
            mention.import_span = _span_for(import_spans, mention.start_byte)
    call_spans = {(mention.start_byte, mention.end_byte) for mention in mentions if mention.relationship == "CALLS"}
    filtered = []
    for mention in mentions:
        if mention.relationship == "REFERENCES" and (mention.start_byte, mention.end_byte) in call_spans:
            continue
        if mention.relationship == "REFERENCES" and _is_own_name(symbols, mention):
            continue
        filtered.append(mention)
    return ParsedFile(
        language=language,
        parse_status="parsed",
        parse_message=None,
        symbols=symbols,
        mentions=filtered,
        import_spans=import_spans,
    )


_import_run_later = None


def _inside_class(stack: list[int], symbols: list[RawSymbol]) -> bool:
    return any(symbols[index].kind == "class" for index in stack)


def _inside_function(stack: list[int], symbols: list[RawSymbol]) -> bool:
    return any(symbols[index].kind in {"function", "method"} for index in stack)


def _is_constant(node) -> bool:
    if node.type != "lexical_declaration":
        return False
    kind = node.child_by_field_name("kind")
    return kind is not None and kind.type == "const"


def _signature(payload: bytes, node) -> str:
    params = node.child_by_field_name("parameters")
    if params is None:
        return ""
    text = _decode(payload, params.start_byte, params.end_byte).strip()
    if text.startswith("(") and text.endswith(")"):
        return text[1:-1]
    return text


def _import_names(payload: bytes, node) -> list[str]:
    if node.type == "import_from_statement":
        module = node.child_by_field_name("module_name")
        if module is None:
            return []
        return [_decode(payload, module.start_byte, module.end_byte)]
    if node.type == "import_statement" and node.child_by_field_name("source") is not None:
        source = node.child_by_field_name("source")
        text = _decode(payload, source.start_byte, source.end_byte).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            text = text[1:-1]
        return [text] if text else []
    names = []
    for child in node.children:
        if child.type in {"dotted_name", "identifier", "relative_import"}:
            names.append(_decode(payload, child.start_byte, child.end_byte))
    return names


def _contiguous_import_spans(payload: bytes, nodes) -> list[tuple[int, int]]:
    if not nodes:
        return []
    ordered = sorted(nodes, key=lambda node: node.start_byte)
    spans: list[tuple[int, int]] = []
    run_start = ordered[0].start_byte
    run_end = ordered[0].end_byte
    for node in ordered[1:]:
        between = payload[run_end : node.start_byte]
        if between.strip(b" \t\r\n") == b"":
            run_end = node.end_byte
            continue
        spans.append((run_start, run_end))
        run_start = node.start_byte
        run_end = node.end_byte
    spans.append((run_start, run_end))
    return spans


def _span_for(spans: list[tuple[int, int]], start: int) -> tuple[int, int] | None:
    for span in spans:
        if span[0] <= start < span[1]:
            return span
    return None


def _is_declaration_name(node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    return parent.child_by_field_name("name") == node


def _is_call_function(node) -> bool:
    parent = node.parent
    if parent is None or parent.type not in CALL_TYPES:
        return False
    return parent.child_by_field_name("function") == node


def _is_own_name(symbols: list[RawSymbol], mention: RawMention) -> bool:
    if mention.source_symbol_index is None:
        return False
    symbol = symbols[mention.source_symbol_index]
    return mention.mentioned_name == symbol.name
