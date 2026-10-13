import re
import unittest

from app.retrieval.implementation import (
    direct_implementation_claims,
    explain_implementation,
    implementation_claims_for_spans,
)
from app.retrieval.structure import symbol_structure
from tests.test_retrieval import dispatch_answer_concepts

DISPATCH = '''def dispatch_request(self):
    """Dispatches the request and returns the value of the view function."""
    req = request_ctx.request
    if req.routing_exception is not None:
        self.raise_routing_exception(req)
    rule = req.url_rule
    return self.ensure_sync(self.view_functions[rule.endpoint])(**view_args)
'''

ADD = """def add(left, right):
    return left + right
"""

BUILD = """function build(name) {
  return prefix.concat(name);
}
"""


def span(name, path, text, chunk_id="chunk-1"):
    return {
        "chunk_id": chunk_id,
        "relative_path": path,
        "qualified_name": name,
        "chunk_kind": "symbol_body",
        "text": text,
    }


class ImplementationExplanationTests(unittest.TestCase):
    def test_a_returned_call_is_described_from_the_source(self) -> None:
        steps = explain_implementation(DISPATCH, "python", "Flask.dispatch_request")
        text = " ".join(steps)
        concepts = dispatch_answer_concepts(text)
        self.assertTrue(concepts["view_lookup"])
        self.assertTrue(concepts["invocation_handling"])
        self.assertTrue(concepts["arguments_and_return"])
        self.assertIn("self.view_functions[rule.endpoint]", text)
        self.assertIn("**view_args", text)
        self.assertNotIn("matched route", text)

    def test_a_plain_return_is_not_turned_into_a_call(self) -> None:
        steps = explain_implementation(ADD, "python", "add")
        self.assertEqual(steps, ["add returns `left + right`."])

    def test_a_javascript_return_cites_the_call_in_the_source(self) -> None:
        steps = explain_implementation(BUILD, "javascript", "build")
        text = " ".join(steps)
        self.assertIn("build returns `prefix.concat(name)`", text)
        self.assertIn("calls `prefix.concat`", text)

    def test_the_named_body_is_cited_and_other_questions_are_left_alone(self) -> None:
        spans = [span("Flask.dispatch_request", "src/flask/app.py", DISPATCH, "fa7a")]
        claims = direct_implementation_claims(["Flask.dispatch_request"], spans)
        self.assertIsNotNone(claims)
        self.assertTrue(all(claim["evidence_ids"] == ["fa7a"] for claim in claims))
        self.assertIsNone(direct_implementation_claims([], spans))
        self.assertIsNone(direct_implementation_claims(["Flask.dispatch_request", "Flask.wsgi_app"], spans))
        self.assertIsNone(direct_implementation_claims(["Missing.method"], spans))

    def test_a_nested_function_return_is_not_described_as_the_outer_method(self) -> None:
        source = """def outer():
    def inner():
        return hidden()
    return shown
"""
        steps = explain_implementation(source, "python", "outer")
        self.assertEqual(steps, ["outer returns `shown`."])

    def test_a_conditional_return_quotes_the_condition(self) -> None:
        source = '''def dispatch_request(self):
    if req.method == "OPTIONS":
        return self.make_default_options_response(ctx)
    return self.ensure_sync(self.view_functions[rule.endpoint])(**view_args)
'''
        steps = explain_implementation(source, "python", "Flask.dispatch_request")
        text = " ".join(steps)
        self.assertIn("returns `self.make_default_options_response(ctx)` when `req.method == \"OPTIONS\"`", text)
        self.assertIn("returns `self.ensure_sync(self.view_functions[rule.endpoint])(**view_args)`", text)
        self.assertNotIn("when `req.method == \"OPTIONS\"`. Flask.dispatch_request returns `self.ensure_sync", text)
        concepts = dispatch_answer_concepts(text)
        self.assertTrue(concepts["view_lookup"])
        self.assertTrue(concepts["invocation_handling"])
        self.assertTrue(concepts["arguments_and_return"])

    def test_a_conditional_assignment_is_quoted_before_the_return(self) -> None:
        source = '''def make_response(self, rv):
    if isinstance(rv, (dict, list)):
        rv = self.json.response(rv)
    return rv
'''
        steps = explain_implementation(source, "python", "Flask.make_response")
        self.assertEqual(
            steps,
            [
                "Flask.make_response assigns `self.json.response(rv)` to `rv` when `isinstance(rv, (dict, list))`.",
                "Flask.make_response returns `rv`.",
            ],
        )

    def test_a_bare_call_does_not_crowd_out_the_method_call(self) -> None:
        source = '''def make_response(self, rv):
    if isinstance(rv, tuple):
        len_rv = len(rv)
    rv = self.response_class(
        rv,  # pyright: ignore
        status=status,
    )
    return rv
'''
        steps = explain_implementation(source, "python", "Flask.make_response")
        text = " ".join(steps)
        self.assertNotIn("len(rv)", text)
        self.assertIn("returns `rv`", text)
        quotes = re.findall(r"`([^`]+)`", text)
        response_quote = next(quote for quote in quotes if "response_class" in quote)
        self.assertIn("# pyright: ignore", response_quote)
        built = symbol_structure(source, "python", "Flask.make_response")
        call = next(item for item in built.calls if item.role == "assignment" and "response_class" in item.text)
        span = source.encode()[call.start_byte : call.end_byte].decode()
        self.assertIn("# pyright: ignore", span)
        self.assertIn("".join(response_quote.split()), "".join(span.split()))
        comment_at = source.encode().find(b"# pyright: ignore")
        self.assertLessEqual(call.start_byte, comment_at)
        self.assertLess(comment_at, call.end_byte)

    def test_a_javascript_conditional_return_quotes_the_condition(self) -> None:
        source = '''function build(name) {
  if (!name) {
    return prefix.concat("guest");
  }
  return prefix.concat(name);
}
'''
        steps = explain_implementation(source, "javascript", "build")
        text = " ".join(steps)
        self.assertIn("returns `prefix.concat(\"guest\")` when `!name`", text)
        self.assertIn("returns `prefix.concat(name)`", text)
        self.assertIn("calls `prefix.concat`", text)

    def test_tied_methods_are_explained_and_cited_separately(self) -> None:
        first = '''def process_response(self, response):
    self.session_interface.save_session(self, response)
    return response
'''
        second = '''def finalize_request(self, rv):
    response = self.make_response(rv)
    response = self.process_response(response)
    return response
'''
        claims = implementation_claims_for_spans(
            [
                span("Flask.finalize_request", "src/flask/app.py", second, "fin"),
                span("Flask.process_response", "src/flask/app.py", first, "proc"),
            ]
        )
        self.assertIsNotNone(claims)
        by_id = {}
        for claim in claims:
            by_id.setdefault(claim["evidence_ids"][0], []).append(claim["text"])
        self.assertIn("self.make_response(rv)", " ".join(by_id["fin"]))
        self.assertIn("self.process_response(response)", " ".join(by_id["fin"]))
        self.assertIn("returns `response`", " ".join(by_id["fin"]))
        self.assertIn("save_session", " ".join(by_id["proc"]))
        self.assertTrue(all(len(claim["evidence_ids"]) == 1 for claim in claims))
        self.assertIsNone(implementation_claims_for_spans([span("Flask.missing", "src/flask/app.py", "x = 1\n", "no")]))
