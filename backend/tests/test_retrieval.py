import json
import unittest

from app.retrieval.model_call import parse_claims
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
    select_edges,
)
from app.retrieval.packet import pack_evidence, review_claims


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
        dropped, dropped_valid = review_claims(
            [{"kind": "suggestion", "text": "rename it", "evidence_ids": ["missing"]}],
            {"chunk-1"},
        )
        self.assertEqual(dropped, [])
        self.assertFalse(dropped_valid)

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


if __name__ == "__main__":
    unittest.main()
