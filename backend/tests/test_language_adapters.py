"""Phase A/B tests for language-agnostic adapters, config discovery, and cross-stack links."""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from app.ingestion.adapters.builtin import FastAPIFrameworkAdapter, NextJsFrameworkAdapter
from app.ingestion.adapters.framework import framework_registry
from app.ingestion.adapters.language import language_registry
from app.ingestion.assemble import SourceBlob, assemble_generation
from app.ingestion.capabilities import build_capability_report
from app.ingestion.ir import CapabilityLevel
from app.ingestion.project_context import discover_project_context
from app.ingestion.protocol_links import extract_client_http_paths, link_http_routes
from app.retrieval.execution_trace import build_investigation_plan, wants_execution_trace_plan
from app.retrieval.question_intent import classify_intent
from app.storage.identity import repository_id

ROOT = Path(__file__).resolve().parent / "fixtures"
CROSS = ROOT / "cross_stack_shipment"
GO = ROOT / "go_warehouse"
SPRING = ROOT / "spring_catalog"
COBOL = ROOT / "unsupported_cobol"
EXEC = ROOT / "execution_flow"


def _blobs(fixture: Path) -> list[SourceBlob]:
    blobs: list[SourceBlob] = []
    for path in sorted(fixture.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(fixture)).replace("\\", "/")
        blobs.append(SourceBlob(rel, "100644", "blob", path.read_bytes()))
    return blobs


def _draft(fixture: Path, repo_slug: str, generation: str):
    repo = str(repository_id(f"https://github.com/org/{repo_slug}"))
    return assemble_generation(repo, "c" * 40, _blobs(fixture), "e" * 64, generation, workers=1)


class RegistryTests(unittest.TestCase):
    def test_language_adapters_registered(self) -> None:
        ids = {adapter.id for adapter in language_registry.all()}
        self.assertIn("python_treesitter", ids)
        self.assertIn("typescript_treesitter", ids)
        self.assertIn("go_language_pack", ids)
        self.assertIn("java_language_pack", ids)

    def test_framework_adapters_registered(self) -> None:
        ids = {adapter.id for adapter in framework_registry.all()}
        self.assertIn("fastapi", ids)
        self.assertIn("nextjs", ids)
        self.assertIn("spring_boot", ids)


class ProjectContextTests(unittest.TestCase):
    def test_discovers_aliases_and_frameworks(self) -> None:
        blobs = {blob.relative_path: blob.payload for blob in _blobs(CROSS)}
        context = discover_project_context(list(blobs), blobs)
        self.assertTrue(any(rule.pattern.startswith("@console") for rule in context.path_alias_rules))
        self.assertIn("fastapi", context.frameworks_hinted)
        self.assertIn("react", context.frameworks_hinted)
        # Alias targets are rooted at the tsconfig directory (web/).
        targets = [target for rule in context.path_alias_rules for target in rule.targets]
        self.assertTrue(any(target.startswith("web/") for target in targets))
    def test_discovers_aspnet_csproj_manifest(self) -> None:
        path = "src/Web.csproj"
        payload = b"""
        <Project>
          <ItemGroup>
            <PackageReference Include="Microsoft.AspNetCore.App" />
          </ItemGroup>
        </Project>
        """

        context = discover_project_context([path], {path: payload})

        self.assertIn(path, context.manifests)
        self.assertIn("csharp", context.languages_hinted)
        self.assertIn("aspnet", context.frameworks_hinted)


class CapabilityReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.draft = _draft(CROSS, "cross-stack-shipment", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

    def test_capability_report_attached(self) -> None:
        report = self.draft.capability_report
        self.assertIsNotNone(report)
        languages = {item["language"]: item for item in report["languages"]}
        self.assertIn("python", languages)
        self.assertGreaterEqual(languages["python"]["level"], int(CapabilityLevel.SEMANTIC_RELATIONSHIP))
        self.assertTrue("tsx" in languages or "typescript" in languages)
        frameworks = {item["framework_id"]: item for item in report["frameworks"]}
        self.assertTrue(frameworks["fastapi"]["detected"])

    def test_python_calls_resolve_in_api(self) -> None:
        calls = [
            edge
            for edge in self.draft.edges
            if edge.relationship == "CALLS" and edge.mentioned_name == "reserve_tracking_code"
        ]
        self.assertTrue(calls)
        self.assertTrue(any(edge.resolution == "resolved" for edge in calls))

    def test_ts_alias_import_resolves(self) -> None:
        imports = [
            edge
            for edge in self.draft.edges
            if edge.relationship == "IMPORTS" and "@console" in edge.mentioned_name
        ]
        self.assertTrue(imports)
        self.assertTrue(any(edge.resolution == "resolved" for edge in imports))

class CapabilityParseStatusTests(unittest.TestCase):
    def _go_capability(self, status: str) -> dict:
        report = build_capability_report(
            repository_id="test-repo",
            generation_id="test-generation",
            relative_paths=["main.go"],
            parse_status_by_path={"main.go": status},
            languages_by_path={"main.go": "go"},
        )
        return next(
            item
            for item in report.as_dict()["languages"]
            if item["language"] == "go"
        )

    def test_unparsed_file_does_not_claim_parser_available(self) -> None:
        row = self._go_capability("not_parsed")

        self.assertFalse(row["parser_available"])
        self.assertFalse(row["syntax_extraction"])
        self.assertEqual(
            row["level"],
            int(CapabilityLevel.SOURCE_DISCOVERABLE),
        )

    def test_successfully_parsed_file_confirms_parser_available(self) -> None:
        row = self._go_capability("parsed")

        self.assertTrue(row["parser_available"])
        self.assertTrue(row["syntax_extraction"])
        self.assertEqual(
            row["level"],
            int(CapabilityLevel.STRUCTURAL_IMPORT),
        )
    def test_syntax_error_still_confirms_parser_execution(self) -> None:
        row = self._go_capability("syntax_error")

        self.assertTrue(row["parser_available"])
        self.assertTrue(row["syntax_extraction"])


class ProtocolLinkTests(unittest.TestCase):
    def test_frontend_backend_http_link_is_protocol_not_calls(self) -> None:
        blobs = {blob.relative_path: blob.payload for blob in _blobs(CROSS)}
        client_text = blobs["web/src/shippingClient.ts"].decode()
        client_hits = [
            (method, path, "web/src/shippingClient.ts")
            for method, path in extract_client_http_paths("web/src/shippingClient.ts", client_text)
        ]
        adapter = FastAPIFrameworkAdapter()
        routes = adapter.discover_routes(
            discover_project_context(list(blobs), blobs),
            relative_paths=list(blobs),
            file_texts={path: payload.decode("utf-8", errors="replace") for path, payload in blobs.items()},
        )
        links = link_http_routes(client_hits, routes)
        self.assertTrue(links)
        self.assertEqual(links[0].resolution, "protocol")
        self.assertIn("/api/v1/shipments", links[0].path)
        # Must not invent a CALLS edge id — protocol links stay separate.
        self.assertEqual(links[0].kind, "http_endpoint")


class GoAndSpringFixtureTests(unittest.TestCase):
    def test_go_fixture_indexes_and_detects_routes(self) -> None:
        draft = _draft(GO, "go-warehouse", "cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        self.assertTrue(any(file.relative_path.endswith(".go") for file in draft.files))
        go_langs = [item for item in draft.capability_report["languages"] if item["language"] == "go"]
        self.assertTrue(go_langs)
        self.assertGreaterEqual(go_langs[0]["level"], int(CapabilityLevel.STRUCTURAL_IMPORT))
        texts = {blob.relative_path: blob.payload.decode() for blob in _blobs(GO)}
        from app.ingestion.adapters.builtin import GoNetHttpFrameworkAdapter

        routes = GoNetHttpFrameworkAdapter().discover_routes(
            discover_project_context(list(texts), {k: v.encode() for k, v in texts.items()}),
            relative_paths=list(texts),
            file_texts=texts,
        )
        self.assertTrue(any(route.path == "/warehouse/stock" for route in routes))

    def test_spring_fixture_detects_controller_routes(self) -> None:
        draft = _draft(SPRING, "spring-catalog", "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        java = [item for item in draft.capability_report["languages"] if item["language"] == "java"]
        self.assertTrue(java)
        texts = {blob.relative_path: blob.payload.decode() for blob in _blobs(SPRING)}
        from app.ingestion.adapters.builtin import SpringBootFrameworkAdapter

        context = discover_project_context(
            list(texts), {path: text.encode() for path, text in texts.items()}
        )
        capability = SpringBootFrameworkAdapter().detect(context, list(texts))
        self.assertTrue(capability.detected)
        routes = SpringBootFrameworkAdapter().discover_routes(
            context, relative_paths=list(texts), file_texts=texts
        )
        self.assertTrue(any("/catalog/skus" in route.path for route in routes))


class UnsupportedLanguageTests(unittest.TestCase):
    def test_cobol_remains_source_discoverable(self) -> None:
        draft = _draft(COBOL, "unsupported-cobol", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
        self.assertTrue(draft.files)
        cobol = [item for item in draft.capability_report["languages"] if item["language"] == "cobol"]
        # Either reported as cobol with limited adapter, or unknown — never claim LEVEL 4.
        if cobol:
            self.assertLess(cobol[0]["level"], int(CapabilityLevel.FRAMEWORK_EXECUTION))
            self.assertFalse(cobol[0]["calls_resolution"])


class ExecutionTraceAgnosticTests(unittest.TestCase):
    def test_shipment_question_does_not_inject_studyplanner(self) -> None:
        question = "Trace the request flow when a dispatcher enqueues a parcel to the shipment API."
        self.assertEqual(classify_intent(question), "execution_trace")
        self.assertTrue(wants_execution_trace_plan(question, "execution_trace"))
        plan = build_investigation_plan(question)
        blob = " ".join(plan.path_hints + plan.feature_terms + plan.lexical_queries).lower()
        self.assertNotIn("generate-plan", blob)
        self.assertNotIn("ruleengine", blob)
        self.assertNotIn("study", blob)

    def test_next_adapter_skips_without_dependency(self) -> None:
        # Express-only package.json must not claim Next.js.
        context = discover_project_context(
            ["package.json"],
            {"package.json": b'{"name":"svc","dependencies":{"express":"4.18.0"}}'},
        )
        capability = NextJsFrameworkAdapter().detect(context, ["src/server.js"])
        self.assertFalse(capability.detected)


class RegressionAndLatencySmokeTests(unittest.TestCase):
    def test_execution_flow_fixture_still_resolves_calls(self) -> None:
        started = time.perf_counter()
        draft = _draft(EXEC, "execution-flow-fixture", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        elapsed = time.perf_counter() - started
        calls = [edge for edge in draft.edges if edge.relationship == "CALLS" and edge.resolution == "resolved"]
        self.assertGreaterEqual(len(calls), 3)
        self.assertIsNotNone(draft.capability_report)
        # Assemble of the small fixture should stay well under a few seconds.
        self.assertLess(elapsed, 30.0)


class ProtocolMethodReliabilityTests(unittest.TestCase):
    def test_nextjs_get_post_route_does_not_match_delete(self) -> None:
        from app.ingestion.adapters.builtin import NextJsFrameworkAdapter

        path = "app/api/plans/route.ts"

        routes = NextJsFrameworkAdapter().discover_routes(
            None,
            relative_paths=[path],
            file_texts={
                path: """
export async function GET(request) {
    return Response.json({});
}

export async function POST(request) {
    return Response.json({});
}
"""
            },
        )

        self.assertEqual(
            {route.method for route in routes},
            {"GET", "POST"},
        )

        links = link_http_routes(
            [("DELETE", "/api/plans", "web/client.ts")],
            routes,
        )

        self.assertFalse(
            links,
            "DELETE must not match a Next.js route exporting only GET and POST",
        )





class HttpProtocolMethodDiscoveryTests(unittest.TestCase):
    def test_spring_mapping_annotations_preserve_http_methods(self) -> None:
        from app.ingestion.adapters.builtin import SpringBootFrameworkAdapter

        path = "src/Controller.java"
        routes = SpringBootFrameworkAdapter().discover_routes(
            None,
            relative_paths=[path],
            file_texts={
                path: (
                    '@GetMapping("/items")\n'
                    'public void getItems() {}\n'
                    '@PostMapping("/items")\n'
                    'public void createItem() {}\n'
                )
            },
        )

        self.assertEqual(
            {(route.method, route.path) for route in routes},
            {("GET", "/items"), ("POST", "/items")},
        )

    def test_spring_request_mapping_respects_explicit_and_unrestricted_methods(self) -> None:
        from app.ingestion.adapters.builtin import SpringBootFrameworkAdapter

        path = "src/Controller.java"
        routes = SpringBootFrameworkAdapter().discover_routes(
            None,
            relative_paths=[path],
            file_texts={
                path: (
                    '@RequestMapping(value="/read", method=RequestMethod.GET)\n'
                    'public void read() {}\n'
                    '@RequestMapping("/any")\n'
                    'public void any() {}\n'
                )
            },
        )

        self.assertEqual(
            {(route.method, route.path) for route in routes},
            {("GET", "/read"), ("*", "/any")},
        )

    def test_go_routes_distinguish_explicit_and_unrestricted_methods(self) -> None:
        from app.ingestion.adapters.builtin import GoNetHttpFrameworkAdapter

        path = "main.go"
        routes = GoNetHttpFrameworkAdapter().discover_routes(
            None,
            relative_paths=[path],
            file_texts={
                path: (
                    'mux.HandleFunc("GET /items", handler)\n'
                    'mux.HandleFunc("/health", handler)\n'
                    'router.Methods("POST", "PATCH").Path("/items")\n'
                )
            },
        )

        self.assertEqual(
            {(route.method, route.path) for route in routes},
            {
                ("GET", "/items"),
                ("*", "/health"),
                ("POST", "/items"),
                ("PATCH", "/items"),
            },
        )


if __name__ == "__main__":
    unittest.main()
