"""Phase A.1: capability-aware execution-trace planning regressions."""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.ingestion.assemble import SourceBlob, assemble_generation
from app.retrieval.capability_profile import (
    CapabilityState,
    profile_from_report,
    unknown_profile,
)
from app.retrieval.execution_trace import (
    InvestigationPlan,
    build_investigation_plan,
    expand_execution_trace_evidence,
    order_spans_by_execution_stage,
    partial_trace_claims,
)
from app.retrieval.packet import pack_evidence
from app.storage.identity import repository_id

ROOT = Path(__file__).resolve().parent / "fixtures"


def _blobs(fixture: Path) -> list[SourceBlob]:
    return [
        SourceBlob(str(path.relative_to(fixture)).replace("\\", "/"), "100644", "blob", path.read_bytes())
        for path in sorted(fixture.rglob("*"))
        if path.is_file()
    ]


def _draft(name: str, generation: str):
    fixture = ROOT / name
    repo = str(repository_id(f"https://github.com/org/{name}-cap"))
    return assemble_generation(repo, "c" * 40, _blobs(fixture), "e" * 64, generation, workers=1)


def _profile(draft, generation: str):
    report = dict(draft.capability_report or {})
    report["generation_id"] = generation
    return profile_from_report(report, expected_generation_id=generation)


class CapabilityProfileNormalisationTests(unittest.TestCase):
    def test_next_typescript_report_supports_routes_and_calls(self) -> None:
        gen = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        draft = _draft("execution_flow", gen)
        profile = _profile(draft, gen)
        self.assertEqual(profile.generation_id, gen)
        self.assertEqual(profile.source, "report")
        self.assertIn("nextjs", profile.frameworks)
        self.assertIn(
            profile.state("calls_resolution"),
            {CapabilityState.SUPPORTED, CapabilityState.PARTIAL},
        )
        self.assertEqual(profile.state("http_route_discovery"), CapabilityState.SUPPORTED)
        self.assertEqual(profile.state("frontend_ui"), CapabilityState.SUPPORTED)
        plan = build_investigation_plan(
            "Trace the request flow when a user generates a plan from frontend to database.",
            profile=profile,
        )
        self.assertIn("follow_verified_calls", plan.strategies)
        self.assertIn("discover_http_routes", plan.strategies)

    def test_fastapi_backend_marks_frontend_not_applicable(self) -> None:
        gen = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        draft = _draft("fastapi_orders", gen)
        profile = _profile(draft, gen)
        self.assertIn("fastapi", profile.frameworks)
        self.assertEqual(profile.state("frontend_ui"), CapabilityState.NOT_APPLICABLE)
        self.assertEqual(profile.state("http_route_discovery"), CapabilityState.SUPPORTED)
        plan = build_investigation_plan(
            "Trace the request flow when a customer submits an order through the API to the database.",
            profile=profile,
        )
        keys = {stage.key for stage in plan.stages}
        self.assertIn("api_handler", keys)
        self.assertIn("database", keys)
        self.assertNotIn("frontend_update", keys)
        self.assertIn("frontend_update", plan.not_applicable_stages)
        self.assertNotIn("generate-plan", " ".join(plan.path_hints + plan.feature_terms).lower())

    def test_frontend_question_on_backend_only_is_blocked(self) -> None:
        gen = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        profile = _profile(_draft("fastapi_orders", gen), gen)
        plan = build_investigation_plan(
            "Trace the frontend React UI state update when an order is submitted.",
            profile=profile,
        )
        self.assertIn("frontend_update", plan.blocked_stages)
        self.assertEqual(plan.stage_status.get("frontend_update"), "blocked_unsupported")

    def test_spring_partial_calls_strategy(self) -> None:
        gen = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        profile = _profile(_draft("spring_catalog", gen), gen)
        self.assertIn("spring_boot", profile.frameworks)
        self.assertIn(profile.state("calls_resolution"), {CapabilityState.PARTIAL, CapabilityState.UNSUPPORTED})
        plan = build_investigation_plan(
            "Trace the API handler flow for listing catalog SKUs.",
            profile=profile,
        )
        self.assertIn("api_handler", {stage.key for stage in plan.stages})
        self.assertTrue(
            "inspect_source_call_sites" in plan.strategies or "lexical_catalogue_search" in plan.strategies
        )
        if profile.state("calls_resolution") == CapabilityState.UNSUPPORTED:
            self.assertNotIn("follow_verified_calls", plan.strategies)

    def test_cobol_uses_lexical_only(self) -> None:
        gen = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        profile = _profile(_draft("unsupported_cobol", gen), gen)
        self.assertEqual(profile.state("calls_resolution"), CapabilityState.UNSUPPORTED)
        plan = build_investigation_plan(
            "Trace the execution flow of the legacy batch program.",
            profile=profile,
        )
        self.assertIn("lexical_catalogue_search", plan.strategies)
        self.assertNotIn("follow_verified_calls", plan.strategies)
        self.assertNotIn("discover_http_routes", plan.strategies)

    def test_cross_stack_enables_protocol_strategy(self) -> None:
        gen = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        profile = _profile(_draft("cross_stack_shipment", gen), gen)
        self.assertEqual(profile.state("multi_language"), CapabilityState.SUPPORTED)
        self.assertIn(profile.state("protocol_links"), {CapabilityState.SUPPORTED, CapabilityState.PARTIAL})
        plan = build_investigation_plan(
            "Trace the request flow when a dispatcher enqueues a parcel from the console to the shipment API.",
            profile=profile,
        )
        self.assertIn("follow_protocol_links", plan.strategies)
        self.assertIn("discover_http_routes", plan.strategies)
        blob = " ".join(plan.path_hints + plan.feature_terms + plan.lexical_queries).lower()
        self.assertNotIn("studyplanner", blob)
        self.assertNotIn("generate-plan", blob)

    def test_unknown_capability_metadata_is_conservative(self) -> None:
        profile = unknown_profile("99999999-9999-4999-8999-999999999999", reason="missing_capability_report")
        self.assertEqual(profile.state("calls_resolution"), CapabilityState.UNKNOWN)
        self.assertNotEqual(profile.state("calls_resolution"), CapabilityState.UNSUPPORTED)
        plan = build_investigation_plan(
            "Trace the request flow through the API.",
            profile=profile,
        )
        self.assertIn("lexical_catalogue_search", plan.strategies)
        self.assertIn("inspect_source_call_sites", plan.strategies)

    def test_foreign_generation_report_is_rejected(self) -> None:
        other = profile_from_report(
            {
                "generation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "languages": [{"language": "python", "level": 3, "calls_resolution": True, "file_count": 1}],
                "frameworks": [],
            },
            expected_generation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        self.assertEqual(other.source, "missing")
        self.assertTrue(any("ignored report" in note for note in other.notes))


class CallsFacetAggregationTests(unittest.TestCase):
    GENERATION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    def _profile(self, *languages):
        return profile_from_report(
            {
                "generation_id": self.GENERATION,
                "languages": list(languages),
                "frameworks": [],
            },
            expected_generation_id=self.GENERATION,
        )

    def _language(self, name, coverage, level=3):
        return {
            "language": name,
            "file_count": 1,
            "level": level,
            "calls_resolution": coverage in {"supported", "partial"},
            "calls_coverage": coverage,
        }

    def test_all_supported_languages_are_supported(self):
        profile = self._profile(
            self._language("python", "supported"),
            self._language("typescript", "supported"),
        )

        self.assertEqual(
            profile.state("calls_resolution"),
            CapabilityState.SUPPORTED,
        )

    def test_supported_and_partial_languages_produce_partial(self):
        profile = self._profile(
            self._language("python", "supported"),
            self._language("typescript", "partial"),
        )

        self.assertEqual(
            profile.state("calls_resolution"),
            CapabilityState.PARTIAL,
        )

    def test_supported_and_unsupported_languages_produce_partial(self):
        profile = self._profile(
            self._language("python", "supported"),
            self._language("go", "unsupported", level=2),
        )

        self.assertEqual(
            profile.state("calls_resolution"),
            CapabilityState.PARTIAL,
        )

    def test_only_no_sites_produces_unknown(self):
        profile = self._profile(
            self._language("python", "no_sites"),
            self._language("typescript", "no_sites"),
        )

        self.assertEqual(
            profile.state("calls_resolution"),
            CapabilityState.UNKNOWN,
        )

    def test_non_code_rows_do_not_affect_calls_capability(self):
        profile = self._profile(
            self._language("python", "no_sites"),
            self._language("markdown", "supported", level=0),
        )

        self.assertEqual(
            profile.state("calls_resolution"),
            CapabilityState.UNKNOWN,
        )
    def test_all_unsupported_languages_are_unsupported(self):
        profile = self._profile(
            self._language("go", "unsupported", level=2),
            self._language("java", "unsupported", level=2),
        )

        self.assertEqual(
            profile.state("calls_resolution"),
            CapabilityState.UNSUPPORTED,
        )
    def test_no_sites_does_not_downgrade_supported_language(self):
        profile = self._profile(
            self._language("python", "supported"),
            self._language("typescript", "no_sites"),
        )

        self.assertEqual(
            profile.state("calls_resolution"),
            CapabilityState.SUPPORTED,
        )


    def test_supported_detail_excludes_external_calls(self):
        python = self._language("python", "supported")
        python["relationship_metrics"] = {
            "CALLS": {
                "total_sites": 10,
                "verified_resolved": 7,
                "external": 3,
                "protocol": 0,
            }
        }

        profile = self._profile(python)
        detail = profile.facets["calls_resolution"].detail

        self.assertIn("7/7 internal calls", detail)
        self.assertNotIn("7/10", detail)


class ExpandRespectsStrategiesTests(unittest.TestCase):
    def test_expand_skips_calls_when_unsupported(self) -> None:
        gen = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        profile = _profile(_draft("unsupported_cobol", gen), gen)
        plan = build_investigation_plan("Trace legacy batch execution.", profile=profile)
        edge = SimpleNamespace(
            relationship_type="CALLS",
            resolution="resolved",
            outgoing=True,
            other_id="sym-target",
            anchor_id="sym-source",
            mentioned_name="DISPLAY",
        )
        generation = SimpleNamespace(id=gen, repository_id="repo", parser_version="p")
        # No DB session needed when follow_calls is disabled — edges ignored before queries.
        choices, updated = expand_execution_trace_evidence(
            db=None,  # type: ignore[arg-type]
            generation=generation,  # type: ignore[arg-type]
            loaded=[],
            edges=[edge],
            plan=plan,
            profile=profile,
        )
        self.assertEqual(choices, [])
        self.assertFalse(any(item.kind == "calls_resolved" for item in updated.verified))

    def test_generation_mismatch_aborts_expand(self) -> None:
        plan = InvestigationPlan(intent="execution_trace", capability_generation_id="gen-a", strategies=["follow_verified_calls"])
        generation = SimpleNamespace(id="gen-b", repository_id="repo", parser_version="p")
        choices, updated = expand_execution_trace_evidence(
            db=None,  # type: ignore[arg-type]
            generation=generation,  # type: ignore[arg-type]
            loaded=[],
            edges=[],
            plan=plan,
        )
        self.assertEqual(choices, [])
        self.assertEqual(updated.stop_reason, "capability_generation_mismatch")


class EvidenceIntegrityWithCapabilityTests(unittest.TestCase):
    def test_required_evidence_survives_pack_under_capability_plan(self) -> None:
        gen = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        profile = _profile(_draft("execution_flow", gen), gen)
        plan = build_investigation_plan(
            "Trace generate plan request flow through API and database",
            profile=profile,
        )
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
                "text": "overview text",
                "context_prefix": "",
                "start_line": 1,
                "end_line": 2,
                "source_start_byte": 0,
                "source_end_byte": 20,
                "raw_bytes": 20,
            },
        ]
        ordered = order_spans_by_execution_stage(spans, plan)
        packed = pack_evidence(
            "header",
            ordered,
            [],
            [],
            context_tokens=8000,
            output_tokens=500,
            margin_tokens=100,
            count_tokens=lambda text: max(1, len(text) // 4),
            span_text=lambda span: (span["raw_bytes"], f"\n{span['text']}"),
            edge_text=lambda edge: "",
            technology_text=lambda tech: "",
            required_symbol_ids={"s1"},
        )
        self.assertIn("c1", {span["chunk_id"] for span in packed.spans})
        claims = partial_trace_claims(plan, packed.spans)
        # No verified relationships yet — empty claims OK; packing still kept required span.
        self.assertIsInstance(claims, list)

    def test_persist_payload_round_trip_generation_id(self) -> None:
        gen = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        draft = _draft("cross_stack_shipment", gen)
        payload = json.dumps(draft.capability_report)
        restored = profile_from_report(json.loads(payload), expected_generation_id=gen)
        self.assertEqual(restored.generation_id, gen)
        self.assertEqual(restored.source, "report")


class PlanningLatencySmokeTests(unittest.TestCase):
    def test_capability_aware_planning_is_cheap(self) -> None:
        gen = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        profile = _profile(_draft("execution_flow", gen), gen)
        started = time.perf_counter()
        for _ in range(50):
            build_investigation_plan(
                "Trace the request flow when a user generates a study plan from frontend to database.",
                profile=profile,
            )
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0)


class BeforeAfterPlanShapeTests(unittest.TestCase):
    def test_question_only_vs_capability_aware_fastapi(self) -> None:
        question = "Trace the request flow when a customer submits an order through the API to the database."
        naive = build_investigation_plan(question)
        gen = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        aware = build_investigation_plan(question, profile=_profile(_draft("fastapi_orders", gen), gen))
        # Capability awareness removes frontend stages that naive question cues might still omit,
        # and adds concrete strategies.
        self.assertTrue(aware.strategies)
        self.assertIn("frontend_update", aware.not_applicable_stages)
        self.assertEqual(naive.capability_generation_id, "")
        self.assertEqual(aware.capability_generation_id, gen)


if __name__ == "__main__":
    unittest.main()
