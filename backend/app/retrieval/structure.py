"""Structural facts for one already-indexed symbol body.

The facts are read from the span text with the existing Tree-sitter parsers.
They do not change chunk boundaries, chunk hashes, edge types, or parser_version.
A call recorded here was seen in the syntax. It is not a resolved call edge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.retrieval.expansion import expansion_names

_CALLS = {"call", "call_expression"}
_FUNCTIONS = {"function_definition", "function_declaration", "method_definition", "arrow_function"}
_HANDLERS = {"except_clause", "catch_clause"}
_CACHE: dict[tuple[str, str], "SymbolStructure"] = {}
_IMPACT = re.compile(r"\b(?:callers?|calls|impact|depend\w*)\b", re.IGNORECASE)
_QUALIFIED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class ObservedCall:
    """A function or method invocation found in the body. This is not a graph edge."""

    text: str
    callee: str
    arguments: str
    start_byte: int
    end_byte: int
    condition: str
    role: str
    assigned_to: str
    receiver: str
    leaf: str


@dataclass(frozen=True)
class ObservedReturn:
    expression: str
    start_byte: int
    end_byte: int
    condition: str


@dataclass(frozen=True)
class ObservedCondition:
    text: str
    start_byte: int
    end_byte: int
    kind: str


@dataclass(frozen=True)
class ObservedRaise:
    text: str
    start_byte: int
    end_byte: int
    condition: str


@dataclass(frozen=True)
class SymbolStructure:
    symbol_id: str
    span_hash: str
    qualified_name: str
    source_start_byte: int
    source_end_byte: int
    calls: tuple[ObservedCall, ...]
    returns: tuple[ObservedReturn, ...]
    conditions: tuple[ObservedCondition, ...]
    raises: tuple[ObservedRaise, ...]


def symbol_structure(
    source: str,
    language: str,
    qualified_name: str,
    *,
    symbol_id: str = "",
    span_hash: str = "",
    source_start_byte: int = 0,
    source_end_byte: int | None = None,
) -> SymbolStructure:
    """Return the cached structure for this symbol body.

    The cache key is the symbol id and the span hash. The same indexed span
    is not parsed again. A body without those ids is parsed and not stored.
    """
    if symbol_id and span_hash:
        cached = _CACHE.get((symbol_id, span_hash))
        if cached is not None and cached.qualified_name == qualified_name:
            return cached
    built = _extract(
        source,
        language,
        qualified_name,
        symbol_id=symbol_id,
        span_hash=span_hash,
        source_start_byte=source_start_byte,
        source_end_byte=len(source.encode("utf-8")) if source_end_byte is None else source_end_byte,
    )
    if symbol_id and span_hash:
        _CACHE[(symbol_id, span_hash)] = built
    return built


def structure_decision(question: str, spans: list[dict], edges: list | None = None) -> tuple[str, list[dict]]:
    """Choose a grounded explanation, or abstain.

    One named method, one class whose retrieved methods call each other, or
    one resolved call path can be explained. A symbol the question names is
    not replaced by a different class. Several unrelated methods cannot.
    """
    edges = list(edges or [])
    bodies = _explainable_bodies(spans)
    if not bodies:
        return "abstain", []
    chosen = _select_bodies(question, bodies, edges)
    if not chosen:
        return "abstain", []
    claims = _claims_for(chosen, edges)
    if not claims:
        return "abstain", []
    return "explain", claims


def route_explanation(
    named: list[dict] | None,
    behavior: list[dict] | None,
    decision: str,
    claims: list[dict],
    *,
    defer_abstain: bool,
) -> tuple[str, list[dict] | None]:
    """Send an answerable packet to the model. Do not return prewritten sentences.

    A named method, a behavior cue, or a structural explanation makes the
    question eligible. Eligibility is not the answer. When the packet does not
    establish the asked behavior, the model still answers and says what is absent.
    Dependency and impact questions use the same model path.
    """
    del named, behavior, decision, claims, defer_abstain
    return "model", None


def defer_structural_abstain(question: str, technologies: list, edges: list) -> bool:
    """Leave dependency and resolved-caller questions on the existing model path."""
    if technologies:
        return True
    if _IMPACT.search(question) is None:
        return False
    return any(
        getattr(edge, "relationship_type", "") == "CALLS" and getattr(edge, "resolution", "") == "resolved"
        for edge in edges
    )


def _extract(
    source: str,
    language: str,
    qualified_name: str,
    *,
    symbol_id: str,
    span_hash: str,
    source_start_byte: int,
    source_end_byte: int,
) -> SymbolStructure:
    empty = SymbolStructure(
        symbol_id,
        span_hash,
        qualified_name,
        source_start_byte,
        source_end_byte,
        (),
        (),
        (),
        (),
    )
    from app.ingestion.parse import _parsers

    payload = source.encode("utf-8")
    try:
        tree = _parsers()[language].parse(payload)
    except Exception:
        return empty
    calls: list[ObservedCall] = []
    returns: list[ObservedReturn] = []
    conditions: list[ObservedCondition] = []
    raises: list[ObservedRaise] = []
    bound: dict[tuple, str] = {}

    def visit(node, depth: int) -> None:
        if node.type in _FUNCTIONS:
            if depth >= 1:
                return
            for child in node.children:
                visit(child, depth + 1)
            return
        if depth != 1:
            for child in node.children:
                visit(child, depth)
            return
        if node.type == "if_statement":
            _visit_if(node, visit, bound, conditions, calls, payload)
            return
        if node.type in _HANDLERS:
            _visit_handler(node, visit, bound, conditions, payload)
            return
        if node.type == "else_clause":
            body = node.child_by_field_name("body") or node.child_by_field_name("consequence")
            if body is not None:
                visit(body, depth)
            return
        if node.type in {"for_statement", "while_statement"}:
            body = node.child_by_field_name("body")
            if body is not None:
                visit(body, depth)
            return
        condition = _condition_of(node, bound)
        if node.type == "return_statement":
            _record_return(node, payload, condition, returns, calls, conditions)
            return
        if node.type in {"raise_statement", "throw_statement"}:
            _record_raise(node, payload, condition, raises, calls)
            return
        if node.type == "expression_statement":
            _record_expression(node, payload, condition, calls)
            _record_conditional(node, payload, conditions)
            return
        for child in node.children:
            visit(child, depth)

    visit(tree.root_node, 0)
    return SymbolStructure(
        symbol_id,
        span_hash,
        qualified_name,
        source_start_byte,
        source_end_byte,
        tuple(calls),
        tuple(returns),
        tuple(conditions),
        tuple(raises),
    )


def _visit_if(node, visit, bound, conditions, calls, payload: bytes) -> None:
    condition = node.child_by_field_name("condition")
    text = _condition_text(payload, condition)
    if condition is not None and text:
        conditions.append(ObservedCondition(text, condition.start_byte, condition.end_byte, "elif" if node.type == "elif_clause" else "if"))
        _collect_calls(condition, payload, text, "condition", "", calls)
        _record_conditional(condition, payload, conditions)
    consequence = node.child_by_field_name("consequence")
    if consequence is not None and text:
        _bind_text(consequence, text, bound)
        visit(consequence, 1)
    elif consequence is not None:
        visit(consequence, 1)
    for child in node.children:
        if child.type == "elif_clause":
            _visit_if(child, visit, bound, conditions, calls, payload)
        elif child.type == "else_clause":
            visit(child, 1)


def _visit_handler(node, visit, bound, conditions, payload: bytes) -> None:
    body = node.child_by_field_name("body")
    if body is None:
        body = next((child for child in node.children if child.type in {"block", "statement_block"}), None)
    text = _handler_text(node, payload)
    if text:
        start, end = _handler_span(node)
        conditions.append(ObservedCondition(text, start, end, "except" if node.type == "except_clause" else "catch"))
    if body is None:
        return
    if text:
        _bind_text(body, text, bound)
    visit(body, 1)


def _record_return(node, payload: bytes, condition: str, returns, calls, conditions) -> None:
    value = next((child for child in node.children if child.type not in {"return", ";"}), None)
    if value is None:
        returns.append(ObservedReturn("", node.start_byte, node.end_byte, condition))
        return
    returns.append(ObservedReturn(_slice(payload, value), value.start_byte, value.end_byte, condition))
    _collect_calls(value, payload, condition, "nested", "", calls)
    _record_conditional(value, payload, conditions)


def _record_raise(node, payload: bytes, condition: str, raises, calls) -> None:
    value = next((child for child in node.children if child.type not in {"raise", "throw", ";"}), None)
    if value is None:
        raises.append(ObservedRaise("", node.start_byte, node.end_byte, condition))
        return
    raises.append(ObservedRaise(_slice(payload, value), value.start_byte, value.end_byte, condition))
    _collect_calls(value, payload, condition, "raise", "", calls)


def _record_expression(node, payload: bytes, condition: str, calls) -> None:
    child = next((item for item in node.children if item.type in {"assignment", *_CALLS}), None)
    if child is None:
        return
    if child.type == "assignment":
        right = child.child_by_field_name("right")
        left = child.child_by_field_name("left")
        if right is None or left is None or right.type not in _CALLS:
            return
        _collect_calls(right, payload, condition, "assignment", _slice(payload, left), calls)
        return
    _collect_calls(child, payload, condition, "statement", "", calls)


def _record_conditional(node, payload: bytes, conditions) -> None:
    if node.type not in {"conditional_expression", "ternary_expression"}:
        for child in node.children:
            _record_conditional(child, payload, conditions)
        return
    text, start, end = _inline_condition(node, payload)
    if text:
        conditions.append(ObservedCondition(text, start, end, "conditional"))


def _collect_calls(node, payload: bytes, condition: str, role: str, assigned_to: str, calls) -> None:
    if node.type in _CALLS:
        callee_node = node.child_by_field_name("function")
        argument_node = node.child_by_field_name("arguments")
        callee = _slice(payload, callee_node) if callee_node is not None else ""
        arguments = _slice(payload, argument_node) if argument_node is not None else "()"
        receiver, leaf = _split_callee(callee)
        calls.append(
            ObservedCall(
                _slice(payload, node),
                callee,
                arguments,
                node.start_byte,
                node.end_byte,
                condition,
                role,
                assigned_to,
                receiver,
                leaf,
            )
        )
        for child in node.children:
            _collect_calls(child, payload, condition, "nested", "", calls)
        return
    for child in node.children:
        _collect_calls(child, payload, condition, "nested", "", calls)


def _explainable_bodies(spans: list[dict]) -> list[tuple[dict, SymbolStructure]]:
    from app.ingestion.parse import language_for_path

    bodies = []
    for span in spans:
        if span.get("chunk_kind") != "symbol_body":
            continue
        path = str(span.get("relative_path") or "")
        if _is_test_path(path):
            continue
        qualified = str(span.get("qualified_name") or "")
        text = span.get("text") or ""
        if not qualified or not text:
            continue
        language = language_for_path(path)
        if language is None:
            continue
        end = span.get("source_end_byte")
        built = symbol_structure(
            str(text),
            language,
            qualified,
            symbol_id=str(span.get("symbol_id") or ""),
            span_hash=str(span.get("span_hash") or ""),
            source_start_byte=int(span.get("source_start_byte") or 0),
            source_end_byte=None if end is None else int(end),
        )
        if not built.calls and not built.returns and not built.raises:
            continue
        bodies.append((span, built))
    return bodies


def _select_bodies(question: str, bodies: list[tuple[dict, SymbolStructure]], edges: list) -> list[tuple[dict, SymbolStructure]]:
    named = _named_bodies(question, bodies)
    group = _owner_group(bodies, edges)
    if group and (not named or _covers(group, named)):
        return group
    if named:
        focused = _component_covering(named, bodies, edges)
        if focused:
            return focused
        if len(named) == 1:
            return _ordered(named)
        return []
    resolved = _resolved_component(bodies, edges)
    if resolved:
        return resolved
    if len(bodies) == 1 and _question_names_method(question, bodies[0][1].qualified_name):
        return bodies
    return []


def _owner_group(bodies: list[tuple[dict, SymbolStructure]], edges: list) -> list[tuple[dict, SymbolStructure]]:
    by_owner: dict[str, list[tuple[dict, SymbolStructure]]] = {}
    for item in bodies:
        by_owner.setdefault(_owner(item[1].qualified_name), []).append(item)
    counts = {owner: len(items) for owner, items in by_owner.items()}
    multi = [owner for owner, count in counts.items() if count >= 2]
    if len(multi) != 1:
        return []
    owner = multi[0]
    count = counts[owner]
    others = [value for name, value in counts.items() if name != owner]
    group = by_owner[owner]
    if count <= 3 and (not others or count > max(others)) and _connected(group, edges):
        return _ordered(group)
    return []


def _named_bodies(question: str, bodies: list[tuple[dict, SymbolStructure]]) -> list[tuple[dict, SymbolStructure]]:
    """Bodies the question names, without treating a shared leaf as unique."""
    exact = {token.lower() for token in _TOKEN.findall(question) if len(token) >= 6}
    by_name: dict[str, int] = {}
    for _, built in bodies:
        key = built.qualified_name.lower()
        by_name[key] = by_name.get(key, 0) + 1
    chosen = []
    for item in bodies:
        qualified = item[1].qualified_name
        spelled = _question_names_method(question, qualified)
        exact_name = qualified.lower() in exact and by_name[qualified.lower()] == 1
        if spelled or exact_name:
            chosen.append(item)
    return chosen


def _covers(group: list[tuple[dict, SymbolStructure]], named: list[tuple[dict, SymbolStructure]]) -> bool:
    ids = {built.symbol_id for _, built in group}
    return all(built.symbol_id in ids for _, built in named)


def _component_covering(
    named: list[tuple[dict, SymbolStructure]],
    bodies: list[tuple[dict, SymbolStructure]],
    edges: list,
) -> list[tuple[dict, SymbolStructure]]:
    """The resolved call component that contains every named body, when it stays small."""
    wanted = {built.symbol_id for _, built in named}
    if not wanted or len(wanted) != len(named):
        return []
    for component in _call_components(bodies, edges):
        ids = {built.symbol_id for _, built in component}
        if wanted <= ids and 2 <= len(ids) <= 3:
            return _ordered(component)
    return []


def _connected(group: list[tuple[dict, SymbolStructure]], edges: list) -> bool:
    names: dict[str, list[str]] = {}
    for _, built in group:
        names.setdefault(_leaf_name(built.qualified_name), []).append(built.qualified_name)
    for _, built in group:
        for call in built.calls:
            if call.receiver not in {"self", "cls"} or not call.leaf:
                continue
            targets = names.get(call.leaf) or []
            if len(targets) == 1 and targets[0] != built.qualified_name:
                return True
    ids = {built.symbol_id for _, built in group if built.symbol_id}
    return any(source != target for source, target, _edge in _resolved_links(edges, ids))


def _resolved_component(bodies, edges) -> list[tuple[dict, SymbolStructure]]:
    components = _call_components(bodies, edges)
    if not components:
        return []
    components.sort(key=lambda items: (-len(items), items[0][1].qualified_name))
    best = len({built.symbol_id for _, built in components[0]})
    if best < 2 or best > 3:
        return []
    if sum(1 for items in components if len({built.symbol_id for _, built in items}) == best) != 1:
        return []
    return _ordered(components[0])


def _call_components(bodies, edges) -> list[list[tuple[dict, SymbolStructure]]]:
    by_id: dict[str, list[tuple[dict, SymbolStructure]]] = {}
    for item in bodies:
        if item[1].symbol_id:
            by_id.setdefault(item[1].symbol_id, []).append(item)
    links = _resolved_links(edges, set(by_id))
    if not links:
        return []
    parent = {item: item for item in by_id}
    linked: set[str] = set()

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for source, target, _edge in links:
        linked.add(source)
        linked.add(target)
        left, right = find(source), find(target)
        if left != right:
            parent[right] = left
    groups: dict[str, list[tuple[dict, SymbolStructure]]] = {}
    for item in linked:
        groups.setdefault(find(item), []).extend(by_id[item])
    return [items for items in groups.values() if len({built.symbol_id for _, built in items}) >= 2]


def _resolved_links(edges: list, ids: set[str]) -> list[tuple[str, str, object]]:
    links = []
    for edge in edges:
        if getattr(edge, "relationship_type", "") != "CALLS" or getattr(edge, "resolution", "") != "resolved":
            continue
        if getattr(edge, "outgoing", False):
            source, target = edge.anchor_id, edge.other_id
        else:
            source, target = edge.other_id, edge.anchor_id
        if source in ids and target in ids and source != target:
            links.append((source, target, edge))
    return links


def _claims_for(group: list[tuple[dict, SymbolStructure]], edges: list) -> list[dict]:
    from app.ingestion.parse import language_for_path
    from app.retrieval.implementation import explain_implementation

    prepared = []
    for span, built in group:
        language = language_for_path(str(span.get("relative_path") or ""))
        if language is None:
            return []
        steps = explain_implementation(str(span.get("text") or ""), language, built.qualified_name)
        raised = _raise_sentences(built)
        if not steps and not raised:
            return []
        prepared.append((span, built, steps, raised))
    claims: list[dict] = []
    names = {built.symbol_id: built.qualified_name for _, built, _, _ in prepared if built.symbol_id}
    for span, built, steps, raised in prepared:
        claims.extend(_observed(span["chunk_id"], steps))
        claims.extend(_observed(span["chunk_id"], raised))
        if built.symbol_id:
            claims.extend(_resolved_sentences(span, built, edges, names))
    return claims


def _raise_sentences(built: SymbolStructure) -> list[str]:
    sentences = []
    for item in built.raises[:2]:
        if not item.text:
            continue
        sentence = f"{built.qualified_name} raises `{item.text}`"
        if item.condition:
            sentence += f" when `{item.condition}`"
        sentences.append(sentence + ".")
    return sentences


def _resolved_sentences(span: dict, built: SymbolStructure, edges: list, names: dict[str, str]) -> list[dict]:
    """Describe a call only when the index resolved it and the mention is in the source."""
    claims = []
    source_text = str(span.get("text") or "")
    for edge in edges:
        if getattr(edge, "relationship_type", "") != "CALLS" or getattr(edge, "resolution", "") != "resolved":
            continue
        if getattr(edge, "outgoing", False):
            source, target = edge.anchor_id, edge.other_id
        else:
            source, target = edge.other_id, edge.anchor_id
        if source != built.symbol_id or target not in names or target == source:
            continue
        mentioned = str(getattr(edge, "mentioned_name", "") or "")
        if not mentioned or not _quote_in_source(mentioned, source_text):
            continue
        text = (
            f"{built.qualified_name} calls `{mentioned}`, "
            f"and the index resolved that call to {names[target]}."
        )
        claims.append({"kind": "observed", "text": text, "evidence_ids": [span["chunk_id"], edge.edge_id]})
    return claims


def _observed(chunk_id: str, steps: list[str]) -> list[dict]:
    return [{"kind": "observed", "text": step, "evidence_ids": [chunk_id]} for step in steps]


def _question_names_method(question: str, qualified: str) -> bool:
    leaf = _leaf_name(qualified)
    if leaf.lower() in {name.lower() for name in expansion_names(question)}:
        return True
    if qualified in {token for token in _QUALIFIED.findall(question)}:
        return True
    for token in _TOKEN.findall(question):
        if token.lower() != leaf.lower():
            continue
        if "_" in token or any(character.isupper() for character in token[1:]):
            return True
    return False


def _quote_in_source(quote: str, source: str) -> bool:
    return "".join(quote.split()) in "".join(source.split())


def _ordered(group: list[tuple[dict, SymbolStructure]]) -> list[tuple[dict, SymbolStructure]]:
    return sorted(group, key=lambda item: (int(item[0].get("start_line") or 0), item[1].qualified_name))


def _owner(qualified: str) -> str:
    if "." not in qualified:
        return qualified
    return qualified.rsplit(".", 1)[0]


def _leaf_name(qualified: str) -> str:
    return qualified.rsplit(".", 1)[-1]


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return "/tests/" in f"/{normalized}" or name.startswith("test_")


def _split_callee(callee: str) -> tuple[str, str]:
    match = re.fullmatch(r"(self|cls)\.([A-Za-z_][A-Za-z0-9_]*)", callee)
    if match:
        return match.group(1), match.group(2)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", callee):
        return "", callee
    return "", ""


def _condition_of(node, bound: dict[tuple, str]) -> str:
    current = node
    while current is not None:
        condition = bound.get(_node_key(current))
        if condition is not None:
            return condition
        current = current.parent
    return ""


def _bind_text(node, text: str, bound: dict[tuple, str]) -> None:
    bound[_node_key(node)] = text
    for child in node.children:
        if child.type in {"elif_clause", "else_clause", "if_statement", *_HANDLERS}:
            continue
        _bind_text(child, text, bound)


def _node_key(node) -> tuple:
    return (node.start_byte, node.end_byte, node.type)


def _condition_text(payload: bytes, node) -> str:
    while node is not None and node.type == "parenthesized_expression":
        inner = next((child for child in node.children if child.type not in {"(", ")"}), None)
        if inner is None:
            break
        node = inner
    if node is None:
        return ""
    return _slice(payload, node)


def _handler_text(node, payload: bytes) -> str:
    if node.type == "except_clause":
        for child in node.children:
            if child.type not in {"except", ":", "block"}:
                return _slice(payload, child)
        return ""
    names = [child for child in node.children if child.type == "identifier"]
    if not names:
        return ""
    return _slice(payload, names[0])


def _handler_span(node) -> tuple[int, int]:
    if node.type == "except_clause":
        for child in node.children:
            if child.type not in {"except", ":", "block"}:
                return child.start_byte, child.end_byte
    names = [child for child in node.children if child.type == "identifier"]
    if names:
        return names[0].start_byte, names[0].end_byte
    return node.start_byte, node.end_byte


def _inline_condition(node, payload: bytes) -> tuple[str, int, int]:
    children = list(node.children)
    if node.type == "conditional_expression":
        for index, child in enumerate(children):
            if child.type == "if" and index + 1 < len(children):
                condition = children[index + 1]
                return _slice(payload, condition), condition.start_byte, condition.end_byte
    if node.type == "ternary_expression" and children:
        condition = children[0]
        return _slice(payload, condition), condition.start_byte, condition.end_byte
    return "", 0, 0


def _slice(payload: bytes, node) -> str:
    """Same quote text as the explainer: collapsed whitespace, comments kept."""
    from app.retrieval.implementation import _slice as implementation_slice

    return implementation_slice(payload, node)
