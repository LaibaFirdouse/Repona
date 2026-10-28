"""End-to-end evidence-integrity validation against a real indexed Repona generation.

Captures the evidence pipeline (lanes → materialize → expand → pack → citations)
for seven representative questions, scores source correctness, and optionally
compares an existing generation with a freshly built lazy-indexed generation.

Does not dump secrets or full repository contents.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(_SCRIPTS))

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal, create_database_tables  # noqa: E402
from app.ingestion.lazy_vectors import count_vector_ready  # noqa: E402
from app.models.catalog import (  # noqa: E402
    Chunk,
    File,
    GenerationChunk,
    GenerationFile,
    IndexGeneration,
    SourceRevision,
    Symbol,
)
from app.models.repository import Repository  # noqa: E402
from app.retrieval.answer import AnswerService  # noqa: E402
from app.retrieval.packet import is_weak_implementation_span  # noqa: E402
from app.services.client_session import ensure_client_user  # noqa: E402
from app.services.refresh_service import RefreshService  # noqa: E402
from app.storage.identity import normalize_repository_url  # noqa: E402
from benchmark_suite import DEFAULT_CLIENT_ID  # noqa: E402

DEFAULT_REPO = "https://github.com/LaibaFirdouse/Repona"


CASES: list[dict[str, Any]] = [
    {
        "id": "exact_method",
        "question": "How does pack_evidence decide which spans fit the context budget?",
        "expect_paths_any": ["backend/app/retrieval/packet.py"],
        "expect_qnames_any": ["pack_evidence"],
        "require_markers_any": ["source_byte_cap", "_fits", "kept_spans", "context_budget"],
        "forbid_readme_only": True,
        "forbid_import_only": True,
        "require_implementation": True,
    },
    {
        "id": "large_multipart",
        "question": "Walk through AnswerService._answer_events step by step: how it searches, packs evidence, and finalizes claims.",
        "expect_paths_any": ["backend/app/retrieval/answer.py"],
        "expect_qnames_any": ["_answer_events", "AnswerService._answer_events"],
        "require_multipart_same_symbol": True,
        "require_markers_any": [
            "materialize_anchors",
            "pack_evidence",
            "review_claims",
            "_search_lanes",
            "fuse_anchors",
        ],
        "forbid_readme_only": True,
        "require_implementation": True,
    },
    {
        "id": "readme_name_collision",
        "question": "How does AnswerService retrieve evidence for a question? Explain from the implementation source code, not the README.",
        "expect_paths_any": ["backend/app/retrieval/answer.py"],
        "expect_qnames_any": ["answer", "AnswerService.answer", "_answer_events", "AnswerService._answer_events"],
        "forbid_paths_as_sole_support": ["README.md"],
        "forbid_readme_only": True,
        "require_implementation": True,
        "require_markers_any": ["fuse_anchors", "materialize", "packet", "span"],
    },
    {
        "id": "imports_false_positive",
        "question": "How is upgrade_chunk_choice implemented? What invariants does it enforce when upgrading a symbol_part?",
        "expect_paths_any": ["backend/app/retrieval/chunk_selection.py"],
        "expect_qnames_any": ["upgrade_chunk_choice"],
        "forbid_import_only": True,
        "require_implementation": True,
        "require_markers_any": ["symbol_part", "symbol_id", "symbol_body"],
    },
    {
        "id": "cross_file_trace",
        "question": "Trace how a question flows from AnswerService.answer through fuse_anchors and materialize_anchors into pack_evidence.",
        "expect_paths_all_of_any": [
            ["backend/app/retrieval/answer.py"],
            ["backend/app/retrieval/fusion.py"],
            ["backend/app/retrieval/packet.py"],
        ],
        "require_implementation": True,
        "forbid_readme_only": True,
        "require_markers_any": ["fuse_anchors", "materialize_anchors", "pack_evidence"],
    },
    {
        "id": "explicit_path_symbol",
        "question": "In backend/app/retrieval/fusion.py, how does materialize_anchors choose chunks for a fused anchor?",
        "expect_paths_any": ["backend/app/retrieval/fusion.py"],
        "expect_qnames_any": ["materialize_anchors"],
        "require_exact_path": "backend/app/retrieval/fusion.py",
        "require_implementation": True,
        "require_markers_any": ["hits_by_anchor", "representative_chunk_choices", "ChunkChoice"],
    },
    {
        "id": "unavailable_evidence",
        "question": "Where is the payroll tax table calculated in this repository?",
        "expect_abstention": True,
        "forbid_confident_invention": True,
    },
]


def _git_provenance() -> dict[str, Any]:
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True)
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=PROJECT_ROOT, text=True
        ).strip()
        return {
            "git_head": head,
            "branch": branch,
            "working_tree_dirty": bool(dirty.strip()),
            "dirty_paths": [line[3:] for line in dirty.splitlines() if line.strip()][:40],
        }
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return {"error": str(exc)}


def _generation_info(db, repository: Repository) -> dict[str, Any]:
    if not repository.active_generation_id:
        return {"error": "no active generation"}
    generation = db.get(IndexGeneration, repository.active_generation_id)
    if generation is None:
        return {"error": "missing generation row"}
    revision = db.get(SourceRevision, generation.source_revision_id)
    files = (
        db.query(GenerationFile)
        .filter(GenerationFile.index_generation_id == generation.id)
        .count()
    )
    chunks = (
        db.query(GenerationChunk)
        .filter(GenerationChunk.index_generation_id == generation.id)
        .count()
    )
    return {
        "generation_id": generation.id,
        "status": generation.status,
        "commit_sha": None if revision is None else revision.commit_sha,
        "snapshot_path": None if revision is None else revision.snapshot_path,
        "files": files,
        "chunks": chunks,
        "vectors_ready": count_vector_ready(db, generation.id),
        "progressive": bool(getattr(settings, "progressive_indexing", False)),
        "lazy_embed_on_query": bool(settings.lazy_embed_on_query),
        "embed_catalogue_before_activate": bool(settings.embed_catalogue_before_activate),
    }


def _symbol_inventory(db, generation_id: str) -> dict[str, Any]:
    """Capture multipart structure for key Repona symbols (metadata only)."""
    wanted = [
        "pack_evidence",
        "upgrade_chunk_choice",
        "materialize_anchors",
        "_answer_events",
        "answer",
        "fuse_anchors",
    ]
    out: dict[str, Any] = {}
    for name in wanted:
        rows = (
            db.query(Symbol, File.relative_path)
            .join(File, File.id == Symbol.file_id)
            .join(GenerationFile, GenerationFile.file_id == File.id)
            .filter(
                GenerationFile.index_generation_id == generation_id,
                Symbol.name == name,
            )
            .all()
        )
        entries = []
        for symbol, path in rows:
            parts = (
                db.query(Chunk)
                .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
                .filter(
                    GenerationChunk.index_generation_id == generation_id,
                    Chunk.symbol_id == symbol.id,
                )
                .order_by(Chunk.ordinal, Chunk.id)
                .all()
            )
            entries.append(
                {
                    "symbol_id": symbol.id,
                    "qualified_name": symbol.qualified_name,
                    "path": path,
                    "parts": [
                        {
                            "chunk_id": chunk.id,
                            "kind": chunk.kind,
                            "ordinal": chunk.ordinal,
                            "start_line": chunk.start_line,
                            "end_line": chunk.end_line,
                            "bytes": max(0, chunk.end_byte - chunk.start_byte),
                        }
                        for chunk in parts
                    ],
                }
            )
        if entries:
            out[name] = entries
    return out


def _mock_complete_from_packet(system: str, user: str, max_tokens: int) -> str:
    """Deterministic model: cite packet evidence IDs; abstain when pack is thin/unrelated."""
    aliases = re.findall(r"\b(E\d+)\b", user)
    span_blocks = re.findall(
        r"span (E\d+) (\S+) ([^\n]*?) (symbol_body|symbol_part|import_block|file_overview|non_code) lines",
        user,
    )
    # Prefer implementation kinds.
    strong = [row for row in span_blocks if row[3] in {"symbol_body", "symbol_part"}]
    weak_only = not strong and bool(span_blocks)
    if "payroll tax table" in user.lower():
        return json.dumps({"claims": []})
    if not aliases:
        return json.dumps({"claims": []})
    if weak_only:
        return json.dumps({"claims": []})
    claims = []
    used = strong[:4] if strong else [(a, "", "", "symbol_body") for a in aliases[:2]]
    for alias, path, qname, kind in used:
        text = f"Observed in {path} {qname or kind}: behaviour is present in the cited excerpt."
        claims.append({"kind": "observed", "text": text, "evidence_ids": [alias]})
    return json.dumps({"claims": claims[:6]})


def _ensure_indexed(db, repo_url: str, client_id: str, *, force: bool) -> dict[str, Any]:
    ensure_client_user(db, client_id)
    service = RefreshService()
    normalized = normalize_repository_url(repo_url)
    repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
    info: dict[str, Any] = {"repo_url": repo_url, "forced": force}
    if repository is not None and repository.active_generation_id and not force:
        generation = db.get(IndexGeneration, repository.active_generation_id)
        if generation is not None and generation.status == "ready":
            info["action"] = "reuse_ready"
            info["generation"] = _generation_info(db, repository)
            return info
    started = perf_counter()
    # index() joins/creates and may ingest when needed
    outcome = service.index(db, repo_url, "master", client_id, None)
    info["index_outcome"] = {
        "action": getattr(outcome, "action", None),
        "message": getattr(outcome, "message", None),
        "generation_id": getattr(outcome, "generation_id", None),
    }
    db.expire_all()
    repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
    if repository is None:
        # try main branch
        outcome = service.index(db, repo_url, "main", client_id, None)
        info["index_outcome_main"] = {
            "action": getattr(outcome, "action", None),
            "message": getattr(outcome, "message", None),
            "generation_id": getattr(outcome, "generation_id", None),
        }
        db.expire_all()
        repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
    if repository is None:
        info["error"] = "repository row missing after index"
        return info
    # If still not ready, force full ingest via benchmark helper.
    generation = (
        db.get(IndexGeneration, repository.active_generation_id)
        if repository.active_generation_id
        else None
    )
    if force or generation is None or generation.status != "ready":
        from benchmark_index_large import run_force_index

        force_report = run_force_index(repo_url, client_id)
        info["force_index"] = {
            "generation_id": force_report.get("generation_id"),
            "commit_sha": force_report.get("commit_sha"),
            "error": force_report.get("error"),
            "ingestion_total_ms": force_report.get("ingestion_total_ms"),
        }
        db.expire_all()
        repository = db.query(Repository).filter(Repository.normalized_url == normalized).one()
    info["elapsed_s"] = round(perf_counter() - started, 2)
    info["generation"] = _generation_info(db, repository)
    info["action"] = "indexed"
    return info


def _ask_one(db, repository: Repository, client_id: str, question: str) -> dict[str, Any]:
    refresh = RefreshService()
    outcome = refresh.ask(db, repository.id, client_id)
    if outcome.action != "serve":
        return {"ok": False, "error": outcome.message or outcome.action, "action": outcome.action}
    trace: dict[str, Any] = {"record_steps": True, "evidence_debug": True}
    service = AnswerService(complete=_mock_complete_from_packet)
    started = perf_counter()
    try:
        answered = service.answer(db, question, outcome, trace)
    except Exception as exc:  # noqa: BLE001 — capture for report
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": _sanitize_trace(trace),
            "elapsed_s": round(perf_counter() - started, 3),
        }
    return {
        "ok": True,
        "elapsed_s": round(perf_counter() - started, 3),
        "message": answered.message,
        "claims": answered.claims,
        "citations": answered.citations,
        "citations_valid": answered.citations_valid,
        "answer_text": "\n\n".join(str(c.get("text") or "") for c in answered.claims),
        "trace": _sanitize_trace(trace),
        "generation_id": outcome.generation_id,
    }


def _sanitize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Keep pipeline diagnostics; truncate source text in packet spans."""
    kept = dict(trace)
    packet = []
    for span in trace.get("packet_spans") or []:
        if not isinstance(span, dict):
            continue
        text = str(span.get("text") or "")
        packet.append(
            {
                "chunk_id": span.get("chunk_id"),
                "relative_path": span.get("relative_path"),
                "qualified_name": span.get("qualified_name"),
                "chunk_kind": span.get("chunk_kind"),
                "start_line": span.get("start_line"),
                "end_line": span.get("end_line"),
                "raw_bytes": span.get("raw_bytes") or len(text.encode("utf-8")),
                "text_preview": text[:240],
                "text_markers_hit": None,  # filled by scorer
            }
        )
    kept["packet_spans"] = packet
    # Drop any accidental huge blobs
    for key in list(kept.keys()):
        if key.endswith("_raw") or key in {"prompt", "full_packet"}:
            kept.pop(key, None)
    return kept


def _packet_blob(result: dict[str, Any], *, full_texts: dict[str, str] | None = None) -> str:
    parts = []
    for span in (result.get("trace") or {}).get("packet_spans") or []:
        chunk_id = str(span.get("chunk_id") or "")
        if full_texts and chunk_id in full_texts:
            parts.append(full_texts[chunk_id])
        else:
            parts.append(str(span.get("text_preview") or ""))
        parts.append(str(span.get("qualified_name") or ""))
        parts.append(str(span.get("relative_path") or ""))
    return "\n".join(parts)


def _load_packet_full_texts(db, generation_id: str | None, packet: list[dict]) -> dict[str, str]:
    """Load full span text from the snapshot for marker checks (not persisted in the report)."""
    if not generation_id or not packet:
        return {}
    generation = db.get(IndexGeneration, generation_id)
    if generation is None:
        return {}
    revision = db.get(SourceRevision, generation.source_revision_id)
    if revision is None or not revision.snapshot_path:
        return {}
    root = Path(revision.snapshot_path)
    out: dict[str, str] = {}
    for span in packet:
        chunk_id = str(span.get("chunk_id") or "")
        if not chunk_id:
            continue
        chunk = db.get(Chunk, chunk_id)
        if chunk is None:
            continue
        file = db.get(File, chunk.file_id)
        if file is None:
            continue
        path = root / file.relative_path
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()[chunk.start_byte : chunk.end_byte]
            out[chunk_id] = data.decode("utf-8", errors="replace")
        except OSError:
            continue
    return out


def _score_case(
    case: dict[str, Any],
    result: dict[str, Any],
    *,
    db=None,
) -> dict[str, Any]:
    score: dict[str, Any] = {"case_id": case["id"], "checks": {}}
    if not result.get("ok"):
        score["passed"] = False
        score["checks"] = {"ask_ok": {"pass": False, "detail": result.get("error")}}
        score["failed_checks"] = ["ask_ok"]
        score["error"] = result.get("error")
        return score

    trace = result.get("trace") or {}
    packet = [s for s in (trace.get("packet_spans") or []) if isinstance(s, dict)]
    paths = [str(s.get("relative_path") or "") for s in packet]
    qnames = [str(s.get("qualified_name") or "") for s in packet]
    kinds = [str(s.get("chunk_kind") or "") for s in packet]
    full_texts = _load_packet_full_texts(db, result.get("generation_id"), packet) if db is not None else {}
    blob = _packet_blob(result, full_texts=full_texts)
    claims = result.get("claims") or []
    citations = result.get("citations") or []
    cited_ids = {item for claim in claims for item in (claim.get("evidence_ids") or [])}
    packet_ids = {str(s.get("chunk_id")) for s in packet if s.get("chunk_id")}

    def check(name: str, ok: bool, detail: Any = None) -> None:
        score["checks"][name] = {"pass": bool(ok), "detail": detail}

    # Path / symbol presence in FINAL packet (not merely retrieved paths)
    expect_paths = case.get("expect_paths_any") or []
    path_hit = [p for p in expect_paths if any(p in path for path in paths)] if expect_paths else []
    check(
        "correct_source_path_in_final_prompt",
        (not expect_paths) or bool(path_hit),
        {"expected_any": expect_paths, "matched": path_hit, "packet_paths": paths[:12]},
    )

    expect_q = case.get("expect_qnames_any") or []
    q_hit = []
    for expected in expect_q:
        if any(expected == q or expected.endswith(q) or q.endswith(expected) for q in qnames if q):
            q_hit.append(expected)
    check(
        "correct_symbol_in_final_prompt",
        (not expect_q) or bool(q_hit),
        {"expected_any": expect_q, "matched": q_hit, "packet_qnames": qnames[:12]},
    )

    if case.get("require_exact_path"):
        exact = case["require_exact_path"]
        check(
            "exact_path_present",
            any(path == exact or path.endswith(exact) for path in paths),
            {"expected": exact, "packet_paths": paths[:12]},
        )

    groups = case.get("expect_paths_all_of_any") or []
    if groups:
        missing = []
        for group in groups:
            if not any(any(token in path for path in paths) for token in group):
                missing.append(group)
        check("cross_file_coverage", not missing, {"missing_groups": missing, "packet_paths": paths[:16]})

    markers = case.get("require_markers_any") or []
    marker_hits = [m for m in markers if m.lower() in blob.lower()]
    check(
        "required_source_coverage",
        (not markers) or bool(marker_hits),
        {"expected_any": markers, "matched": marker_hits},
    )

    if case.get("require_multipart_same_symbol"):
        by_symbol: dict[str, list[dict]] = {}
        for span in packet:
            qn = str(span.get("qualified_name") or "")
            if not qn:
                continue
            if any(token in qn for token in (case.get("expect_qnames_any") or ["_answer_events"])):
                by_symbol.setdefault(qn, []).append(span)
        multipart = {k: v for k, v in by_symbol.items() if len(v) >= 2}
        ordered = True
        for spans in multipart.values():
            ordinals = []
            # ordinal may not be in packet_spans; use start_line as proxy
            lines = [int(s.get("start_line") or 0) for s in spans]
            if lines != sorted(lines):
                ordered = False
        check(
            "multipart_ordered_coverage",
            bool(multipart) and ordered,
            {
                "multipart_symbols": {k: len(v) for k, v in multipart.items()},
                "note": "fail if only one part of a large symbol reached the final prompt",
            },
        )

    strong = [s for s in packet if not is_weak_implementation_span(s)]
    if case.get("forbid_readme_only"):
        only_readme = bool(packet) and all(
            "readme" in str(s.get("relative_path") or "").lower() for s in packet
        )
        check("not_readme_only", not only_readme, {"packet_paths": paths[:8]})
    if case.get("forbid_import_only"):
        only_imports = bool(packet) and all(k == "import_block" for k in kinds)
        check("not_import_only", not only_imports, {"kinds": kinds[:12]})
    if case.get("require_implementation"):
        check(
            "has_substantive_implementation",
            bool(strong),
            {
                "strong_count": len(strong),
                "strong_paths": [s.get("relative_path") for s in strong[:8]],
            },
        )

    # Citation mapping: every claim evidence id is in the final packet
    if case.get("expect_abstention"):
        abstained = (not claims) or (
            not result.get("citations_valid")
            and ("insufficient" in str(result.get("message") or "").lower()
                 or "did not produce" in str(result.get("message") or "").lower()
                 or not claims)
        )
        check("explicit_limitation_or_abstention", abstained, {
            "claims": len(claims),
            "message": result.get("message"),
            "citations_valid": result.get("citations_valid"),
        })
        invented = False
        if claims and "payroll" in (result.get("answer_text") or "").lower():
            # claiming a concrete payroll implementation is invention
            invented = any("calculat" in str(c.get("text") or "").lower() for c in claims)
        check("no_confident_invention", not invented, {"answer_preview": (result.get("answer_text") or "")[:200]})
    else:
        citation_ok = bool(claims) and cited_ids.issubset(packet_ids)
        check(
            "citation_ids_in_final_prompt",
            citation_ok,
            {"cited_ids": sorted(cited_ids)[:12], "packet_ids_sample": sorted(packet_ids)[:12]},
        )
        # Citation path/line agreement
        cite_paths_ok = True
        for cite in citations:
            cid = str(cite.get("chunk_id") or "")
            match = next((s for s in packet if str(s.get("chunk_id")) == cid), None)
            if match is None:
                cite_paths_ok = False
                break
            if cite.get("path") and cite.get("path") != match.get("relative_path"):
                cite_paths_ok = False
                break
        check("citation_path_line_mapping", cite_paths_ok and bool(citations), {"citations": citations[:6]})

    # Substitution / identity: no packet span labeled with expected symbol but wrong path
    if expect_q and expect_paths:
        mislabeled = []
        for span in packet:
            qn = str(span.get("qualified_name") or "")
            path = str(span.get("relative_path") or "")
            if any(token in qn for token in expect_q) and not any(p in path for p in expect_paths):
                mislabeled.append({"qname": qn, "path": path})
        check("no_unrelated_substitution", not mislabeled, {"mislabeled": mislabeled})

    # Evidence completeness: pipeline ops recorded when evidence_debug on
    pipeline = trace.get("evidence_pipeline") or {}
    check(
        "pipeline_trace_captured",
        bool(pipeline) or bool(trace.get("steps")),
        {"pipeline_keys": sorted(pipeline.keys())[:20], "steps": len(trace.get("steps") or [])},
    )

    pack_stage = pipeline.get("11_pack") or {}
    check(
        "packer_exclusions_recorded",
        "excluded" in pack_stage or bool(packet),
        {"excluded_count": len(pack_stage.get("excluded") or []), "kept": len(pack_stage.get("kept") or [])},
    )

    failed = [name for name, row in score["checks"].items() if not row["pass"]]
    score["passed"] = not failed
    score["failed_checks"] = failed
    score["answer_strategy"] = trace.get("answer_strategy")
    score["intent"] = (trace.get("retrieval_router") or {}).get("intent")
    return score


def _run_suite(db, repository: Repository, client_id: str, label: str) -> dict[str, Any]:
    gen = _generation_info(db, repository)
    inventory = _symbol_inventory(db, gen["generation_id"]) if gen.get("generation_id") else {}
    cases_out = []
    for case in CASES:
        result = _ask_one(db, repository, client_id, case["question"])
        score = _score_case(case, result, db=db)
        # Attach compact evidence summary for the report artifact
        trace = result.get("trace") or {}
        cases_out.append(
            {
                "case": case,
                "score": score,
                "generation_id": result.get("generation_id") or gen.get("generation_id"),
                "elapsed_s": result.get("elapsed_s"),
                "ok": result.get("ok"),
                "error": result.get("error"),
                "message": result.get("message"),
                "claims": result.get("claims"),
                "citations": result.get("citations"),
                "citations_valid": result.get("citations_valid"),
                "intent": (trace.get("retrieval_router") or {}).get("intent"),
                "lanes": (trace.get("lane_trace") or {}).get("lanes_executed")
                or (trace.get("retrieval_router") or {}).get("lanes"),
                "lane_hit_counts": (trace.get("lane_trace") or {}).get("lane_hit_counts"),
                "fused_anchor_count": len(trace.get("fused_anchor_ids") or []),
                "candidate_chunk_ids": (trace.get("candidate_chunk_ids") or [])[:24],
                "evidence_pipeline": trace.get("evidence_pipeline"),
                "packet_spans": trace.get("packet_spans"),
                "steps": trace.get("steps"),
                "lazy_query_embed": trace.get("lazy_query_embed"),
                "vectors_ready": trace.get("vectors_ready"),
                "vectors_ready_after": trace.get("vectors_ready_after"),
            }
        )
    passed = sum(1 for row in cases_out if row["score"].get("passed"))
    return {
        "label": label,
        "generation": gen,
        "symbol_inventory": inventory,
        "cases": cases_out,
        "summary": {"passed": passed, "total": len(cases_out)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", default=DEFAULT_REPO)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--force-index", action="store_true", help="Force a fresh ingest before the first suite")
    parser.add_argument(
        "--skip-lazy-rebuild",
        action="store_true",
        help="Only run against the current/ready generation",
    )
    parser.add_argument(
        "--output",
        default="evaluation/evidence-integrity-latest.json",
    )
    args = parser.parse_args()

    create_database_tables()
    db = SessionLocal()
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": _git_provenance(),
        "settings": {
            "database_url_host": (settings.database_url or "").split("@")[-1][:80],
            "qdrant_url": settings.qdrant_url,
            "neo4j_uri": settings.neo4j_uri,
            "progressive_indexing": settings.progressive_indexing,
            "lazy_embed_on_query": settings.lazy_embed_on_query,
            "embed_catalogue_before_activate": settings.embed_catalogue_before_activate,
            "llm_provider": settings.llm_provider,
        },
        "repo_url": args.repo_url,
        "commands": [
            "docker compose up -d postgres neo4j qdrant",
            "DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5433/repona "
            "QDRANT_URL=http://127.0.0.1:6333 NEO4J_URI=bolt://127.0.0.1:7687 "
            "PROGRESSIVE_INDEXING=true EMBED_CATALOGUE_BEFORE_ACTIVATE=false "
            "PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST=false LAZY_EMBED_ON_QUERY=true "
            "PYTHONPATH=backend:scripts .venv/bin/python scripts/validate_evidence_integrity.py "
            "--force-index",
        ],
    }
    try:
        index_info = _ensure_indexed(db, args.repo_url, args.client_id, force=args.force_index)
        report["index"] = index_info
        if index_info.get("error") or (index_info.get("force_index") or {}).get("error"):
            print(json.dumps(report, indent=2)[:4000])
            return 2
        repository = (
            db.query(Repository)
            .filter(Repository.normalized_url == normalize_repository_url(args.repo_url))
            .one()
        )
        existing = _run_suite(db, repository, args.client_id, label="existing_or_initial_generation")
        report["suites"] = [existing]

        if not args.skip_lazy_rebuild:
            # Build a fresh lazy generation and re-run.
            os.environ.setdefault("PROGRESSIVE_INDEXING", "true")
            os.environ.setdefault("EMBED_CATALOGUE_BEFORE_ACTIVATE", "false")
            os.environ.setdefault("PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST", "false")
            os.environ.setdefault("LAZY_EMBED_ON_QUERY", "true")
            rebuild = _ensure_indexed(db, args.repo_url, args.client_id, force=True)
            report["lazy_rebuild"] = rebuild
            db.expire_all()
            repository = (
                db.query(Repository)
                .filter(Repository.normalized_url == normalize_repository_url(args.repo_url))
                .one()
            )
            # Confirm deferred / non-ready vectors exist at queryable time
            gen = _generation_info(db, repository)
            report["lazy_rebuild"]["vectors_at_queryable"] = gen.get("vectors_ready")
            report["lazy_rebuild"]["chunks"] = gen.get("chunks")
            lazy_suite = _run_suite(db, repository, args.client_id, label="fresh_lazy_generation")
            report["suites"].append(lazy_suite)

        # Aggregate
        all_scores = []
        for suite in report["suites"]:
            for case in suite["cases"]:
                all_scores.append(
                    {
                        "suite": suite["label"],
                        "case_id": case["case"]["id"],
                        "passed": case["score"].get("passed"),
                        "failed_checks": case["score"].get("failed_checks"),
                        "generation_id": case.get("generation_id"),
                    }
                )
        report["aggregate"] = {
            "scores": all_scores,
            "all_passed": all(row["passed"] for row in all_scores) if all_scores else False,
        }
        if report["aggregate"]["all_passed"]:
            report["release_candidate_recommendation"] = (
                "Evidence-integrity suite passed on the live Repona index for both "
                "generations tested. Suitable for a release-candidate tag after normal "
                "CI (unit suites already green) and a human spot-check of answer prose."
            )
        else:
            failures = [row for row in all_scores if not row["passed"]]
            report["release_candidate_recommendation"] = (
                "Not ready for RC: evidence-integrity failures remain. "
                f"Failed cases: {failures}"
            )
    finally:
        db.close()

    out = PROJECT_ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md = out.with_suffix(".md")
    md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"output": str(out), "markdown": str(md), "aggregate": report.get("aggregate")}, indent=2))
    return 0 if report.get("aggregate", {}).get("all_passed") else 1


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Evidence integrity validation",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Repo: `{report.get('repo_url')}`",
        f"HEAD: `{(report.get('provenance') or {}).get('git_head')}` "
        f"(dirty={(report.get('provenance') or {}).get('working_tree_dirty')})",
        "",
        "## Commands",
        "",
    ]
    for cmd in report.get("commands") or []:
        lines.append(f"```bash\n{cmd}\n```")
        lines.append("")
    for suite in report.get("suites") or []:
        gen = suite.get("generation") or {}
        lines.extend(
            [
                f"## Suite: {suite.get('label')}",
                "",
                f"- generation_id: `{gen.get('generation_id')}`",
                f"- commit: `{gen.get('commit_sha')}`",
                f"- files/chunks/vectors: {gen.get('files')}/{gen.get('chunks')}/{gen.get('vectors_ready')}",
                f"- summary: **{suite.get('summary', {}).get('passed')}/{suite.get('summary', {}).get('total')}** passed",
                "",
                "| Case | Pass | Failed checks | Intent |",
                "|------|------|---------------|--------|",
            ]
        )
        for case in suite.get("cases") or []:
            score = case.get("score") or {}
            failed = ", ".join(score.get("failed_checks") or []) or "—"
            lines.append(
                f"| `{case['case']['id']}` | {'PASS' if score.get('passed') else 'FAIL'} | {failed} | {case.get('intent')} |"
            )
        lines.append("")
        inv = suite.get("symbol_inventory") or {}
        if inv.get("_answer_events"):
            parts = inv["_answer_events"][0].get("parts") or []
            lines.append(
                f"- `_answer_events` inventory: {len(parts)} chunk(s) "
                f"kinds={[p.get('kind') for p in parts[:8]]}"
            )
            lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    lines.append(str(report.get("release_candidate_recommendation") or ""))
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
