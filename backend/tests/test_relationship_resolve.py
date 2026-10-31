"""Phase B.1: relationship resolution precision, provenance, and coverage metrics."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.ingestion.assemble import SourceBlob, assemble_generation
from app.ingestion.invalidation import impact_for_changed_paths, relationships_invalidated_by
from app.ingestion.parse import parse_file
from app.ingestion.relationship_resolve import resolve_call, ImportScope
from app.ingestion.parse import RawMention
from app.storage.identity import repository_id
from app.ingestion.relationship_resolve import (
    resolve_call,
    ImportScope,
    coverage_state,
)

ROOT = Path(__file__).resolve().parent / "fixtures"


def _blobs(fixture: Path) -> list[SourceBlob]:
    return [
        SourceBlob(str(path.relative_to(fixture)).replace("\\", "/"), "100644", "blob", path.read_bytes())
        for path in sorted(fixture.rglob("*"))
        if path.is_file()
    ]


def _draft(name: str, generation: str):
    fixture = ROOT / name
    repo = str(repository_id(f"https://github.com/org/{name}-rel"))
    return assemble_generation(repo, "c" * 40, _blobs(fixture), "e" * 64, generation, workers=1)


def _precision_recall(expected: set[tuple], actual: set[tuple]) -> tuple[float, float]:
    if not expected and not actual:
        return 1.0, 1.0
    if not actual:
        return 0.0, 0.0
    if not expected:
        return 0.0, 1.0
    tp = len(expected & actual)
    precision = tp / len(actual)
    recall = tp / len(expected)
    return precision, recall


class ImportBindingTests(unittest.TestCase):
    def test_ts_named_and_namespace_bindings(self) -> None:
        payload = b'''import { createStudyPlan as makePlan } from "@/lib/service";
import * as auth from "@/lib/auth";
'''
        parsed = parse_file("sample.ts", payload)
        kinds = {(b.kind, b.local_name, b.imported_name) for b in parsed.import_bindings}
        self.assertIn(("named", "makePlan", "createStudyPlan"), kinds)
        self.assertIn(("namespace", "auth", "*"), kinds)

    def test_python_from_import_bindings(self) -> None:
        payload = b"from app.services.dispatcher import reserve_tracking_code as reserve\n"
        parsed = parse_file("app/routers/x.py", payload)
        self.assertTrue(parsed.import_bindings)
        locals_ = {b.local_name for b in parsed.import_bindings}
        self.assertTrue("reserve" in locals_ or "reserve_tracking_code" in locals_)


class ExecutionFlowResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gen = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        cls.draft = _draft("execution_flow", cls.gen)
        cls.symbols = {s.id: s for s in cls.draft.symbols}
        cls.files = {f.id: f for f in cls.draft.files}
        cls.by_qname = {s.qualified_name: s for s in cls.draft.symbols}

    def _resolved_calls(self):
        return [
            edge
            for edge in self.draft.edges
            if edge.relationship == "CALLS" and edge.resolution == "resolved"
        ]

    def test_ground_truth_call_chain_precision(self) -> None:
        post = self.by_qname["POST"]
        service = self.by_qname["createStudyPlan"]
        generate = self.by_qname["generateTasks"]
        save = self.by_qname["savePlan"]
        expected = {
            (post.id, service.id, "createStudyPlan"),
            (service.id, generate.id, "generateTasks"),
            (service.id, save.id, "savePlan"),
        }
        actual = {
            (edge.source_id, edge.target_id, edge.mentioned_name)
            for edge in self._resolved_calls()
            if edge.mentioned_name in {"createStudyPlan", "generateTasks", "savePlan"}
        }
        precision, recall = _precision_recall(expected, actual)
        self.assertGreaterEqual(precision, 1.0)
        self.assertGreaterEqual(recall, 1.0)

    def test_provenance_on_resolved_calls(self) -> None:
        calls = [edge for edge in self._resolved_calls() if edge.mentioned_name == "createStudyPlan"]
        self.assertTrue(calls)
        edge = calls[0]
        self.assertEqual(edge.resolution_method, "static")
        self.assertIn(edge.strategy, {"import_scoped", "local_exact", "import_or_local_exact"})
        self.assertIsNotNone(edge.start_byte)
        self.assertIsNotNone(edge.end_byte)

    def test_namespace_import_call_resolves(self) -> None:
        calls = [
            edge
            for edge in self.draft.edges
            if edge.relationship == "CALLS"
            and edge.mentioned_name in {"auth.requireSession", "requireSession"}
            and edge.resolution == "resolved"
        ]
        # Prefer namespace-qualified form when present.
        qualified = [edge for edge in calls if edge.mentioned_name == "auth.requireSession"]
        self.assertTrue(qualified or calls)
        if qualified:
            self.assertEqual(qualified[0].strategy, "import_namespace")

    def test_external_next_response_not_verified_call(self) -> None:
        bad = [
            edge
            for edge in self.draft.edges
            if edge.relationship == "CALLS"
            and "NextResponse" in edge.mentioned_name
            and edge.resolution == "resolved"
        ]
        self.assertEqual(bad, [])

    def test_capability_metrics_are_measured(self) -> None:
        report = self.draft.capability_report
        self.assertIn("relationship_metrics", report)
        self.assertEqual(report.get("resolver_version"), "relationship_resolve_v1")
        ts = next(item for item in report["languages"] if item["language"] in {"typescript", "tsx"})
        self.assertIn(ts["calls_coverage"], {"supported", "partial"})
        metrics = ts.get("relationship_metrics") or {}
        self.assertIn("CALLS", metrics)
        self.assertGreater(metrics["CALLS"]["verified_resolved"], 0)
        self.assertEqual(
            metrics["CALLS"]["total_sites"],
            metrics["CALLS"]["verified_resolved"]
            + metrics["CALLS"]["ambiguous"]
            + metrics["CALLS"]["external"]
            + metrics["CALLS"]["unresolved"],
        )


class CrossStackProtocolTests(unittest.TestCase):
    def test_routes_to_is_protocol_not_calls(self) -> None:
        draft = _draft("cross_stack_shipment", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        routes = [edge for edge in draft.edges if edge.relationship == "ROUTES_TO"]
        self.assertTrue(routes)
        self.assertTrue(all(edge.resolution == "protocol" for edge in routes))
        self.assertTrue(all(edge.resolution_method == "protocol" for edge in routes))
        # Must not invent CALLS from TS client to Python handler.
        cross = [
            edge
            for edge in draft.edges
            if edge.relationship == "CALLS"
            and edge.resolution == "resolved"
            and "enqueueParcel" in edge.mentioned_name
            and "create_shipment" in (edge.evidence or "")
        ]
        self.assertEqual(cross, [])


class FastApiAndGoTests(unittest.TestCase):
    def test_fastapi_import_scoped_calls(self) -> None:
        draft = _draft("fastapi_orders", "ffffffff-ffff-4fff-8fff-ffffffffffff")
        resolved = [
            edge
            for edge in draft.edges
            if edge.relationship == "CALLS" and edge.resolution == "resolved"
        ]
        names = {edge.mentioned_name for edge in resolved}
        self.assertTrue(names & {"create_order", "reserve_stock", "persist_order"} or len(resolved) >= 1)
        report = draft.capability_report
        py = next(item for item in report["languages"] if item["language"] == "python")
        self.assertIn(py["calls_coverage"], {"supported", "partial"})

    def test_go_fixture_indexes_without_fake_calls(self) -> None:
        draft = _draft("go_warehouse", "cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        self.assertTrue(any(f.relative_path.endswith(".go") for f in draft.files))
        # External/stdlib-style receivers should not become verified CALLS to local symbols.
        verified_http = [
            edge
            for edge in draft.edges
            if edge.relationship == "CALLS"
            and edge.resolution == "resolved"
            and edge.mentioned_name.startswith("http.")
        ]
        self.assertEqual(verified_http, [])


class LimitedLanguageFixtures(unittest.TestCase):
    def test_rust_and_csharp_are_source_indexed(self) -> None:
        rust = _draft("rust_axum", "11111111-1111-4111-8111-111111111111")
        csharp = _draft("csharp_billing", "22222222-2222-4222-8222-222222222222")
        self.assertTrue(any(f.relative_path.endswith(".rs") for f in rust.files))
        self.assertTrue(any(f.relative_path.endswith(".cs") for f in csharp.files))
        for draft in (rust, csharp):
            for lang in draft.capability_report["languages"]:
                if lang["language"] in {"rust", "c_sharp", "csharp"}:
                    self.assertLessEqual(lang["level"], 2)


class InvalidationTests(unittest.TestCase):
    def test_tsconfig_change_invalidates_imports_and_calls(self) -> None:
        kinds = relationships_invalidated_by(["web/tsconfig.json"])
        self.assertIn("IMPORTS", kinds)
        self.assertIn("CALLS", kinds)

    def test_source_change_impact(self) -> None:
        impacts = impact_for_changed_paths(["app/routers/shipments.py"])
        self.assertTrue(impacts)
        self.assertIn("CALLS", impacts[0].relationship_kinds)


class CommonNameGuardTests(unittest.TestCase):
    def test_create_without_scope_stays_unresolved(self) -> None:
        mention = RawMention("CALLS", "create", 0, 6, None)
        result = resolve_call(
            path="x.ts",
            mention=mention,
            symbols_by_file={"x.ts": []},
            local_names={},
            import_scope=ImportScope(),
        )
        self.assertEqual(result.resolution, "unresolved")
        self.assertEqual(result.strategy, "common_name")


class RegressionSmokeTests(unittest.TestCase):
    def test_existing_execution_flow_still_resolves_core_chain(self) -> None:
        draft = _draft("execution_flow", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        resolved = {
            edge.mentioned_name
            for edge in draft.edges
            if edge.relationship == "CALLS" and edge.resolution == "resolved"
        }
        for name in ("createStudyPlan", "generateTasks", "savePlan", "requireSession", "validatePlanInput"):
            self.assertIn(name, resolved)


class CoverageStateTests(unittest.TestCase):

    def test_all_internal_calls_resolved_is_supported(self):
        result = coverage_state(
            {
                "total_sites": 10,
                "verified_resolved": 7,
                "external": 3,
                "ambiguous": 0,
                "unresolved": 0,
            },
            adapter_claims=True,
        )
        self.assertEqual(result, (True, "supported"))

    def test_some_internal_calls_resolved_is_partial(self):
        result = coverage_state(
            {
                "total_sites": 10,
                "verified_resolved": 2,
                "external": 3,
                "ambiguous": 2,
                "unresolved": 3,
            },
            adapter_claims=True,
        )
        self.assertEqual(result, (True, "partial"))

    def test_no_internal_calls_resolved(self):
        result = coverage_state(
            {
                "total_sites": 10,
                "verified_resolved": 0,
                "external": 3,
                "ambiguous": 2,
                "unresolved": 5,
            },
            adapter_claims=True,
        )
        self.assertEqual(result, (False, "zero_verified"))

    def test_external_only_calls_do_not_count_as_internal_sites(self):
        result = coverage_state(
            {
                "total_sites": 10,
                "verified_resolved": 0,
                "external": 10,
                "ambiguous": 0,
                "unresolved": 0,
            },
            adapter_claims=True,
        )
        self.assertEqual(result, (False, "no_sites"))





class HttpProtocolAssemblyTests(unittest.TestCase):
    """Exercise route discovery and ROUTES_TO creation through assembly."""

    def _assemble(self, name: str, sources: dict[str, str], generation: str):
        blobs = [
            SourceBlob(
                path,
                "100644",
                "blob",
                content.encode("utf-8"),
            )
            for path, content in sorted(sources.items())
        ]
        repo = str(repository_id(
            f"https://github.com/org/{name}-protocol-test"
        ))
        return assemble_generation(
            repo,
            "a" * 40,
            blobs,
            "b" * 64,
            generation,
            workers=1,
        )

    def _route_edges(self, draft):
        return [
            edge for edge in draft.edges
            if edge.relationship == "ROUTES_TO"
        ]

    def test_spring_get_matches_get_but_not_post(self) -> None:
        sources = {
            "pom.xml": "<project></project>",
            "src/main/java/com/example/ItemsController.java": (
                "package com.example;\n"
                "@RestController\n"
                "public class ItemsController {\n"
                '    @GetMapping("/items")\n'
                "    public String getItems() { return \"ok\"; }\n"
                "}\n"
            ),
            "web/getClient.ts": (
                'export const getItems = () => fetch("/items");\n'
            ),
            "web/postClient.ts": (
                'export const postItems = () => '
                'fetch("/items", { method: "POST" });\n'
            ),
        }

        draft = self._assemble(
            "spring-http-methods",
            sources,
            "11111111-1111-4111-8111-111111111111",
        )
        links = self._route_edges(draft)

        self.assertEqual(len(links), 1, links)
        self.assertEqual(links[0].mentioned_name, "GET /items")
        self.assertIn("getClient.ts", links[0].evidence)
        self.assertIn("ItemsController.java", links[0].evidence)

    def test_go_explicit_and_unrestricted_routes(self) -> None:
        sources = {
            "go.mod": (
                "module example.com/protocol-test\n"
                "go 1.22\n"
            ),
            "main.go": (
                "package main\n"
                'import "net/http"\n'
                "func handler(w http.ResponseWriter, r *http.Request) {}\n"
                "func main() {\n"
                '    http.HandleFunc("GET /items", handler)\n'
                '    http.HandleFunc("/health", handler)\n'
                "}\n"
            ),
            "web/getClient.ts": (
                'export const getItems = () => fetch("/items");\n'
            ),
            "web/postClient.ts": (
                'export const postItems = () => '
                'fetch("/items", { method: "POST" });\n'
            ),
            "web/healthClient.ts": (
                'export const health = () => fetch("/health");\n'
            ),
        }

        draft = self._assemble(
            "go-http-methods",
            sources,
            "22222222-2222-4222-8222-222222222222",
        )
        links = self._route_edges(draft)

        actual = {
            (edge.mentioned_name, edge.evidence.split(" -> ")[0])
            for edge in links
        }

        self.assertEqual(
            actual,
            {
                ("GET /items", "web/getClient.ts"),
                ("GET /health", "web/healthClient.ts"),
            },
        )

        self.assertTrue(
            all(edge.resolution == "protocol" for edge in links)
        )
        self.assertTrue(
            all(edge.resolution_method == "protocol" for edge in links)
        )



class IrRelationshipResolutionTests(unittest.TestCase):
    def test_import_scope_and_call_resolution_accept_ir_document(self) -> None:
        from types import SimpleNamespace

        from app.ingestion.ir import parsed_file_to_ir
        from app.ingestion.parse import parse_file
        from app.ingestion.relationship_resolve import (
            ResolutionIndex,
            build_import_scope,
            resolve_call,
        )

        path = "app.py"
        payload = (
            b"from pkg import helper\n\n"
            b"def run():\n"
            b"    helper()\n"
        )

        parsed = parse_file(path, payload)
        document = parsed_file_to_ir(
            path,
            parsed,
            adapter_id="python_treesitter",
        )

        app_symbols = [
            SimpleNamespace(
                id=f"app-symbol-{index}",
                kind=symbol.kind,
                name=symbol.name,
                qualified_name=symbol.qualified_name,
            )
            for index, symbol in enumerate(parsed.symbols)
        ]

        target_symbol = SimpleNamespace(
            id="pkg-helper-id",
            kind="function",
            name="helper",
            qualified_name="helper",
        )

        symbols_by_file = {
            "app.py": app_symbols,
            "pkg.py": [target_symbol],
        }

        index = ResolutionIndex(
            paths={"app.py", "pkg.py"},
            symbols_by_file=symbols_by_file,
            local_names={"app.py": {}, "pkg.py": {}},
            import_targets_fn=lambda source, specifier, paths, **kwargs: (
                ["pkg.py"] if specifier == "pkg" else []
            ),
        )

        scope = build_import_scope(path, document, index)

        self.assertEqual(
            scope.names.get("helper"),
            ["pkg-helper-id"],
        )

        call_relationship = next(
            relationship
            for relationship in document.relationships
            if relationship.kind == "CALLS"
        )

        result = resolve_call(
            path=path,
            mention=call_relationship,
            symbols_by_file=symbols_by_file,
            local_names={},
            import_scope=scope,
        )

        self.assertEqual(result.resolution, "resolved")
        self.assertEqual(result.target_id, "pkg-helper-id")
        self.assertEqual(result.strategy, "import_scoped")


if __name__ == "__main__":
    unittest.main()
