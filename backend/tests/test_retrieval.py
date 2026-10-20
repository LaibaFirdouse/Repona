import json
import time
import unittest
from unittest.mock import patch

from app.retrieval.model_call import parse_claims
from app.retrieval.answer import (
    SYSTEM_PROMPT,
    select_technology_rows,
    specific_technology_question,
)
from app.retrieval.query_terms import question_identifiers, question_paths, question_qualified_names
from app.retrieval.fusion import (
    ChunkChoice,
    GraphEdge,
    LaneHit,
    allow_edge,
    chunk_ids_for_anchor,
    consecutive_ranks,
    fuse_anchors,
    materialize_anchors,
    neighbor_chunk,
    preserve_named_spans,
    select_edges,
)
from app.retrieval.packet import (
    assign_evidence_aliases,
    fits_model_context,
    implementation_before_tests,
    ollama_prompt_fits,
    pack_evidence,
    qwen_prompt_tokens,
    render_answer,
    resolve_evidence_aliases,
    review_claims,
    span_citations,
)


class FusionTests(unittest.TestCase):
    def test_duplicate_parts_keep_one_rank_and_ranks_are_consecutive(self) -> None:
        hits = [
            LaneHit("authenticate_user", "p1", 1),
            LaneHit("authenticate_user", "p2", 2),
            LaneHit("database_connection", "d1", 3),
            LaneHit("database_connection", "d2", 4),
            LaneHit("verify_password", "v1", 5),
        ]
        self.assertEqual(
            consecutive_ranks(hits),
            [("authenticate_user", 1), ("database_connection", 2), ("verify_password", 3)],
        )
        self.assertEqual(chunk_ids_for_anchor(hits)["authenticate_user"], ["p1", "p2"])

    def test_two_lanes_outrank_one_lane(self) -> None:
        name = [("authenticate_user", 1)]
        vector = [("authenticate_user", 2), ("other", 1)]
        fused = fuse_anchors([name, vector, []])
        self.assertEqual(fused[0], "authenticate_user")
        self.assertGreater(
            1 / 61 + 1 / 62,
            1 / 61,
        )

    def test_name_only_symbol_uses_parts_when_it_has_no_body(self) -> None:
        parts = [
            ChunkChoice("part-1", "authenticate_user", "symbol_part", 0),
            ChunkChoice("part-0", "authenticate_user", "symbol_part", 1),
        ]
        chosen = materialize_anchors(["authenticate_user"], {}, {}, {"authenticate_user": parts})
        self.assertEqual([item.chunk_id for item in chosen], ["part-1", "part-0"])

    def test_chunk_hits_are_kept_and_ordered_by_anchor_rank(self) -> None:
        body = ChunkChoice("small", "small_fn", "symbol_body", 0)
        part = ChunkChoice("large-part", "large_fn", "symbol_part", 2)
        chosen = materialize_anchors(
            ["large_fn", "small_fn"],
            {"large_fn": ["large-part"], "small_fn": ["small"]},
            {"large-part": part, "small": body},
            {},
        )
        self.assertEqual([item.chunk_id for item in chosen], ["large-part", "small"])

    def test_neighbor_without_a_body_uses_the_first_part(self) -> None:
        chosen = neighbor_chunk(
            [
                ChunkChoice("later", "verify_password", "symbol_part", 2),
                ChunkChoice("first", "verify_password", "symbol_part", 0),
            ]
        )
        self.assertEqual(chosen.chunk_id, "first")

    def test_edge_direction_and_cap_are_deterministic(self) -> None:
        self.assertTrue(allow_edge("CALLS", False, "symbol", "symbol"))
        self.assertFalse(allow_edge("DECLARES", True, "file", "symbol"))
        self.assertTrue(allow_edge("CONTAINS", False, "file", "symbol"))
        edges = [
            GraphEdge("e2", "REFERENCES", True, "a", 1, "x", "symbol", "x", "resolved"),
            GraphEdge("e1", "CALLS", False, "a", 1, "y", "symbol", "y", "resolved"),
            GraphEdge("e3", "CALLS", True, "b", 2, "z", "symbol", "z", "unresolved"),
        ]
        selected = select_edges(edges, limit=2)
        self.assertEqual([edge.edge_id for edge in selected], ["e1", "e2"])


class PacketTests(unittest.TestCase):
    def test_mixed_ids_keep_the_claim_without_calling_it_fully_valid(self) -> None:
        kept, valid = review_claims(
            [{"kind": "observed", "text": "It verifies passwords.", "evidence_ids": ["chunk-1", "missing"]}],
            {"chunk-1"},
        )
        self.assertEqual(kept[0]["evidence_ids"], ["chunk-1"])
        self.assertFalse(valid)

    def test_an_observed_claim_with_only_invalid_ids_is_removed(self) -> None:
        kept, valid = review_claims(
            [{"kind": "observed", "text": "missing", "evidence_ids": ["nope"]}],
            {"chunk-1"},
        )
        self.assertEqual(kept, [])
        self.assertFalse(valid)

    def test_no_citations_are_not_valid(self) -> None:
        kept, valid = review_claims([{"kind": "suggestion", "text": "rename it", "evidence_ids": []}], {"chunk-1"})
        self.assertEqual(kept, [])
        self.assertFalse(valid)

    def test_an_inference_needs_a_packet_id(self) -> None:
        kept, valid = review_claims(
            [{"kind": "inference", "text": "Login delegates to the session store.", "evidence_ids": ["chunk-1"]}],
            {"chunk-1"},
        )
        self.assertTrue(valid)
        self.assertEqual(kept[0]["kind"], "inference")
        self.assertEqual(
            render_answer(kept),
            "Login delegates to the session store.",
        )
        dropped, dropped_valid = review_claims(
            [{"kind": "suggestion", "text": "rename it", "evidence_ids": ["missing"]}],
            {"chunk-1"},
        )
        self.assertEqual(dropped, [])
        self.assertFalse(dropped_valid)

    def test_the_flask_dispatch_request_span_survives_and_resolves(self) -> None:
        span_id = "fa7a557f-672c-5d55-84de-0242ea4c64b1"
        claim = {
            "kind": "observed",
            "text": "Flask.dispatch_request returns the view for the matched URL rule.",
            "evidence_ids": [span_id],
        }
        kept, valid = review_claims([claim], {span_id})
        self.assertTrue(valid)
        self.assertEqual(kept[0]["evidence_ids"], [span_id])
        self.assertEqual(kept[0]["text"], claim["text"])
        citations = span_citations(
            [
                {
                    "chunk_id": span_id,
                    "relative_path": "src/flask/app.py",
                    "start_line": 966,
                    "end_line": 990,
                }
            ],
            kept,
        )
        self.assertEqual(
            citations,
            [
                {
                    "chunk_id": span_id,
                    "path": "src/flask/app.py",
                    "start_line": 966,
                    "end_line": 990,
                }
            ],
        )

    def test_an_alias_resolves_to_the_flask_method_span(self) -> None:
        span_id = "fa7a557f-672c-5d55-84de-0242ea4c64b1"
        spans = [
            {
                "chunk_id": span_id,
                "relative_path": "src/flask/app.py",
                "start_line": 966,
                "end_line": 990,
            }
        ]
        aliases = assign_evidence_aliases([span_id])
        claim = {"kind": "observed", "text": "It calls ensure_sync on the matched view.", "evidence_ids": ["E1"]}
        resolved = resolve_evidence_aliases([claim], aliases)
        self.assertEqual(resolved[0]["evidence_ids"], [span_id])
        kept, valid = review_claims(resolved, {span_id})
        self.assertTrue(valid)
        self.assertEqual(span_citations(spans, kept)[0]["path"], "src/flask/app.py")
        self.assertEqual(span_citations(spans, kept)[0]["start_line"], 966)
        self.assertEqual(span_citations(spans, kept)[0]["end_line"], 990)

    def test_an_unknown_alias_is_rejected(self) -> None:
        span_id = "fa7a557f-672c-5d55-84de-0242ea4c64b1"
        aliases = assign_evidence_aliases([span_id])
        resolved = resolve_evidence_aliases(
            [{"kind": "observed", "text": "missing", "evidence_ids": ["E9"]}],
            aliases,
        )
        self.assertEqual(resolved[0]["evidence_ids"], ["E9"])
        kept, valid = review_claims(resolved, {span_id})
        self.assertEqual(kept, [])
        self.assertFalse(valid)

    def test_a_prefix_of_a_span_id_is_not_accepted(self) -> None:
        span_id = "fa7a557f-672c-5d55-84de-0242ea4c64b1"
        aliases = assign_evidence_aliases([span_id])
        resolved = resolve_evidence_aliases(
            [{"kind": "observed", "text": "shortened", "evidence_ids": ["fa7a557f"]}],
            aliases,
        )
        self.assertEqual(resolved[0]["evidence_ids"], ["fa7a557f"])
        kept, _valid = review_claims(resolved, {span_id})
        self.assertEqual(kept, [])

    def test_an_alias_from_another_request_does_not_resolve_to_this_span(self) -> None:
        flask_id = "fa7a557f-672c-5d55-84de-0242ea4c64b1"
        other_id = "11111111-1111-1111-1111-111111111111"
        this_request = assign_evidence_aliases([other_id])
        other_request = assign_evidence_aliases([flask_id])
        self.assertEqual(other_request["E1"], flask_id)
        resolved = resolve_evidence_aliases(
            [{"kind": "observed", "text": "wrong request", "evidence_ids": ["E1"]}],
            this_request,
        )
        self.assertEqual(resolved[0]["evidence_ids"], [other_id])
        kept, _valid = review_claims(resolved, {flask_id})
        self.assertEqual(kept, [])

    def test_implementation_spans_stay_ahead_of_tests(self) -> None:
        spans = [
            {"relative_path": "tests/test_views.py", "chunk_id": "t"},
            {"relative_path": "src/flask/app.py", "chunk_id": "a"},
            {"relative_path": "src/flask/views.py", "chunk_id": "v"},
            {"relative_path": "tests/test_basic.py", "chunk_id": "b"},
        ]
        ordered = implementation_before_tests(spans)
        self.assertEqual([span["chunk_id"] for span in ordered], ["a", "v", "t", "b"])
        named = implementation_before_tests(
            [
                {"relative_path": "src/flask/views.py", "qualified_name": "View.dispatch_request", "chunk_id": "v"},
                {"relative_path": "src/flask/app.py", "qualified_name": "Flask.dispatch_request", "chunk_id": "a"},
                {"relative_path": "tests/test_views.py", "qualified_name": "Index.dispatch_request", "chunk_id": "t"},
            ],
            "In Flask, how does Flask.dispatch_request invoke the matched view function?",
        )
        self.assertEqual([span["chunk_id"] for span in named], ["a", "v", "t"])

    def test_the_prompt_asks_for_natural_grounded_answers(self) -> None:
        self.assertIn("Never mention internal ids such as E1", SYSTEM_PROMPT)
        self.assertIn("inputs, calls, and returns", SYSTEM_PROMPT)
        self.assertIn("Cite tests only when the question is about tests", SYSTEM_PROMPT)
        self.assertIn("the way a helpful assistant would", SYSTEM_PROMPT)
        self.assertNotIn("evidence packet", SYSTEM_PROMPT)

    def test_a_full_span_id_still_resolves_when_aliases_are_in_use(self) -> None:
        span_id = "fa7a557f-672c-5d55-84de-0242ea4c64b1"
        aliases = assign_evidence_aliases([span_id, "edge-1"])
        self.assertEqual(aliases, {"E1": span_id, "E2": "edge-1"})
        resolved = resolve_evidence_aliases(
            [{"kind": "observed", "text": "full id", "evidence_ids": [span_id]}],
            aliases,
        )
        self.assertEqual(resolved[0]["evidence_ids"], [span_id])
        kept, valid = review_claims(resolved, {span_id, "edge-1"})
        self.assertTrue(valid)
        self.assertEqual(kept[0]["evidence_ids"], [span_id])

    def test_review_of_a_twelve_span_packet_is_fast(self) -> None:
        span_id = "fa7a557f-672c-5d55-84de-0242ea4c64b1"
        valid_ids = {f"span-{index}" for index in range(12)} | {f"edge-{index}" for index in range(24)}
        valid_ids.add(span_id)
        claims = [
            {"kind": "observed", "text": f"claim {index}", "evidence_ids": [f"span-{index}", "missing"]}
            for index in range(12)
        ]
        claims.append({"kind": "observed", "text": "the method", "evidence_ids": [span_id]})
        started = time.perf_counter()
        kept, valid = review_claims(claims, valid_ids)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.05)
        self.assertEqual(len(kept), 13)
        self.assertFalse(valid)
        self.assertEqual(kept[-1]["evidence_ids"], [span_id])

    def test_one_valid_citation_is_accepted(self) -> None:
        kept, valid = review_claims(
            [{"kind": "observed", "text": "It returns the user.", "evidence_ids": ["chunk-1"]}],
            {"chunk-1"},
        )
        self.assertTrue(valid)
        self.assertEqual(kept[0]["evidence_ids"], ["chunk-1"])

    def test_later_spans_are_dropped_when_the_byte_cap_is_reached(self) -> None:
        spans = [{"id": "a", "raw": 8, "text": "abcdefgh"}, {"id": "b", "raw": 8, "text": "ijklmnop"}]
        packed = pack_evidence(
            "q",
            spans,
            [],
            [],
            source_byte_cap=8,
            input_byte_cap=10_000,
            context_tokens=10_000,
            output_tokens=1,
            span_text=lambda span: (span["raw"], span["text"]),
            edge_text=lambda edge: "",
            technology_text=lambda item: "",
        )
        self.assertEqual([span["id"] for span in packed.spans], ["a"])

    def test_margin_drops_the_next_span_before_the_window_is_full(self) -> None:
        spans = [{"id": "a", "raw": 1, "text": "abcd"}, {"id": "b", "raw": 1, "text": "efgh"}]
        packed = pack_evidence(
            "qqqq",
            spans,
            [],
            [],
            source_byte_cap=100,
            input_byte_cap=10_000,
            context_tokens=25,
            output_tokens=10,
            margin_tokens=5,
            count_tokens=len,
            span_text=lambda span: (span["raw"], span["text"]),
            edge_text=lambda edge: "",
            technology_text=lambda item: "",
        )
        self.assertEqual([span["id"] for span in packed.spans], ["a"])

    def test_qwen_template_matches_the_measured_ollama_count(self) -> None:
        self.assertEqual(qwen_prompt_tokens("Reply with a JSON object.", "Say hi."), 22)

    def test_ollama_budget_leaves_output_room_and_a_margin(self) -> None:
        self.assertTrue(fits_model_context(3000, 800, 256, 4096))
        self.assertFalse(fits_model_context(3041, 800, 256, 4096))
        with patch("app.retrieval.packet.qwen_prompt_tokens", return_value=4000):
            self.assertFalse(ollama_prompt_fits("system", "user", 800))

    def test_token_budget_drops_lower_priority_evidence(self) -> None:
        spans = [{"id": "a", "raw": 4, "text": "abcd"}, {"id": "b", "raw": 4, "text": "efgh"}]
        packed = pack_evidence(
            "qqqq",
            spans,
            [],
            [],
            source_byte_cap=100,
            input_byte_cap=10_000,
            context_tokens=2,
            output_tokens=1,
            span_text=lambda span: (span["raw"], span["text"]),
            edge_text=lambda edge: "",
            technology_text=lambda item: "",
        )
        self.assertEqual(packed.spans, [])

    def test_model_json_is_parsed_from_a_fence(self) -> None:
        raw = '```json\n' + json.dumps({"claims": [{"kind": "observed", "text": "yes", "evidence_ids": ["c"]}]}) + "\n```"
        self.assertEqual(parse_claims(raw).claims[0]["text"], "yes")


def dispatch_answer_concepts(answer: str) -> dict[str, bool]:
    """Score one Flask dispatch explanation. This is not a rule applied to every answer."""
    text = answer.lower()
    return {
        "view_lookup": "endpoint" in text and "view_functions" in text,
        "invocation_handling": "ensure_sync" in text,
        "arguments_and_return": "view_args" in text and "return" in text,
    }


class QuestionTokenTests(unittest.TestCase):
    def test_a_sentence_keeps_the_identifier_and_the_directory(self) -> None:
        self.assertEqual(question_identifiers("Which functions call derive_key?"), ["Which", "functions", "call", "derive_key"])
        self.assertEqual(question_paths("What Python files live under src/itsdangerous?"), ["src/itsdangerous"])

    def test_a_qualified_name_is_kept_and_a_single_word_is_not(self) -> None:
        question = "In Flask, how does Flask.dispatch_request invoke the matched view function?"
        self.assertEqual(question_qualified_names(question), ["Flask.dispatch_request"])
        conceptual = "How does Flask match an incoming HTTP request to a view function and return that view's result?"
        self.assertEqual(question_qualified_names(conceptual), [])

    def test_a_named_span_past_the_cap_is_kept_and_fusion_order_follows(self) -> None:
        spans = [ChunkChoice(f"c{index}", f"a{index}", "symbol_body", index) for index in range(15)]
        named = spans[14]
        kept = preserve_named_spans(spans, [named], 12)
        self.assertEqual(kept[0].chunk_id, "c14")
        self.assertEqual([span.chunk_id for span in kept[1:]], [f"c{index}" for index in range(11)])
        self.assertEqual(len(kept), 12)

    def test_an_unnamed_late_span_stays_outside_the_cap(self) -> None:
        spans = [ChunkChoice(f"c{index}", f"a{index}", "symbol_body", index) for index in range(15)]
        kept = preserve_named_spans(spans, [], 12)
        self.assertEqual([span.chunk_id for span in kept], [f"c{index}" for index in range(12)])
        self.assertNotIn("c14", [span.chunk_id for span in kept])


class TechnologySelectionTests(unittest.TestCase):
    def test_a_named_dependency_is_kept_ahead_of_the_alphabetical_limit(self) -> None:
        rows = [{"id": f"id-{index}", "name": f"pkg{index:02d}", "evidence_text": "x"} for index in range(25)]
        rows.append({"id": "its", "name": "itsdangerous", "evidence_text": "itsdangerous>=2.2.0"})
        selected = select_technology_rows("Where is the itsdangerous library used?", rows)
        self.assertEqual([row["name"] for row in selected], ["itsdangerous"])
        self.assertTrue(specific_technology_question("Where is the itsdangerous library used?", selected))

    def test_a_method_question_does_not_select_technologies(self) -> None:
        rows = [{"id": "flask", "name": "flask", "evidence_text": "flask"}]
        question = "How does Flask.dispatch_request select and invoke a view?"
        self.assertEqual(select_technology_rows(question, rows), [])
        self.assertFalse(specific_technology_question(question, []))

    def test_a_general_library_question_keeps_the_bounded_list(self) -> None:
        rows = [{"id": f"id-{index}", "name": f"pkg{index:02d}", "evidence_text": "x"} for index in range(25)]
        selected = select_technology_rows("Which libraries does this project depend on?", rows)
        self.assertEqual(len(selected), 20)
        self.assertFalse(specific_technology_question("Which libraries does this project depend on?", selected))


class DispatchAnswerConceptTests(unittest.TestCase):
    def test_a_complete_explanation_covers_lookup_call_and_return(self) -> None:
        answer = (
            "Flask.dispatch_request uses rule.endpoint to find the view in self.view_functions, "
            "passes that function through self.ensure_sync, calls it with **view_args, and returns the result."
        )
        concepts = dispatch_answer_concepts(answer)
        self.assertTrue(concepts["view_lookup"])
        self.assertTrue(concepts["invocation_handling"])
        self.assertTrue(concepts["arguments_and_return"])

    def test_the_shallow_sentence_misses_the_mechanics(self) -> None:
        concepts = dispatch_answer_concepts("Flask.dispatch_request() invokes the matched view function.")
        self.assertFalse(concepts["view_lookup"])
        self.assertFalse(concepts["invocation_handling"])
        self.assertFalse(concepts["arguments_and_return"])


if __name__ == "__main__":
    unittest.main()
