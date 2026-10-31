"""Phase B.3: end-to-end execution-trace reliability."""

from __future__ import annotations

import unittest

from app.evaluation.execution_trace_eval import (
    assemble_fixture,
    evaluate_all,
    evaluate_scenario,
    load_scenarios,
)
from app.retrieval.fusion import allow_edge
from app.retrieval.graph_expand import next_symbol_frontier as expand_frontier


class GroundTruthScenarioTests(unittest.TestCase):
    def test_scenarios_cover_required_languages(self) -> None:
        ids = {row["id"] for row in load_scenarios()}
        self.assertTrue({"A_typescript_execution_flow", "B_python_fastapi_orders", "C_go_warehouse", "D_cross_stack_shipment"} <= ids)
        self.assertTrue(any(item.startswith("E_") for item in ids))

    def test_evaluate_all_graph_recall(self) -> None:
        report = evaluate_all()
        for row in report["scenarios"]:
            self.assertGreaterEqual(
                row["graph"]["recall"],
                1.0,
                msg=f"{row['id']} graph={row['graph']}",
            )
            self.assertEqual(row["graph"]["fp"], 0, msg=row["id"])


class GraphProtocolAdmissionTests(unittest.TestCase):
    def test_routes_to_allowed_for_symbols(self) -> None:
        self.assertTrue(allow_edge("ROUTES_TO", True, "symbol", "symbol"))
        self.assertFalse(allow_edge("ROUTES_TO", True, "file", "symbol"))

    def test_protocol_extends_frontier(self) -> None:
        rows = [
            {
                "relationship_type": "ROUTES_TO",
                "resolution": "protocol",
                "anchor_id": "frontend",
                "other_id": "handler",
                "outgoing": True,
            }
        ]
        roles = {"frontend": "symbol", "handler": "symbol"}
        nxt = expand_frontier({"frontend"}, rows, roles, limit=8)
        self.assertEqual(nxt, ["handler"])


class GoNamespaceCallTests(unittest.TestCase):
    def test_go_package_qualified_calls_resolve(self) -> None:
        draft = assemble_fixture("go_warehouse", "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        resolved = {
            edge.mentioned_name: edge.resolution
            for edge in draft.edges
            if edge.relationship == "CALLS"
            and edge.mentioned_name in {"service.StockLevels", "store.CountBins"}
        }
        self.assertEqual(resolved.get("service.StockLevels"), "resolved")
        self.assertEqual(resolved.get("store.CountBins"), "resolved")
        self.assertTrue(
            any(edge.resolution == "external" for edge in draft.edges if edge.mentioned_name.startswith("json."))
        )

    def test_unused_handler_not_on_stock_path(self) -> None:
        scenario = next(item for item in load_scenarios() if item["id"] == "C_go_warehouse")
        row = evaluate_scenario(scenario)
        self.assertEqual(row["graph"]["fp"], 0)


class CrossStackProtocolExpandTests(unittest.TestCase):
    def test_cross_stack_graph_includes_protocol(self) -> None:
        scenario = next(item for item in load_scenarios() if item["id"] == "D_cross_stack_shipment")
        row = evaluate_scenario(scenario)
        self.assertIn("protocol_present", row["graph"]["notes"])
        self.assertGreaterEqual(row["ablation"]["graph_recall_delta"], 0.5)

    def test_graph_ablation_helps_positive_scenarios(self) -> None:
        report = evaluate_all()
        positives = [row for row in report["scenarios"] if not row["id"].startswith("E_")]
        for row in positives:
            self.assertTrue(row["ablation"]["graph_helps_recall"], msg=row["id"])
            self.assertGreater(row["ablation"]["graph_recall_delta"], 0.0, msg=row["id"])


class AdversarialAbstentionTests(unittest.TestCase):
    def test_bare_create_execute_remain_unresolved(self) -> None:
        scenario = next(item for item in load_scenarios() if item["id"] == "E_adversarial_rels")
        row = evaluate_scenario(scenario)
        self.assertEqual(row["graph"]["fp"], 0)
        self.assertEqual(row["evidence_with_graph"]["fp"], 0)


class GenerationIsolationTests(unittest.TestCase):
    def test_foreign_capability_generation_rejected(self) -> None:
        from app.retrieval.capability_profile import profile_from_report
        from app.retrieval.execution_trace import build_investigation_plan, expand_execution_trace_evidence

        draft = assemble_fixture("fastapi_orders", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        profile = profile_from_report(draft.capability_report, expected_generation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        plan = build_investigation_plan("Trace submit_customer_order to the database.", profile=profile)
        plan.capability_generation_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        generation = type("G", (), {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "repository_id": "r", "parser_version": "v1"})()
        choices, updated = expand_execution_trace_evidence(
            db=None,  # type: ignore[arg-type]
            generation=generation,  # type: ignore[arg-type]
            loaded=[],
            edges=[],
            plan=plan,
            profile=profile,
        )
        self.assertEqual(choices, [])
        self.assertEqual(updated.stop_reason, "capability_generation_mismatch")


if __name__ == "__main__":
    unittest.main()
