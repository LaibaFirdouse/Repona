"""Regression fixtures for source evidence identity, packing, and citations.

Each case asserts ground-truth paths/symbols/coverage—not merely that any
expected path appears somewhere in the pack.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.retrieval.chunk_selection import (
    enrich_spans_with_bodies,
    expand_spans_with_sibling_bodies,
    is_substantive_chunk_kind,
    limit_spans_per_file,
    upgrade_chunk_choice,
)
from app.retrieval.evidence_scoring import prioritize_question_targets
from app.retrieval.fusion import ChunkChoice, materialize_anchors, representative_chunk_choices
from app.retrieval.packet import (
    assign_evidence_aliases,
    conservative_byte_tokens,
    pack_evidence,
    resolve_evidence_aliases,
    review_claims,
    review_implementation_support,
    span_citations,
)


def _choice(chunk_id: str, anchor: str, kind: str, ordinal: int = 0) -> ChunkChoice:
    return ChunkChoice(chunk_id, anchor, kind, ordinal)


def _loaded(
    *,
    chunk_id: str,
    path: str,
    qname: str = "",
    kind: str = "symbol_body",
    start_line: int = 1,
    end_line: int = 10,
    text: str = "body",
    raw_bytes: int | None = None,
    symbol_id: str = "",
    start_byte: int = 0,
    end_byte: int | None = None,
) -> dict:
    payload = text.encode("utf-8")
    return {
        "chunk_id": chunk_id,
        "relative_path": path,
        "qualified_name": qname,
        "chunk_kind": kind,
        "start_line": start_line,
        "end_line": end_line,
        "text": text,
        "raw_bytes": raw_bytes if raw_bytes is not None else len(payload),
        "symbol_id": symbol_id or None,
        "source_start_byte": start_byte,
        "source_end_byte": end_byte if end_byte is not None else start_byte + len(payload),
        "context_prefix": "",
    }


class SplitSymbolPartsTests(unittest.TestCase):
    """1. Large function split into symbol_part chunks."""

    def test_ordered_parts_cover_validation_mid_and_return(self) -> None:
        parts = [
            _choice("p0", "sym.validate", "symbol_part", 0),
            _choice("p1", "sym.validate", "symbol_part", 1),
            _choice("p2", "sym.validate", "symbol_part", 2),
        ]
        sizes = {"p0": 400, "p1": 9000, "p2": 500}
        chosen = representative_chunk_choices(parts, sizes)
        self.assertEqual([item.chunk_id for item in chosen], ["p0", "p1", "p2"])

        loaded = [
            _loaded(
                chunk_id="p0",
                path="src/auth.py",
                qname="validate_user",
                kind="symbol_part",
                start_line=10,
                end_line=40,
                text="def validate_user(x):\n    if not x:\n        raise ValueError('bad')\n",
                symbol_id="sym.validate",
                start_byte=0,
                end_byte=80,
            ),
            _loaded(
                chunk_id="p1",
                path="src/auth.py",
                qname="validate_user",
                kind="symbol_part",
                start_line=41,
                end_line=120,
                text="    token = issue(x)\n    audit(token)\n",
                symbol_id="sym.validate",
                start_byte=80,
                end_byte=200,
            ),
            _loaded(
                chunk_id="p2",
                path="src/auth.py",
                qname="validate_user",
                kind="symbol_part",
                start_line=121,
                end_line=140,
                text="    return token\n",
                symbol_id="sym.validate",
                start_byte=200,
                end_byte=220,
            ),
        ]
        # Per-file cap must not drop later parts of the same symbol.
        kept = limit_spans_per_file(loaded, max_per_file=1)
        self.assertEqual([span["chunk_id"] for span in kept], ["p0", "p1", "p2"])
        packed = pack_evidence(
            "q",
            kept,
            [],
            [],
            source_byte_cap=50_000,
            input_byte_cap=100_000,
            context_tokens=100_000,
            output_tokens=1,
            span_text=lambda span: (span["raw_bytes"], span["text"]),
            edge_text=lambda _e: "",
            technology_text=lambda _t: "",
        )
        texts = "\n".join(span["text"] for span in packed.spans)
        self.assertIn("raise ValueError", texts)
        self.assertIn("audit(token)", texts)
        self.assertIn("return token", texts)
        self.assertEqual([span["chunk_id"] for span in packed.spans], ["p0", "p1", "p2"])
        self.assertTrue(is_substantive_chunk_kind("symbol_part", 400))


class ShorterSymbolNotReplacedTests(unittest.TestCase):
    """2. Two functions in one file; longer unrelated body must not replace the short one."""

    def test_upgrade_symbol_part_never_crosses_symbols(self) -> None:
        short = SimpleNamespace(
            id="short-part",
            kind="symbol_part",
            file_id="file-1",
            symbol_id="sym.short",
            start_byte=0,
            end_byte=40,
            ordinal=0,
        )
        long_body = SimpleNamespace(
            id="long-body",
            kind="symbol_body",
            file_id="file-1",
            symbol_id="sym.long",
            start_byte=100,
            end_byte=5000,
            ordinal=0,
        )
        db = MagicMock()
        db.get.return_value = short
        query = MagicMock()
        db.query.return_value = query
        query.join.return_value = query
        query.filter.return_value = query
        # Same-symbol body lookup returns empty → keep the part.
        query.all.return_value = []
        upgraded = upgrade_chunk_choice(db, "gen", short)
        self.assertEqual(upgraded.id, "short-part")

        # Even if a file-wide body query would return the long symbol, symbol_part
        # path must not see it (filter includes symbol_id).
        query.all.return_value = [long_body]
        # Simulate mistaken broad query: upgrade still requires same symbol_id match.
        # When same-symbol returns long_body incorrectly tagged, reject in enrich.
        span = _choice("short-part", "sym.short", "symbol_part", 0)
        db.get.return_value = short

        def _upgrade_side_effect(_db, _gen, chunk):
            return long_body if chunk.id == "short-part" else chunk

        # enrich rejects cross-symbol substitution.
        from app.retrieval import chunk_selection as cs

        original = cs.upgrade_chunk_choice
        try:
            cs.upgrade_chunk_choice = _upgrade_side_effect  # type: ignore[assignment]
            out = enrich_spans_with_bodies(db, "gen", [span])
        finally:
            cs.upgrade_chunk_choice = original  # type: ignore[assignment]
        self.assertEqual(out[0].chunk_id, "short-part")
        self.assertEqual(out[0].anchor_id, "sym.short")

    def test_sibling_expand_keeps_requested_symbol_before_longer_peer(self) -> None:
        short_body = SimpleNamespace(
            id="short-body",
            kind="symbol_body",
            file_id="file-1",
            symbol_id="sym.short",
            start_byte=0,
            end_byte=80,
            ordinal=0,
        )
        long_body = SimpleNamespace(
            id="long-body",
            kind="symbol_body",
            file_id="file-1",
            symbol_id="sym.long",
            start_byte=100,
            end_byte=9000,
            ordinal=0,
        )
        short_part = SimpleNamespace(
            id="short-part",
            kind="symbol_part",
            file_id="file-1",
            symbol_id="sym.short",
            start_byte=0,
            end_byte=40,
            ordinal=0,
        )
        db = MagicMock()

        def get_chunk(model, chunk_id):
            if chunk_id == "short-part":
                return short_part
            if chunk_id == "short-body":
                return short_body
            if chunk_id == "long-body":
                return long_body
            return None

        db.get.side_effect = get_chunk
        query = MagicMock()
        db.query.return_value = query
        query.join.return_value = query
        query.filter.return_value = query

        # First call: symbol rows for sym.short → short_body; later file peer query → both bodies.
        query.all.side_effect = [
            [short_body],
            [short_body, long_body],
        ]
        spans = [_choice("short-part", "sym.short", "symbol_part", 0)]
        expanded = expand_spans_with_sibling_bodies(db, "gen", spans, max_added=4)
        ids = [item.chunk_id for item in expanded]
        self.assertIn("short-body", ids)
        # Long peer may be added secondarily, but never as the identity of sym.short.
        short_items = [item for item in expanded if item.anchor_id == "sym.short"]
        self.assertTrue(all(item.chunk_id in {"short-part", "short-body"} for item in short_items))


class ReadmeVsImplementationTests(unittest.TestCase):
    """3. README mentions the function; real implementation must win for how-it-works."""

    def test_implementation_outranks_readme_mention(self) -> None:
        spans = [
            _loaded(
                chunk_id="readme",
                path="README.md",
                qname="",
                kind="non_code",
                text="See authenticate_user for login.",
                raw_bytes=40,
            ),
            _loaded(
                chunk_id="impl",
                path="src/auth.py",
                qname="authenticate_user",
                kind="symbol_body",
                text="def authenticate_user(u, p):\n    return check(u, p)\n",
                raw_bytes=900,
                symbol_id="sym.auth",
            ),
        ]
        ordered = prioritize_question_targets(
            spans,
            "How does authenticate_user work?",
            prefer_implementation=True,
        )
        self.assertEqual(ordered[0]["chunk_id"], "impl")
        kept, ok = review_implementation_support(
            [{"kind": "observed", "text": "It checks credentials.", "evidence_ids": ["readme"]}],
            ordered,
            require_implementation=True,
        )
        self.assertEqual(kept, [])
        self.assertFalse(ok)
        kept, ok = review_implementation_support(
            [{"kind": "observed", "text": "It checks credentials.", "evidence_ids": ["impl"]}],
            ordered,
            require_implementation=True,
        )
        self.assertEqual(kept[0]["evidence_ids"], ["impl"])
        self.assertTrue(ok)


class ClassMemberBeatsBareTypeShellTests(unittest.TestCase):
    """Named type (AnswerService) must not pack ahead of Class.member bodies."""

    def test_members_outrank_bare_class_shell(self) -> None:
        spans = [
            _loaded(
                chunk_id="class0",
                path="backend/app/retrieval/answer.py",
                qname="AnswerService",
                kind="symbol_part",
                text="class AnswerService:\n    ...\n",
                raw_bytes=8000,
                symbol_id="sym.class",
            ),
            _loaded(
                chunk_id="events0",
                path="backend/app/retrieval/answer.py",
                qname="AnswerService._answer_events",
                kind="symbol_part",
                text="def _answer_events(self):\n    fuse_anchors()\n",
                raw_bytes=8000,
                symbol_id="sym.events",
            ),
        ]
        ordered = prioritize_question_targets(
            spans,
            "How does AnswerService retrieve evidence for a question?",
            prefer_implementation=True,
        )
        self.assertEqual(ordered[0]["chunk_id"], "events0")
        self.assertEqual(ordered[1]["chunk_id"], "class0")


class ImportsDoNotSatisfyImplementationTests(unittest.TestCase):
    """4. Imports alone must not satisfy an implementation question."""

    def test_import_only_claim_rejected_when_body_present(self) -> None:
        spans = [
            _loaded(
                chunk_id="imports",
                path="src/queue.py",
                kind="import_block",
                text="from .ring import RingBuffer\n",
                raw_bytes=30,
            ),
            _loaded(
                chunk_id="body",
                path="src/queue.py",
                qname="enqueue",
                kind="symbol_body",
                text="def enqueue(self, item):\n    self.buf.push(item)\n",
                raw_bytes=800,
                symbol_id="sym.enqueue",
            ),
        ]
        ordered = prioritize_question_targets(
            spans,
            "How is enqueue implemented?",
            prefer_implementation=True,
        )
        self.assertEqual(ordered[0]["chunk_id"], "body")
        kept, ok = review_implementation_support(
            [{"kind": "observed", "text": "It imports RingBuffer.", "evidence_ids": ["imports"]}],
            ordered,
            require_implementation=True,
        )
        self.assertEqual(kept, [])
        self.assertFalse(ok)


class PackSkipsOversizedKeepsLaterTests(unittest.TestCase):
    """5. Later relevant short span still considered after an oversized candidate."""

    def test_small_relevant_after_huge(self) -> None:
        spans = [
            _loaded(chunk_id="huge", path="src/big.py", text="X" * 500, raw_bytes=500, start_byte=0, end_byte=500),
            _loaded(
                chunk_id="needed",
                path="src/small.py",
                qname="needed_fn",
                text="return 1",
                raw_bytes=8,
                start_byte=0,
                end_byte=8,
            ),
        ]
        packed = pack_evidence(
            "q",
            spans,
            [],
            [],
            source_byte_cap=40,
            input_byte_cap=10_000,
            context_tokens=10_000,
            output_tokens=1,
            span_text=lambda span: (span["raw_bytes"], span["text"]),
            edge_text=lambda _e: "",
            technology_text=lambda _t: "",
        )
        self.assertEqual([span["chunk_id"] for span in packed.spans], ["needed"])
        self.assertEqual(packed.excluded[0]["chunk_id"], "huge")


class ExactPathAndSymbolIdentityTests(unittest.TestCase):
    """6. Explicit file path + qualified symbol remain exact through packing/citations."""

    def test_identity_and_citation_agreement(self) -> None:
        spans = [
            _loaded(
                chunk_id="c1",
                path="lib/ring.hpp",
                qname="RingBuffer::push",
                kind="symbol_body",
                start_line=40,
                end_line=88,
                text="bool push(T v) { ... }",
                symbol_id="sym.push",
            ),
            _loaded(
                chunk_id="c2",
                path="other/file.cpp",
                qname="unrelated",
                kind="symbol_body",
                text="void unrelated() {}",
            ),
        ]
        ordered = prioritize_question_targets(
            spans,
            "In lib/ring.hpp how does RingBuffer::push work?",
            prefer_implementation=True,
        )
        self.assertEqual(ordered[0]["chunk_id"], "c1")
        self.assertEqual(ordered[0]["relative_path"], "lib/ring.hpp")
        self.assertEqual(ordered[0]["qualified_name"], "RingBuffer::push")
        packed = pack_evidence(
            "q",
            ordered,
            [],
            [],
            source_byte_cap=50_000,
            input_byte_cap=100_000,
            context_tokens=100_000,
            output_tokens=1,
            span_text=lambda span: (span["raw_bytes"], span["text"]),
            edge_text=lambda _e: "",
            technology_text=lambda _t: "",
        )
        aliases = assign_evidence_aliases([span["chunk_id"] for span in packed.spans])
        resolved = resolve_evidence_aliases(
            [{"kind": "observed", "text": "push writes the next slot.", "evidence_ids": ["E1"]}],
            aliases,
        )
        kept, valid = review_claims(resolved, {span["chunk_id"] for span in packed.spans})
        self.assertTrue(valid)
        citations = span_citations(packed.spans, kept)
        self.assertEqual(citations[0]["path"], "lib/ring.hpp")
        self.assertEqual(citations[0]["start_line"], 40)
        self.assertEqual(citations[0]["end_line"], 88)
        self.assertEqual(citations[0]["chunk_id"], "c1")
        self.assertEqual(kept[0]["evidence_ids"], ["c1"])


class MultiFileEvidenceSurvivesTests(unittest.TestCase):
    """7. Multi-file questions keep required locations through rank/dedupe/pack."""

    def test_required_paths_survive_dedupe_and_pack(self) -> None:
        spans = [
            _loaded(
                chunk_id="a1",
                path="src/a.py",
                qname="open_gate",
                text="def open_gate():\n    return issue_token()\n",
                symbol_id="sym.a",
                start_byte=0,
                end_byte=40,
            ),
            # Overlapping duplicate of a1 — must drop without dropping b1.
            _loaded(
                chunk_id="a1-dup",
                path="src/a.py",
                qname="open_gate",
                text="def open_gate():\n    return issue_token()\n",
                symbol_id="sym.a",
                start_byte=0,
                end_byte=40,
            ),
            _loaded(
                chunk_id="b1",
                path="src/b.py",
                qname="lookup_account",
                text="def lookup_account(id):\n    return db.get(id)\n",
                symbol_id="sym.b",
                start_byte=0,
                end_byte=50,
            ),
        ]
        fused = materialize_anchors(
            ["sym.a", "sym.b"],
            {"sym.a": ["a1"], "sym.b": ["b1"]},
            {
                "a1": _choice("a1", "sym.a", "symbol_body", 0),
                "b1": _choice("b1", "sym.b", "symbol_body", 0),
            },
            {},
        )
        self.assertEqual([item.chunk_id for item in fused], ["a1", "b1"])
        packed = pack_evidence(
            "q",
            spans,
            [],
            [],
            source_byte_cap=50_000,
            input_byte_cap=100_000,
            context_tokens=100_000,
            output_tokens=1,
            span_text=lambda span: (span["raw_bytes"], span["text"]),
            edge_text=lambda _e: "",
            technology_text=lambda _t: "",
        )
        ids = {span["chunk_id"] for span in packed.spans}
        self.assertIn("a1", ids)
        self.assertIn("b1", ids)
        self.assertNotIn("a1-dup", ids)
        paths = {span["relative_path"] for span in packed.spans}
        self.assertEqual(paths, {"src/a.py", "src/b.py"})
        aliases = assign_evidence_aliases([span["chunk_id"] for span in packed.spans])
        claims = resolve_evidence_aliases(
            [
                {"kind": "observed", "text": "open_gate calls issue_token.", "evidence_ids": ["E1"]},
                {"kind": "observed", "text": "lookup_account reads db.", "evidence_ids": ["E2"]},
            ],
            aliases,
        )
        kept, valid = review_claims(claims, ids)
        self.assertTrue(valid)
        cited = {item for claim in kept for item in claim["evidence_ids"]}
        self.assertEqual(cited, {"a1", "b1"})
        citations = span_citations(packed.spans, kept)
        self.assertEqual({c["path"] for c in citations}, {"src/a.py", "src/b.py"})


class DocsAndImportsPrimaryWhenAskedTests(unittest.TestCase):
    def test_readme_allowed_for_documentation_question(self) -> None:
        spans = [
            _loaded(chunk_id="readme", path="README.md", kind="non_code", text="Install with pip.", raw_bytes=20),
            _loaded(chunk_id="code", path="src/main.py", kind="symbol_body", text="def main():\n    pass\n", raw_bytes=40),
        ]
        ordered = prioritize_question_targets(
            spans,
            "What does the README say about installation?",
            prefer_implementation=False,
        )
        self.assertEqual(ordered[0]["chunk_id"], "readme")


class ExplicitMultipartTargetPackingTests(unittest.TestCase):
    """BUG 1: named multipart function must survive pack ahead of graph neighbours."""

    def _six_parts(self) -> list[dict]:
        parts = []
        cursor = 0
        bodies = [
            "def _answer_events(self):\n    fused = fuse_anchors(lanes)\n",
            "    ranks = {a: i for i, a in enumerate(fused)}\n    edges = self._edges()\n",
            "    spans = materialize_anchors(fused, choices)\n",
            "    named = _named_symbol_spans(db, generation, question)\n",
            "    loaded = _load_span_text(db, generation.id, revision, spans, blobs)\n",
            "    packed = pack_evidence(header, loaded, edges, techs)\n    return packed\n",
        ]
        for index, text in enumerate(bodies):
            raw = text.encode("utf-8")
            parts.append(
                _loaded(
                    chunk_id=f"ae-{index}",
                    path="backend/app/retrieval/answer.py",
                    qname="AnswerService._answer_events",
                    kind="symbol_part",
                    start_line=10 + index * 10,
                    end_line=19 + index * 10,
                    text=text,
                    raw_bytes=len(raw),
                    symbol_id="sym.answer_events",
                    start_byte=cursor,
                    end_byte=cursor + len(raw),
                )
            )
            parts[-1]["question_target"] = True
            cursor += len(raw)
        return parts

    def test_leading_underscore_snake_outranks_graph_neighbours(self) -> None:
        from app.retrieval.evidence_scoring import question_snake_identifiers

        self.assertIn("_answer_events", question_snake_identifiers("How does _answer_events work?"))
        target = self._six_parts()
        competitors = [
            _loaded(
                chunk_id=f"graph-{index}",
                path="backend/app/ingestion/graph_store.py",
                qname=name,
                kind="symbol_body",
                text=("def " + name + "():\n" + ("    x = 1\n" * 80)),
                raw_bytes=4000,
                symbol_id=f"sym.{name}",
                start_byte=index * 5000,
                end_byte=index * 5000 + 4000,
            )
            for index, name in enumerate(
                ("write_graph", "upsert_nodes", "link_edges", "flush_batch", "rebuild_index")
            )
        ]
        # Competitors first (as if fusion ranked them ahead); prioritize must restore target.
        ordered = prioritize_question_targets(
            competitors + target,
            "How does _answer_events work?",
            prefer_implementation=True,
        )
        leading = [span["chunk_id"] for span in ordered[:6]]
        self.assertEqual(leading, [f"ae-{i}" for i in range(6)])

    def test_required_parts_fill_final_prompt_in_source_order(self) -> None:
        target = self._six_parts()
        competitors = [
            _loaded(
                chunk_id="graph-big",
                path="backend/app/ingestion/graph_store.py",
                qname="write_graph",
                kind="symbol_body",
                text="W" * 12_000,
                raw_bytes=12_000,
                symbol_id="sym.write_graph",
                start_byte=0,
                end_byte=12_000,
            ),
            _loaded(
                chunk_id="readme",
                path="README.md",
                kind="non_code",
                text="_answer_events is described in the docs.",
                raw_bytes=40,
            ),
        ]
        ordered = prioritize_question_targets(
            competitors + target,
            "How does _answer_events work?",
            prefer_implementation=True,
        )
        packed = pack_evidence(
            "How does _answer_events work?\n",
            ordered,
            [],
            [],
            source_byte_cap=50_000,
            input_byte_cap=200_000,
            context_tokens=200_000,
            output_tokens=1,
            span_text=lambda span: (span["raw_bytes"], span["text"]),
            edge_text=lambda _e: "",
            technology_text=lambda _t: "",
            required_symbol_ids={"sym.answer_events"},
        )
        kept_ids = [span["chunk_id"] for span in packed.spans]
        self.assertEqual(kept_ids[:6], [f"ae-{i}" for i in range(6)])
        self.assertEqual(packed.incomplete_targets, ())
        prompt = "How does _answer_events work?\n" + "".join(span["text"] for span in packed.spans)
        for index in range(6):
            self.assertIn(target[index]["text"].strip(), prompt)
            self.assertEqual(packed.spans[index]["symbol_id"], "sym.answer_events")
            self.assertEqual(packed.spans[index]["qualified_name"], "AnswerService._answer_events")
            self.assertEqual(packed.spans[index]["start_line"], target[index]["start_line"])
            self.assertEqual(packed.spans[index]["end_line"], target[index]["end_line"])
        # Unrelated neighbour may follow after the required parts, but must not displace them.
        if "graph-big" in kept_ids:
            self.assertGreater(kept_ids.index("graph-big"), 5)

    def test_incomplete_coverage_recorded_when_budget_blocks_parts(self) -> None:
        target = self._six_parts()
        # Tight model-context budget so only the first two required parts fit.
        # Optional neighbours must not steal that budget from the named function.
        two_parts_text = "q\n" + "".join(span["text"] for span in target[:2])
        # estimate_tokens ≈ ceil(utf8_len / 3); leave room for ~2 parts only.
        context_tokens = max(8, conservative_byte_tokens(two_parts_text) + 2)
        packed = pack_evidence(
            "q\n",
            target
            + [
                _loaded(
                    chunk_id="neighbour",
                    path="backend/app/ingestion/graph_store.py",
                    qname="write_graph",
                    kind="symbol_body",
                    text="N" * 20,
                    raw_bytes=20,
                    symbol_id="sym.write_graph",
                )
            ],
            [],
            [],
            source_byte_cap=40,
            input_byte_cap=100_000,
            context_tokens=context_tokens,
            output_tokens=1,
            span_text=lambda span: (span["raw_bytes"], span["text"]),
            edge_text=lambda _e: "",
            technology_text=lambda _t: "",
            required_symbol_ids={"sym.answer_events"},
        )
        kept_ids = [span["chunk_id"] for span in packed.spans]
        self.assertTrue(all(item.startswith("ae-") for item in kept_ids))
        self.assertLess(len(kept_ids), 6)
        self.assertEqual(len(packed.incomplete_targets), 1)
        incomplete = packed.incomplete_targets[0]
        self.assertEqual(incomplete["symbol_id"], "sym.answer_events")
        self.assertEqual(incomplete["qualified_name"], "AnswerService._answer_events")
        self.assertEqual(incomplete["kept_parts"], len(kept_ids))
        self.assertEqual(incomplete["total_parts"], 6)
        self.assertNotIn("neighbour", kept_ids)

class StableEvidenceRankingTests(unittest.TestCase):
    def test_explicit_filename_outranks_other_files(self) -> None:
        spans = [
            _loaded(
                chunk_id="html",
                path="index.html",
                kind="file_overview",
                text="<link href='styles.css'>",
                raw_bytes=25,
            ),
            _loaded(
                chunk_id="readme",
                path="README.md",
                kind="file_overview",
                text="Example repository",
                raw_bytes=20,
            ),
            _loaded(
                chunk_id="css",
                path="styles.css",
                kind="file_overview",
                text="#octocat { width: 384px; margin: 50px auto; }",
                raw_bytes=48,
            ),
        ]

        ordered = prioritize_question_targets(
            spans,
            "What width and margin are assigned in styles.css?",
            prefer_implementation=False,
        )

        self.assertEqual(
            [span["chunk_id"] for span in ordered],
            ["css", "html", "readme"],
        )

    def test_equal_priority_preserves_incoming_retrieval_order(self) -> None:
        spans = [
            _loaded(chunk_id="css", path="styles.css", kind="file_overview"),
            _loaded(chunk_id="html", path="index.html", kind="file_overview"),
            _loaded(chunk_id="readme", path="README.md", kind="file_overview"),
        ]

        ordered = prioritize_question_targets(
            spans,
            "Show me the relevant files.",
            prefer_implementation=False,
        )

        self.assertEqual(
            [span["chunk_id"] for span in ordered],
            ["css", "html", "readme"],
        )


if __name__ == "__main__":
    unittest.main()
