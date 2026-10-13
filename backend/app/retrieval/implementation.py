"""Explain a named function from its own source. The wording comes from that syntax, not from a model."""

from __future__ import annotations

_CALLS = {"call", "call_expression"}
_SUBSCRIPTS = {"subscript", "subscript_expression"}
_FUNCTIONS = {"function_definition", "function_declaration", "method_definition", "arrow_function"}


def direct_implementation_claims(names: list[str], spans: list[dict]) -> list[dict] | None:
    """Cite one named implementation. Return None when the question is not that direct case."""
    if len(names) != 1:
        return None
    name = names[0]
    matches = [
        span
        for span in spans
        if span.get("qualified_name") == name and span.get("chunk_kind") == "symbol_body" and span.get("text")
    ]
    if len(matches) != 1:
        return None
    return implementation_claims_for_span(matches[0])


def implementation_claims_for_span(span: dict) -> list[dict] | None:
    """Cite the steps in one symbol body. Return None when that body has nothing to describe."""
    return implementation_claims_for_spans([span])


def implementation_claims_for_spans(spans: list[dict]) -> list[dict] | None:
    """Cite each body on its own. Return None when any of them cannot be explained."""
    claims: list[dict] = []
    for span in spans:
        qualified = str(span.get("qualified_name") or "")
        if span.get("chunk_kind") != "symbol_body" or not span.get("text") or not qualified:
            return None
        from app.ingestion.parse import language_for_path

        language = language_for_path(str(span.get("relative_path") or ""))
        if language is None:
            return None
        steps = explain_implementation(str(span["text"]), language, qualified)
        if not steps:
            return None
        claims.extend(
            {"kind": "observed", "text": step, "evidence_ids": [span["chunk_id"]]}
            for step in steps
        )
    return claims or None


def explain_implementation(source: str, language: str, qualified_name: str) -> list[str]:
    """Describe operations and returns using only text that appears in the source."""
    from app.ingestion.parse import _parsers

    try:
        tree = _parsers()[language].parse(source.encode("utf-8"))
    except Exception:
        return []
    payload = source.encode("utf-8")
    operations, returns = _events(tree.root_node, payload, qualified_name)
    while operations and len(operations) + len(returns) > 8:
        operations.pop()
    return (operations + returns)[:8]


def _events(root, payload: bytes, qualified_name: str) -> tuple[list[str], list[str]]:
    operations: list[str] = []
    returns: list[str] = []
    seen: set[str] = set()
    recorded: list[tuple[int, str]] = []
    conditions: dict[tuple, str] = {}
    return_count = 0

    def add_operation(sentence: str, priority: int) -> None:
        if sentence in seen:
            return
        seen.add(sentence)
        recorded.append((priority, sentence))

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
            _visit_if(node, visit, conditions, payload)
            return
        if node.type in {"except_clause", "catch_clause"}:
            _visit_handler(node, visit, conditions, payload)
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
        if node.type == "return_statement":
            nonlocal return_count
            if return_count < 2:
                returns.extend(_describe_return(qualified_name, node, payload, _condition_of(node, conditions)))
                return_count += 1
            return
        if node.type == "expression_statement":
            _record_statement(node, payload, qualified_name, add_operation, conditions)
            return
        for child in node.children:
            visit(child, depth)

    visit(root, 0)
    preferred = [sentence for priority, sentence in recorded if priority == 0]
    fallback = [sentence for priority, sentence in recorded if priority == 1]
    operations.extend((preferred or fallback)[:3])
    return operations, returns


def _visit_if(node, visit, conditions: dict[tuple, str], payload: bytes) -> None:
    condition = node.child_by_field_name("condition")
    consequence = node.child_by_field_name("consequence")
    if consequence is not None:
        _bind_condition(consequence, condition, conditions, payload)
        visit(consequence, 1)
    for child in node.children:
        if child.type == "elif_clause":
            _visit_if(child, visit, conditions, payload)
        elif child.type == "else_clause":
            visit(child, 1)


def _node_key(node) -> tuple:
    return (node.start_byte, node.end_byte, node.type)


def _visit_handler(node, visit, conditions: dict[tuple, str], payload: bytes) -> None:
    """A return inside except or catch belongs to that handler, not to the try body."""
    body = node.child_by_field_name("body")
    if body is None:
        body = next((child for child in node.children if child.type in {"block", "statement_block"}), None)
    if body is None:
        return
    text = _handler_text(node, payload)
    if text:
        _bind_text(body, text, conditions)
    visit(body, 1)


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


def _bind_text(node, text: str, conditions: dict[tuple, str]) -> None:
    conditions[_node_key(node)] = text
    for child in node.children:
        if child.type in {"elif_clause", "else_clause", "if_statement", "except_clause", "catch_clause"}:
            continue
        _bind_text(child, text, conditions)


def _bind_condition(node, condition, conditions: dict[tuple, str], payload: bytes) -> None:
    text = _condition_text(payload, condition)
    if not text:
        return
    conditions[_node_key(node)] = text
    for child in node.children:
        if child.type in {"elif_clause", "else_clause", "if_statement"}:
            continue
        _bind_condition(child, condition, conditions, payload)


def _condition_of(node, conditions: dict[tuple, str]):
    current = node
    while current is not None:
        condition = conditions.get(_node_key(current))
        if condition is not None:
            return condition
        current = current.parent
    return None


def _record_statement(node, payload: bytes, qualified_name: str, add_operation, conditions: dict[tuple, str]) -> None:
    child = next((item for item in node.children if item.type in {"assignment", *_CALLS}), None)
    if child is None:
        return
    condition = _condition_of(node, conditions)
    if child.type == "assignment":
        right = child.child_by_field_name("right")
        left = child.child_by_field_name("left")
        if right is None or left is None or right.type not in _CALLS:
            return
        sentence = f"{qualified_name} assigns `{_slice(payload, right)}` to `{_slice(payload, left)}`"
        priority = _call_priority(right)
    else:
        sentence = f"{qualified_name} calls `{_slice(payload, child)}`"
        priority = _call_priority(child)
    if condition:
        sentence += f" when `{condition}`"
    add_operation(sentence + ".", priority)


def _condition_text(payload: bytes, node) -> str:
    while node is not None and node.type == "parenthesized_expression":
        inner = next((child for child in node.children if child.type not in {"(", ")"}), None)
        if inner is None:
            break
        node = inner
    if node is None:
        return ""
    return _slice(payload, node)


def _describe_return(qualified_name: str, node, payload: bytes, condition: str = "") -> list[str]:
    value = next((child for child in node.children if child.type not in {"return", ";"}), None)
    if value is None:
        sentence = f"{qualified_name} returns"
        if condition:
            sentence += f" when `{condition}`"
        return [sentence + "."]
    sentence = f"{qualified_name} returns `{_slice(payload, value)}`"
    if condition:
        sentence += f" when `{condition}`"
    steps = [sentence + "."]
    steps.extend(_describe_call(qualified_name, value, payload))
    return steps


def _describe_call(qualified_name: str, node, payload: bytes) -> list[str]:
    if node.type not in _CALLS:
        return []
    callee = node.child_by_field_name("function")
    arguments = node.child_by_field_name("arguments")
    if callee is not None and callee.type in _CALLS:
        steps = _describe_call(qualified_name, callee, payload)
        if arguments is not None:
            steps.append(f"{qualified_name} calls that result with `{_slice(payload, arguments)}`.")
        return steps
    steps = []
    if callee is not None:
        argument_text = _slice(payload, arguments) if arguments is not None else "()"
        steps.append(f"{qualified_name} calls `{_slice(payload, callee)}` with `{argument_text}`.")
    if arguments is not None:
        steps.extend(_subscripts(arguments, payload))
    return steps


def _subscripts(node, payload: bytes) -> list[str]:
    found: list[str] = []

    def visit(current) -> None:
        if current.type in _SUBSCRIPTS:
            parts = [child for child in current.children if child.type not in {"[", "]"}]
            if len(parts) >= 2:
                found.append(f"`{_slice(payload, parts[0])}` is indexed by `{_slice(payload, parts[1])}`.")
            return
        for child in current.children:
            visit(child)

    visit(node)
    return found


def _call_priority(node) -> int:
    """A method call is the operation. A bare name such as len is only a fallback."""
    callee = node.child_by_field_name("function")
    if callee is not None and callee.type in {"attribute", "member_expression", "call", "call_expression"}:
        return 0
    return 1


def _slice(payload: bytes, node) -> str:
    """Quote the node's own source. Whitespace is collapsed; comments stay.

    A quoted expression has to be recoverable from this byte span. Stripping a
    ``#`` comment would make the quote fail that check.
    """
    text = payload[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    return " ".join(text.split())
