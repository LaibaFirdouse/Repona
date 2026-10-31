"""Regression tests for execution-trace retrieval and CALLS reconstruction."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.ingestion.assemble import SourceBlob, assemble_generation
from app.retrieval.execution_trace import (
    build_investigation_plan,
    order_spans_by_execution_stage,
    partial_trace_claims,
    wants_execution_trace_plan,
    _http_routes_in_text,
)
from app.retrieval.packet import assign_evidence_aliases, pack_evidence, review_claims, resolve_evidence_aliases
from app.retrieval.question_intent import classify_intent
from app.storage.identity import repository_id

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "execution_flow"
REPO = str(repository_id("https://github.com/org/execution-flow-fixture"))
GENERATION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _blobs() -> list[SourceBlob]:
    blobs: list[SourceBlob] = []
    for path in sorted(FIXTURE.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(FIXTURE)).replace("\\", "/")
        blobs.append(SourceBlob(rel, "100644", "blob", path.read_bytes()))
    return blobs


def _draft():
    return assemble_generation(REPO, "c" * 40, _blobs(), "e" * 64, GENERATION, workers=1)


class IntentRoutingTests(unittest.TestCase):
    def test_overview_stays_architecture(self) -> None:
        self.assertEqual(classify_intent("Tell me about this repository."), "architecture_question")
        self.assertFalse(wants_execution_trace_plan("Tell me about this repository.", "architecture_question"))

    def test_request_flow_is_execution_trace(self) -> None:
        question = "Explain the exact request flow when a user generates a study plan."
        self.assertEqual(classify_intent(question), "execution_trace")
        self.assertTrue(wants_execution_trace_plan(question, "execution_trace"))

    def test_plan_extracts_feature_and_stages(self) -> None:
        plan = build_investigation_plan(
            "Trace what happens when a user generates a new study plan from frontend to database."
        )
        self.assertTrue(plan.feature_terms)
        keys = {stage.key for stage in plan.stages}
        self.assertIn("database", keys)
        self.assertIn("frontend_update", keys)
        self.assertTrue(
            any("generate" in term.lower() or "plan" in term.lower() for term in plan.feature_terms)
        )
        # Must not invent StudyPlanner layout paths absent from the question text.
        invented = {"generate-plan", "api/generate-plan", "ruleEngine", "lib/ai", "lib/db", "lib/auth", "nextauth"}
        self.assertFalse(invented & {hint.lower() for hint in plan.path_hints})

    def test_order_question_does_not_inject_study_plan_paths(self) -> None:
        plan = build_investigation_plan(
            "Trace the request flow when a customer submits an order through the API to the database."
        )
        blob = " ".join(plan.path_hints + plan.feature_terms + plan.lexical_queries).lower()
        self.assertNotIn("generate-plan", blob)
        self.assertNotIn("ruleengine", blob)
        self.assertNotIn("connectdb", blob)
        keys = {stage.key for stage in plan.stages}
        self.assertIn("api_handler", keys)
        self.assertIn("database", keys)
        self.assertNotIn("frontend_update", keys)


class AssembleCallChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.draft = _draft()
        cls.symbols = {symbol.id: symbol for symbol in cls.draft.symbols}
        cls.files = {file.id: file for file in cls.draft.files}
        cls.by_qname = {symbol.qualified_name: symbol for symbol in cls.draft.symbols}

    def _calls(self, mentioned: str | None = None):
        rows = [edge for edge in self.draft.edges if edge.relationship == "CALLS"]
        if mentioned is not None:
            rows = [edge for edge in rows if edge.mentioned_name == mentioned]
        return rows

    def test_alias_imports_resolve(self) -> None:
        imports = [edge for edge in self.draft.edges if edge.relationship == "IMPORTS" and edge.mentioned_name.startswith("@/")]
        self.assertTrue(imports)
        self.assertTrue(all(edge.resolution == "resolved" for edge in imports if "unused" not in edge.mentioned_name or True))
        resolved = [edge for edge in imports if edge.resolution == "resolved"]
        self.assertGreaterEqual(len(resolved), 4)

    def test_api_to_service_call(self) -> None:
        calls = self._calls("createStudyPlan")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].resolution, "resolved")
        self.assertEqual(self.symbols[calls[0].target_id].qualified_name, "createStudyPlan")
        self.assertEqual(self.files[self.symbols[calls[0].target_id].file_id].relative_path, "lib/service.ts")

    def test_service_to_database_and_rules(self) -> None:
        generate = self._calls("generateTasks")
        save = self._calls("savePlan")
        self.assertEqual(len(generate), 1)
        self.assertEqual(generate[0].resolution, "resolved")
        self.assertEqual(self.symbols[generate[0].target_id].qualified_name, "generateTasks")
        self.assertEqual(len(save), 1)
        self.assertEqual(save[0].resolution, "resolved")
        self.assertEqual(self.symbols[save[0].target_id].qualified_name, "savePlan")

    def test_auth_and_validation_calls(self) -> None:
        self.assertEqual(self._calls("requireSession")[0].resolution, "resolved")
        self.assertEqual(self._calls("validatePlanInput")[0].resolution, "resolved")
        self.assertEqual(self._calls("connectDB")[0].resolution, "resolved")

    def test_multi_file_call_chain_order(self) -> None:
        """POST → createStudyPlan → generateTasks / savePlan establishes the service chain."""
        post = self.by_qname["POST"]
        service = self.by_qname["createStudyPlan"]
        rules = self.by_qname["generateTasks"]
        db_save = self.by_qname["savePlan"]
        edges = {(edge.source_id, edge.target_id) for edge in self._calls() if edge.resolution == "resolved"}
        self.assertIn((post.id, service.id), edges)
        self.assertIn((service.id, rules.id), edges)
        self.assertIn((service.id, db_save.id), edges)

    def test_imported_but_never_invoked_is_not_a_calls_edge(self) -> None:
        mentioned = [edge.mentioned_name for edge in self._calls()]
        self.assertNotIn("generateTasksAlt", mentioned)

    def test_similar_name_without_call_is_not_linked_from_route(self) -> None:
        preview = self.by_qname["createStudyPlanPreview"]
        post = self.by_qname["POST"]
        linked = [
            edge
            for edge in self._calls()
            if edge.source_id == post.id and edge.target_id == preview.id
        ]
        self.assertEqual(linked, [])

    def test_http_path_discovery(self) -> None:
        form = (FIXTURE / "components" / "PlanForm.tsx").read_text()
        routes = _http_routes_in_text(form)
        self.assertEqual(routes, [("/api/plans", "POST")])


class EvidenceOrganisationTests(unittest.TestCase):
    def test_stage_ordering_puts_api_before_database(self) -> None:
        plan = build_investigation_plan("Trace generate study plan request flow through API and database")
        spans = [
            {
                "chunk_id": "db",
                "relative_path": "lib/db.ts",
                "qualified_name": "savePlan",
                "chunk_kind": "symbol_body",
                "text": "export async function savePlan() { return { id: '1' } }",
            },
            {
                "chunk_id": "api",
                "relative_path": "app/api/plans/route.ts",
                "qualified_name": "POST",
                "chunk_kind": "symbol_body",
                "text": "export async function POST() { await createStudyPlan() }",
            },
            {
                "chunk_id": "ui",
                "relative_path": "components/PlanForm.tsx",
                "qualified_name": "PlanForm.onGenerate",
                "chunk_kind": "symbol_body",
                "text": 'await fetch("/api/plans", { method: "POST" })',
            },
        ]
        ordered = order_spans_by_execution_stage(spans, plan)
        paths = [span["relative_path"] for span in ordered]
        self.assertLess(paths.index("components/PlanForm.tsx"), paths.index("app/api/plans/route.ts"))
        self.assertLess(paths.index("app/api/plans/route.ts"), paths.index("lib/db.ts"))

    def test_required_call_site_survives_pack(self) -> None:
        spans = [
            {
                "chunk_id": "c1",
                "symbol_id": "s1",
                "relative_path": "app/api/plans/route.ts",
                "qualified_name": "POST",
                "chunk_kind": "symbol_body",
                "text": "await createStudyPlan(input, session.userId)",
                "context_prefix": "",
                "start_line": 1,
                "end_line": 10,
                "source_start_byte": 0,
                "source_end_byte": 40,
                "raw_bytes": 40,
                "question_target": True,
            },
            {
                "chunk_id": "c2",
                "symbol_id": "s2",
                "relative_path": "README.md",
                "qualified_name": "",
                "chunk_kind": "non_code",
                "text": "createStudyPlan generateTasks /api/plans",
                "context_prefix": "",
                "start_line": 1,
                "end_line": 4,
                "source_start_byte": 0,
                "source_end_byte": 40,
                "raw_bytes": 40,
            },
        ]

        def span_text(span):
            piece = f"\n{span['text']}"
            return span["raw_bytes"], piece

        packed = pack_evidence(
            "header",
            spans,
            [],
            [],
            context_tokens=8000,
            output_tokens=500,
            margin_tokens=100,
            count_tokens=lambda text: max(1, len(text) // 4),
            span_text=span_text,
            edge_text=lambda edge: "",
            technology_text=lambda tech: "",
            required_symbol_ids={"s1"},
        )
        kept_ids = {span["chunk_id"] for span in packed.spans}
        self.assertIn("c1", kept_ids)

    def test_partial_trace_claims_require_real_ids(self) -> None:
        from app.retrieval.execution_trace import InvestigationPlan, VerifiedRelationship, TraceStage

        plan = InvestigationPlan(
            intent="execution_trace",
            stages=[TraceStage("api_handler", "api handler")],
            missing_stages=["database"],
            verified=[
                VerifiedRelationship(
                    kind="http_route",
                    caller_path="components/PlanForm.tsx",
                    caller_symbol="PlanForm.onGenerate",
                    callee_name="POST /api/plans",
                    callee_path="app/api/plans/route.ts",
                    evidence_chunk_id="c1",
                )
            ],
        )
        spans = [
            {
                "chunk_id": "c1",
                "relative_path": "components/PlanForm.tsx",
                "qualified_name": "PlanForm.onGenerate",
                "chunk_kind": "symbol_body",
                "text": 'fetch("/api/plans")',
            }
        ]
        claims = partial_trace_claims(plan, spans)
        self.assertTrue(claims)
        aliases = assign_evidence_aliases(["c1"])
        resolved = resolve_evidence_aliases(
            [{"kind": "observed", "text": claims[0]["text"], "evidence_ids": ["E1"]}],
            aliases,
        )
        kept, valid = review_claims(resolved, {"c1"})
        self.assertTrue(kept)
        self.assertTrue(valid)
        self.assertIn("database", claims[0]["text"])
        self.assertIn("applicable", claims[0]["text"])


class FastAPIOrdersFixtureTests(unittest.TestCase):
    FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fastapi_orders"
    REPO = str(repository_id("https://github.com/org/fastapi-orders-fixture"))
    GENERATION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    @classmethod
    def setUpClass(cls) -> None:
        blobs = []
        for path in sorted(cls.FIXTURE.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(cls.FIXTURE)).replace("\\", "/")
            blobs.append(SourceBlob(rel, "100644", "blob", path.read_bytes()))
        cls.draft = assemble_generation(cls.REPO, "d" * 40, blobs, "e" * 64, cls.GENERATION, workers=1)
        cls.symbols = {symbol.id: symbol for symbol in cls.draft.symbols}
        cls.files = {file.id: file for file in cls.draft.files}
        cls.by_qname = {symbol.qualified_name: symbol for symbol in cls.draft.symbols}

    def _calls(self, mentioned: str | None = None):
        rows = [edge for edge in self.draft.edges if edge.relationship == "CALLS"]
        if mentioned is not None:
            rows = [edge for edge in rows if edge.mentioned_name == mentioned]
        return rows

    def _symbol_in(self, name: str, relative_path: str):
        for symbol in self.draft.symbols:
            if symbol.name == name and self.files[symbol.file_id].relative_path == relative_path:
                return symbol
        self.fail(f"missing {name} in {relative_path}")

    def test_route_to_service_to_db_chain(self) -> None:
        route = self.by_qname["submit_customer_order"]
        service = self._symbol_in("place_customer_order", "app/services/order_service.py")
        inventory = self.by_qname["assert_sku_available"]
        persist = self.by_qname["persist_order_record"]
        edges = {(edge.source_id, edge.target_id) for edge in self._calls() if edge.resolution == "resolved"}
        self.assertIn((route.id, service.id), edges)
        self.assertIn((service.id, inventory.id), edges)
        self.assertIn((service.id, persist.id), edges)

    def test_preview_helper_not_called_from_route(self) -> None:
        preview = self.by_qname["preview_customer_order"]
        route = self.by_qname["submit_customer_order"]
        linked = [
            edge
            for edge in self._calls()
            if edge.source_id == route.id and edge.target_id == preview.id
        ]
        self.assertEqual(linked, [])

    def test_unrelated_same_name_not_preferred_via_import(self) -> None:
        calls = self._calls("place_customer_order")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].resolution, "resolved")
        target = self.symbols[calls[0].target_id]
        self.assertEqual(self.files[target.file_id].relative_path, "app/services/order_service.py")

    def test_cli_style_question_omits_frontend_stage(self) -> None:
        plan = build_investigation_plan(
            "Trace how place_customer_order persists an order in the database end to end"
        )
        keys = {stage.key for stage in plan.stages}
        self.assertIn("database", keys)
        self.assertNotIn("frontend_update", keys)
        self.assertIn("frontend_update", plan.not_applicable_stages)


class NegativeEvidenceTests(unittest.TestCase):
    def test_readme_mention_is_not_a_resolved_call(self) -> None:
        draft = _draft()
        readme_calls = [
            edge
            for edge in draft.edges
            if edge.relationship == "CALLS" and "README" in (
                next((file.relative_path for file in draft.files if file.id == edge.source_id), "")
            )
        ]
        # README is non-code / not parsed for CALLS from function symbols.
        self.assertEqual(readme_calls, [])


class TsconfigAliasTests(unittest.TestCase):
    def test_tsconfig_paths_drive_alias_resolution(self) -> None:
        from app.ingestion.assemble import _parse_tsconfig_paths, _js_alias_targets

        rules = _parse_tsconfig_paths(
            b'{"compilerOptions":{"baseUrl":".","paths":{"@lib/*":["packages/lib/*"]}}}'
        )
        self.assertEqual(rules, [("@lib/*", ["packages/lib"])])
        paths = {"packages/lib/db.ts", "lib/db.ts"}
        resolved = _js_alias_targets("@lib/db", paths, alias_rules=rules)
        self.assertEqual(resolved, ["packages/lib/db.ts"])
        # Without a matching rule hit, do not fall back when rules were provided.
        self.assertEqual(_js_alias_targets("@/db", paths, alias_rules=rules), [])


if __name__ == "__main__":
    unittest.main()
