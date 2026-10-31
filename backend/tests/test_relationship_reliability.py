"""Phase B.2: ground-truth reliability, provenance, ROUTES_TO, invalidation, traces."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.evaluation.relationship_eval import (
    assemble_fixture,
    evaluate_all,
    graph_row_round_trip,
    load_manifest,
    provenance_ok,
    score_manifest,
)
from app.ingestion.invalidation import impact_for_changed_paths, relationships_invalidated_by
from app.ingestion.protocol_links import extract_client_http_paths, link_http_routes
from app.ingestion.adapters.builtin import FastAPIFrameworkAdapter
from app.ingestion.project_context import discover_project_context
from app.retrieval.execution_trace import (
    build_investigation_plan,
    expand_execution_trace_evidence,
    wants_execution_trace_plan,
)
from app.retrieval.capability_profile import profile_from_report
from app.retrieval.question_intent import classify_intent


class GroundTruthAccuracyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = evaluate_all()

    def test_typescript_and_python_core_f1(self) -> None:
        by_fixture = {item["fixture"]: item for item in self.report["results"]}
        for name in ("execution_flow", "adversarial_rels", "fastapi_orders", "cross_stack_shipment"):
            result = by_fixture[name]
            for rel, stats in result["by_relationship"].items():
                self.assertGreaterEqual(stats["precision"], 1.0, msg=f"{name}/{rel}")
                self.assertGreaterEqual(stats["recall"], 1.0, msg=f"{name}/{rel}")

    def test_routes_to_stress_f1(self) -> None:
        result = next(item for item in self.report["results"] if item["fixture"] == "routes_to_stress")
        stats = result["by_relationship"]["ROUTES_TO"]
        self.assertEqual(stats["tp"], 2)
        self.assertEqual(stats["fp"], 0)
        self.assertEqual(stats["fn"], 0)

    def test_go_imports_resolved_calls_not_fabricated(self) -> None:
        result = next(item for item in self.report["results"] if item["fixture"] == "go_inventory")
        self.assertEqual(result["by_relationship"]["IMPORTS"]["f1"], 1.0)

    def test_java_rust_csharp_do_not_fabricate_calls(self) -> None:
        for name in ("java_methods", "rust_mods", "csharp_methods"):
            result = next(item for item in self.report["results"] if item["fixture"] == name)
            # Empty expected set: only assert must_not_resolve via scoring side effects (fp==0).
            for stats in result["by_relationship"].values():
                self.assertEqual(stats["fp"], 0, msg=name)


class ProvenanceTests(unittest.TestCase):
    def test_resolved_edges_have_provenance(self) -> None:
        draft = assemble_fixture("execution_flow")
        defects_total = []
        for edge in draft.edges:
            if edge.relationship not in {"CALLS", "IMPORTS", "ROUTES_TO"}:
                continue
            if edge.resolution not in {"resolved", "protocol", "external", "unresolved"}:
                continue
            defects = provenance_ok(edge, generation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
            if edge.resolution in {"resolved", "external", "unresolved"} and edge.relationship != "ROUTES_TO":
                self.assertNotIn("missing_resolution_method", defects, msg=edge.mentioned_name)
                self.assertNotIn("missing_strategy", defects, msg=edge.mentioned_name)
            if edge.resolution == "resolved" and edge.relationship == "CALLS":
                self.assertNotIn("missing_source_range", defects, msg=edge.mentioned_name)
            defects_total.extend(defects)
        # Round-trip preserves fields.
        sample = next(edge for edge in draft.edges if edge.relationship == "CALLS" and edge.resolution == "resolved")
        restored = graph_row_round_trip(sample)
        self.assertEqual(restored.resolution_method, sample.resolution_method)
        self.assertEqual(restored.strategy, sample.strategy)
        self.assertEqual(restored.start_byte, sample.start_byte)
        self.assertEqual(restored.evidence, sample.evidence)

    def test_generation_isolation_in_capability_report(self) -> None:
        draft = assemble_fixture("execution_flow", generation="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        report = draft.capability_report or {}
        self.assertEqual(report.get("generation_id"), "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        foreign = profile_from_report(report, expected_generation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        self.assertEqual(foreign.source, "missing")


class RoutesToStressUnitTests(unittest.TestCase):
    def test_method_and_ambiguity_rules(self) -> None:
        root = Path(__file__).resolve().parent / "fixtures" / "routes_to_stress"
        blobs = {
            str(path.relative_to(root)).replace("\\", "/"): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        texts = {key: value.decode() for key, value in blobs.items()}
        routes = FastAPIFrameworkAdapter().discover_routes(
            discover_project_context(list(blobs), blobs),
            relative_paths=list(blobs),
            file_texts=texts,
        )
        hits = []
        for path, text in texts.items():
            for method, route_path in extract_client_http_paths(path, text):
                hits.append((method, route_path, path))
        links = link_http_routes(hits, routes)
        methods_paths = {(link.method, link.path) for link in links}
        self.assertIn(("GET", "/api/v2/users/{param}"), methods_paths)
        self.assertIn(("POST", "/api/v2/users"), methods_paths)
        self.assertNotIn(("DELETE", "/api/v2/users/{param}"), methods_paths)
        self.assertFalse(any(path.endswith("/items") for _, path in methods_paths))


class InvalidationTests(unittest.TestCase):
    def test_source_change_invalidates_routes_to(self) -> None:
        kinds = relationships_invalidated_by(["web/client.ts", "api/app.py"])
        self.assertIn("ROUTES_TO", kinds)
        self.assertIn("CALLS", kinds)

    def test_tsconfig_and_go_mod_impacts(self) -> None:
        impacts = impact_for_changed_paths(["web/tsconfig.json", "go.mod"])
        kinds = {kind for impact in impacts for kind in impact.relationship_kinds}
        self.assertIn("IMPORTS", kinds)
        self.assertIn("CALLS", kinds)

    def test_unrelated_markdown_change_is_ignored(self) -> None:
        self.assertEqual(relationships_invalidated_by(["README.md"]), set())


class DownstreamTraceTests(unittest.TestCase):
    def test_cross_stack_plan_uses_protocol_strategy(self) -> None:
        question = "Trace the request flow when a dispatcher enqueues a parcel from the console to the shipment API."
        self.assertEqual(classify_intent(question), "execution_trace")
        self.assertTrue(wants_execution_trace_plan(question, "execution_trace"))
        draft = assemble_fixture("cross_stack_shipment", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        profile = profile_from_report(draft.capability_report, expected_generation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        plan = build_investigation_plan(question, profile=profile)
        self.assertIn("follow_protocol_links", plan.strategies)
        self.assertIn("discover_http_routes", plan.strategies)
        routes = [edge for edge in draft.edges if edge.relationship == "ROUTES_TO" and edge.resolution == "protocol"]
        self.assertTrue(routes)
        # Expand must not treat protocol as CALLS.
        generation = type("G", (), {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "repository_id": "r", "parser_version": "v1"})()
        _, updated = expand_execution_trace_evidence(
            db=None,  # type: ignore[arg-type]
            generation=generation,  # type: ignore[arg-type]
            loaded=[],
            edges=[],
            plan=plan,
            profile=profile,
        )
        self.assertFalse(any(item.kind == "calls_resolved" for item in updated.verified))

    def test_backend_only_question_blocks_frontend(self) -> None:
        draft = assemble_fixture("fastapi_orders", "ffffffff-ffff-4fff-8fff-ffffffffffff")
        profile = profile_from_report(draft.capability_report, expected_generation_id="ffffffff-ffff-4fff-8fff-ffffffffffff")
        plan = build_investigation_plan(
            "Trace the frontend React UI update when an order is submitted.",
            profile=profile,
        )
        self.assertIn("frontend_update", plan.blocked_stages)

    def test_adversarial_common_names_do_not_seed_fake_paths(self) -> None:
        draft = assemble_fixture("adversarial_rels", "33333333-3333-4333-8333-333333333333")
        resolved_create = [
            edge
            for edge in draft.edges
            if edge.relationship == "CALLS" and edge.mentioned_name == "create" and edge.resolution == "resolved"
        ]
        self.assertEqual(resolved_create, [])


class ManifestPresenceTests(unittest.TestCase):
    def test_manifests_are_hand_authored(self) -> None:
        for name in (
            "execution_flow",
            "fastapi_orders",
            "cross_stack_shipment",
            "adversarial_rels",
            "routes_to_stress",
            "go_inventory",
            "java_methods",
            "rust_mods",
            "csharp_methods",
        ):
            manifest = load_manifest(name)
            self.assertEqual(manifest["fixture"], name)
            score = score_manifest(manifest, assemble_fixture(manifest["fixture"], manifest.get("generation")))
            self.assertIn("by_relationship", score)


if __name__ == "__main__":
    unittest.main()
