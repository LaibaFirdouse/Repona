import unittest

from app.retrieval.fusion import GraphEdge
from app.retrieval.implementation import explain_implementation
from app.retrieval.structure import (
    defer_structural_abstain,
    route_explanation,
    structure_decision,
    symbol_structure,
)

SIGN = """def sign(self, value):
    value = want_bytes(value)
    return value + self.sep + self.get_signature(value)
"""

UNSIGN = """def unsign(self, signed_value):
    signed_value = want_bytes(signed_value)
    if self.sep not in signed_value:
        raise BadSignature("missing")
    if self.verify_signature(value, sig):
        return value
    raise BadSignature("mismatch")
"""

VALIDATE = """def validate(self, signed_value):
    try:
        self.unsign(signed_value)
        return True
    except BadSignature:
        return False
"""

TIMESTAMP = """def validate(self, signed_value):
    return self.unsign(signed_value)
"""

ALGORITHM = """def verify_signature(self, key, value, sig):
    return hmac.compare_digest(key, sig)
"""

TEST_BODY = """def test_signer(self):
    return signer.sign(b"value")
"""

CONDITIONAL = '''def dispatch_request(self):
    if req.method == "OPTIONS":
        return self.make_default_options_response(ctx)
    return self.ensure_sync(self.view_functions[rule.endpoint])(**view_args)
'''

JS = """function build(name) {
  if (!name) {
    return prefix.concat("guest");
  }
  return prefix.concat(name);
}
"""

SIGNING_QUESTION = "How does a value get signed and later checked?"


def body(name, path, text, chunk_id, symbol_id, start_line):
    return {
        "chunk_id": chunk_id,
        "relative_path": path,
        "qualified_name": name,
        "chunk_kind": "symbol_body",
        "text": text,
        "symbol_id": symbol_id,
        "span_hash": chunk_id,
        "start_line": start_line,
        "source_start_byte": start_line * 10,
        "source_end_byte": start_line * 10 + len(text.encode()),
    }


def edge(source, target, mentioned, resolution="resolved", edge_id="edge-1"):
    return GraphEdge(edge_id, "CALLS", True, source, 1, target, "symbol", mentioned, resolution)


class StructureExtractionTests(unittest.TestCase):
    def test_symbol_identity_is_cached_with_the_span_hash(self) -> None:
        first = symbol_structure(
            SIGN,
            "python",
            "Signer.sign",
            symbol_id="sign-id",
            span_hash="sign-hash",
            source_start_byte=220,
            source_end_byte=310,
        )
        second = symbol_structure(SIGN, "python", "Signer.sign", symbol_id="sign-id", span_hash="sign-hash")
        other = symbol_structure(SIGN, "python", "Signer.sign", symbol_id="sign-id", span_hash="other-hash")
        self.assertIs(first, second)
        self.assertIsNot(first, other)
        self.assertEqual(first.symbol_id, "sign-id")
        self.assertEqual(first.span_hash, "sign-hash")
        self.assertEqual(first.source_start_byte, 220)
        self.assertEqual(first.source_end_byte, 310)

    def test_a_call_span_is_the_source_expression_and_not_an_edge(self) -> None:
        built = symbol_structure(SIGN, "python", "Signer.sign", symbol_id="sign-body", span_hash="sign-body")
        payload = SIGN.encode()
        assignment = next(call for call in built.calls if call.role == "assignment")
        signature = next(call for call in built.calls if call.leaf == "get_signature")
        self.assertEqual(assignment.callee, "want_bytes")
        self.assertEqual(assignment.assigned_to, "value")
        self.assertEqual(payload[assignment.start_byte : assignment.end_byte].decode(), "want_bytes(value)")
        self.assertEqual(signature.receiver, "self")
        self.assertEqual(payload[signature.start_byte : signature.end_byte].decode(), "self.get_signature(value)")
        self.assertFalse(hasattr(signature, "resolution"))
        self.assertEqual(built.returns[0].condition, "")
        self.assertEqual(built.returns[0].expression, "value + self.sep + self.get_signature(value)")
        self.assertEqual(
            payload[built.returns[0].start_byte : built.returns[0].end_byte].decode(),
            built.returns[0].expression,
        )

    def test_a_conditional_return_stays_distinct_from_the_following_return(self) -> None:
        built = symbol_structure(CONDITIONAL, "python", "Flask.dispatch_request")
        self.assertEqual(built.returns[0].condition, 'req.method == "OPTIONS"')
        self.assertEqual(built.returns[1].condition, "")
        self.assertIn("make_default_options_response", built.returns[0].expression)
        self.assertIn("ensure_sync", built.returns[1].expression)
        self.assertTrue(any(item.kind == "if" for item in built.conditions))

    def test_an_except_return_is_conditional_and_the_try_return_is_not(self) -> None:
        built = symbol_structure(VALIDATE, "python", "Signer.validate")
        true_return = next(item for item in built.returns if item.expression == "True")
        false_return = next(item for item in built.returns if item.expression == "False")
        self.assertEqual(true_return.condition, "")
        self.assertEqual(false_return.condition, "BadSignature")
        text = " ".join(explain_implementation(VALIDATE, "python", "Signer.validate"))
        self.assertIn("returns `False` when `BadSignature`", text)
        self.assertNotIn("returns `True` when `BadSignature`", text)

    def test_raises_keep_their_source_spans_and_conditions(self) -> None:
        built = symbol_structure(UNSIGN, "python", "Signer.unsign")
        payload = UNSIGN.encode()
        self.assertEqual(len(built.raises), 2)
        self.assertEqual(built.raises[0].condition, "self.sep not in signed_value")
        self.assertEqual(built.raises[1].condition, "")
        self.assertEqual(payload[built.raises[0].start_byte : built.raises[0].end_byte].decode(), 'BadSignature("missing")')
        self.assertEqual(payload[built.raises[1].start_byte : built.raises[1].end_byte].decode(), 'BadSignature("mismatch")')

    def test_a_ternary_is_a_condition_without_making_the_return_conditional(self) -> None:
        source = """def choose(ready):
    return a if ready else b
"""
        built = symbol_structure(source, "python", "choose")
        self.assertEqual(built.returns[0].condition, "")
        self.assertEqual(built.returns[0].expression, "a if ready else b")
        self.assertTrue(any(item.kind == "conditional" and item.text == "ready" for item in built.conditions))

    def test_every_return_is_stored_even_when_the_explainer_stops_at_two(self) -> None:
        source = """def choose(a, b):
    if a:
        return one
    if b:
        return two
    return three
"""
        built = symbol_structure(source, "python", "choose")
        self.assertEqual([item.expression for item in built.returns], ["one", "two", "three"])
        self.assertEqual(built.returns[0].condition, "a")
        self.assertEqual(built.returns[1].condition, "b")
        self.assertEqual(built.returns[2].condition, "")
        self.assertEqual(len(explain_implementation(source, "python", "choose")), 2)

    def test_a_nested_function_is_not_part_of_the_outer_method(self) -> None:
        source = """def outer():
    def inner():
        return hidden()
    return shown
"""
        built = symbol_structure(source, "python", "outer")
        self.assertEqual([item.expression for item in built.returns], ["shown"])
        self.assertFalse(any(call.callee == "hidden" for call in built.calls))

    def test_javascript_branches_and_call_spans_follow_the_source(self) -> None:
        built = symbol_structure(JS, "javascript", "build")
        payload = JS.encode()
        self.assertEqual(built.returns[0].condition, "!name")
        self.assertEqual(built.returns[1].condition, "")
        call = next(item for item in built.calls if "guest" in item.arguments)
        self.assertEqual(call.callee, "prefix.concat")
        self.assertEqual(payload[call.start_byte : call.end_byte].decode(), 'prefix.concat("guest")')


class StructureSelectionTests(unittest.TestCase):
    def signing_packet(self):
        return [
            body("Signer.validate", "src/itsdangerous/signer.py", VALIDATE, "validate", "validate-id", 258),
            body("Signer.sign", "src/itsdangerous/signer.py", SIGN, "sign", "sign-id", 222),
            body("TimestampSigner.validate", "src/itsdangerous/timed.py", TIMESTAMP, "timestamp", "timestamp-id", 80),
            body("SigningAlgorithm.verify_signature", "src/itsdangerous/signer.py", ALGORITHM, "algorithm", "algorithm-id", 40),
            body("Signer.unsign", "src/itsdangerous/signer.py", UNSIGN, "unsign", "unsign-id", 244),
            body("TestSigner.test_signer", "tests/test_signer.py", TEST_BODY, "test", "test-id", 10),
        ]

    def test_the_signing_paraphrase_explains_the_connected_class_only(self) -> None:
        decision, claims = structure_decision(SIGNING_QUESTION, self.signing_packet())
        self.assertEqual(decision, "explain")
        cited = {claim["evidence_ids"][0] for claim in claims}
        self.assertEqual(cited, {"sign", "unsign", "validate"})
        text = " ".join(claim["text"] for claim in claims)
        self.assertIn("value + self.sep + self.get_signature(value)", text)
        self.assertIn("raises `BadSignature(\"missing\")` when `self.sep not in signed_value`", text)
        self.assertNotIn("resolved", text)
        self.assertNotIn("TimestampSigner", text)
        self.assertNotIn("SigningAlgorithm", text)
        self.assertNotIn("test_signer", text)

    def test_an_unresolved_cross_file_edge_does_not_create_a_call_path(self) -> None:
        spans = [
            body("Gate.open", "src/gate.py", "def open(self):\n    other.sign(value)\n    return value\n", "open", "open-id", 1),
            body("Signer.sign", "src/itsdangerous/signer.py", SIGN, "sign", "sign-id", 222),
        ]
        unresolved = edge("open-id", "sign-id", "other.sign", "unresolved", "edge-open")
        ambiguous = edge("open-id", "sign-id", "other.sign", "ambiguous", "edge-open")
        self.assertEqual(structure_decision("How does a value get signed?", spans, [unresolved])[0], "abstain")
        self.assertEqual(structure_decision("How does a value get signed?", spans, [ambiguous])[0], "abstain")

    def test_a_resolved_cross_file_edge_is_explained_from_that_edge(self) -> None:
        spans = [
            body("Gate.open", "src/gate.py", "def open(self):\n    other.sign(value)\n    return value\n", "open", "open-id", 1),
            body("Signer.sign", "src/itsdangerous/signer.py", SIGN, "sign", "sign-id", 222),
        ]
        resolved = edge("open-id", "sign-id", "other.sign", "resolved", "edge-open")
        decision, claims = structure_decision("What calls sign?", spans, [resolved])
        self.assertEqual(decision, "explain")
        text = " ".join(claim["text"] for claim in claims)
        self.assertIn("calls `other.sign`, and the index resolved that call to Signer.sign", text)
        resolved_claim = next(claim for claim in claims if "resolved" in claim["text"])
        self.assertEqual(resolved_claim["evidence_ids"], ["open", "edge-open"])
        self.assertNotIn("self.get_signature`, and the index resolved", text)

    def test_two_unrelated_classes_are_not_merged(self) -> None:
        spans = [
            body("Signer.sign", "src/itsdangerous/signer.py", SIGN, "sign", "sign-id", 222),
            body("Signer.derive_key", "src/itsdangerous/signer.py", "def derive_key(self):\n    return self.secret\n", "derive", "derive-id", 180),
            body("TimestampSigner.unsign", "src/itsdangerous/timed.py", UNSIGN, "timed-unsign", "timed-unsign-id", 90),
            body("TimestampSigner.validate", "src/itsdangerous/timed.py", VALIDATE, "timed-validate", "timed-validate-id", 110),
        ]
        self.assertEqual(structure_decision(SIGNING_QUESTION, spans)[0], "abstain")

    def test_more_than_three_methods_of_one_class_are_not_trimmed_arbitrarily(self) -> None:
        extra = "def get_signature(self, value):\n    return self.algorithm.get_signature(value)\n"
        spans = [
            body("Signer.sign", "src/itsdangerous/signer.py", SIGN, "sign", "sign-id", 222),
            body("Signer.unsign", "src/itsdangerous/signer.py", UNSIGN, "unsign", "unsign-id", 244),
            body("Signer.validate", "src/itsdangerous/signer.py", VALIDATE, "validate", "validate-id", 258),
            body("Signer.get_signature", "src/itsdangerous/signer.py", extra, "signature", "signature-id", 215),
        ]
        self.assertEqual(structure_decision(SIGNING_QUESTION, spans)[0], "abstain")

    def test_one_named_method_is_explained_and_one_unnamed_method_is_not(self) -> None:
        make = "def make_response(self, rv):\n    return self.response_class(rv)\n"
        spans = [body("App.make_response", "src/app.py", make, "make", "make-id", 12)]
        decision, claims = structure_decision("How does the application make a response?", spans)
        self.assertEqual(decision, "explain")
        self.assertIn("self.response_class(rv)", claims[0]["text"])
        lone = body("Signer.sign", "src/itsdangerous/signer.py", SIGN, "only-sign", "only-sign", 222)
        self.assertEqual(structure_decision(SIGNING_QUESTION, [lone])[0], "abstain")

    def test_a_named_function_is_not_replaced_by_another_class(self) -> None:
        escape = "def escape(s):\n    return Markup(_escape_inner(s))\n"
        inner = "def _escape_inner(s):\n    return s.replace('&', '&amp;')\n"
        method = "def escape(self, s):\n    rv = escape(s)\n    return rv\n"
        ljust = "def ljust(self, width, fillchar):\n    return self.escape(fillchar)\n"
        spans = [
            body("escape", "src/markupsafe/__init__.py", escape, "escape", "escape-id", 24),
            body("_escape_inner", "src/markupsafe/_native.py", inner, "inner", "inner-id", 1),
            body("Markup.escape", "src/markupsafe/__init__.py", method, "method", "method-id", 241),
            body("Markup.ljust", "src/markupsafe/__init__.py", ljust, "ljust", "ljust-id", 270),
        ]
        resolved = edge("escape-id", "inner-id", "_escape_inner", "resolved", "edge-inner")
        decision, claims = structure_decision("How does escape escape a string?", spans, [resolved])
        self.assertEqual(decision, "explain")
        cited = {claim["evidence_ids"][0] for claim in claims}
        self.assertEqual(cited, {"escape", "inner"})
        text = " ".join(claim["text"] for claim in claims)
        self.assertIn("resolved that call to _escape_inner", text)
        self.assertNotIn("Markup.ljust", text)

    def test_a_named_callee_keeps_its_resolved_caller(self) -> None:
        escape = "def escape(s):\n    return Markup(_escape_inner(s))\n"
        inner = "def _escape_inner(s):\n    return s.replace('&', '&amp;')\n"
        method = "def escape(self, s):\n    rv = escape(s)\n    return rv\n"
        ljust = "def ljust(self, width, fillchar):\n    return self.escape(fillchar)\n"
        spans = [
            body("escape", "src/markupsafe/__init__.py", escape, "escape", "escape-id", 24),
            body("_escape_inner", "src/markupsafe/_native.py", inner, "inner", "inner-id", 1),
            body("Markup.escape", "src/markupsafe/__init__.py", method, "method", "method-id", 241),
            body("Markup.ljust", "src/markupsafe/__init__.py", ljust, "ljust", "ljust-id", 270),
        ]
        resolved = edge("escape-id", "inner-id", "_escape_inner", "resolved", "edge-inner")
        decision, claims = structure_decision(
            "How does escape reach _escape_inner in another file?", spans, [resolved]
        )
        self.assertEqual(decision, "explain")
        text = " ".join(claim["text"] for claim in claims)
        self.assertIn("calls `_escape_inner`, and the index resolved that call to _escape_inner", text)
        self.assertNotIn("Markup.ljust", text)
        resolved_claim = next(claim for claim in claims if "resolved" in claim["text"])
        self.assertEqual(resolved_claim["evidence_ids"], ["escape", "edge-inner"])

    def test_empty_evidence_and_tests_abstain(self) -> None:
        self.assertEqual(structure_decision("Where is the request dispatcher?", [])[0], "abstain")
        test_span = body("TestSigner.test_signer", "tests/test_signer.py", TEST_BODY, "test", "test-id", 1)
        self.assertEqual(structure_decision("How does a value get signed?", [test_span])[0], "abstain")


class ExplanationRouteTests(unittest.TestCase):
    def test_an_answerable_packet_goes_to_the_model_without_prewritten_sentences(self) -> None:
        named = [{"text": "named"}]
        behavior = [{"text": "behavior"}]
        self.assertEqual(route_explanation(named, None, "abstain", [], defer_abstain=False), ("model", None))
        self.assertEqual(route_explanation(None, behavior, "explain", [{"text": "structure"}], defer_abstain=False), ("model", None))
        self.assertEqual(route_explanation(None, None, "explain", [{"text": "grounded"}], defer_abstain=False), ("model", None))

    def test_missing_evidence_still_goes_to_the_model(self) -> None:
        self.assertEqual(route_explanation(None, None, "abstain", [], defer_abstain=False), ("model", None))

    def test_dependency_and_resolved_caller_questions_keep_the_model_path(self) -> None:
        resolved = edge("caller-id", "target-id", "issue_token")
        unresolved = edge("caller-id", "target-id", "issue_token", "unresolved")
        self.assertTrue(defer_structural_abstain("Which libraries does this project depend on?", [{"id": "flask"}], []))
        self.assertTrue(defer_structural_abstain("What calls issue_token?", [], [resolved]))
        self.assertFalse(defer_structural_abstain("What calls issue_token?", [], [unresolved]))
        self.assertFalse(defer_structural_abstain("Where is the request dispatcher?", [], []))
        self.assertFalse(defer_structural_abstain(SIGNING_QUESTION, [], []))
        self.assertEqual(route_explanation(None, None, "abstain", [], defer_abstain=True), ("model", None))
