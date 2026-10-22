"""Shared subprocess helper: run one AnswerService ask with trace capture."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def run_ask_probe(
    *,
    repo_url: str,
    client_id: str,
    question: str,
    env_overrides: dict[str, str] | None = None,
    setup_code: str = "",
    embed_fail: bool = False,
) -> dict:
    code = f"""
import json, sys
from pathlib import Path
from time import perf_counter

root = Path({str(PROJECT_ROOT)!r})
sys.path.insert(0, str(root / "backend"))
sys.path.insert(0, str(root / "scripts"))

from app.db.session import SessionLocal, create_database_tables
from app.ingestion.lazy_vectors import count_vector_ready
from app.models.catalog import IndexGeneration
from app.services.refresh_service import RefreshService
from app.retrieval.answer import AnswerService
from benchmark_suite import ensure_benchmark_repository_access

{setup_code}

create_database_tables()
repo_url = {repo_url!r}
client_id = {client_id!r}
question = {question!r}
embed_fail = {embed_fail!r}

def mock_complete(_s, _u, _m):
    return '{{"claims":[{{"kind":"observed","text":"stub","evidence_ids":["E1"]}}]}}'

db = SessionLocal()
repository = ensure_benchmark_repository_access(db, repo_url, client_id)
generation = db.get(IndexGeneration, repository.active_generation_id)
vectors_before = count_vector_ready(db, generation.id)

refresh = RefreshService()
outcome = refresh.ask(db, repository.id, client_id)
if outcome.action != "serve":
    print(json.dumps({{"ok": False, "error": outcome.message or outcome.action}}))
    sys.exit(2)

service = AnswerService(complete=mock_complete)
if embed_fail:
    def fail_embed(_text):
        raise RuntimeError("embedding provider unavailable")
    service.embed = fail_embed

trace = {{"record_steps": True}}
started = perf_counter()
error = None
try:
    service.answer(db, question, outcome, trace)
except Exception as exc:
    error = f"{{type(exc).__name__}}: {{exc}}"

elapsed_ms = round((perf_counter() - started) * 1000, 2)
vectors_after = count_vector_ready(db, generation.id)
router = trace.get("retrieval_router") or {{}}
lane_trace = trace.get("lane_trace") or {{}}
lazy = trace.get("lazy_query_embed") or {{}}
retrieved_paths = [
    span.get("relative_path") or ""
    for span in (trace.get("spans_before_budget") or [])
]
packet_spans = trace.get("packet_spans") or []
for span in packet_spans:
    path = span.get("relative_path") or ""
    if path and path not in retrieved_paths:
        retrieved_paths.append(path)
lane_exec = (trace.get("lane_trace") or {{}}).get("lanes_executed") or {{}}
min_substantive_bytes = 350
substantive_span_count = 0
span_byte_samples = []
for span in packet_spans:
    if not isinstance(span, dict):
        continue
    kind = str(span.get("chunk_kind") or "")
    text = str(span.get("text") or "")
    raw = int(span.get("raw_bytes") or 0) or len(text.encode("utf-8"))
    span_byte_samples.append({{"kind": kind, "raw_bytes": raw, "path": span.get("relative_path") or ""}})
    if kind == "symbol_body" and raw >= min_substantive_bytes:
        substantive_span_count += 1

result = {{
    "ok": error is None,
    "error": error,
    "elapsed_ms": elapsed_ms,
    "vectors_before": vectors_before,
    "vectors_after": vectors_after,
    "vectors_added": vectors_after - vectors_before,
    "vectors_ready": trace.get("vectors_ready"),
    "lanes_requested": lane_trace.get("lanes_requested") or router.get("lanes"),
    "lanes_executed": lane_exec,
    "lane_hit_counts": lane_trace.get("lane_hit_counts"),
    "evidence_fallback": trace.get("evidence_fallback"),
    "packet_spans": packet_spans,
    "substantive_span_count": substantive_span_count,
    "span_byte_samples": span_byte_samples[:16],
    "trace_anchor_chunks": trace.get("trace_anchor_chunks"),
    "trace_anchor_graph_hops": trace.get("trace_anchor_graph_hops"),
    "trace_anchor_out_degree_added": trace.get("trace_anchor_out_degree_added"),
    "vector_lane_expanded": router.get("vector_lane_expanded"),
    "lazy_query_embed": lazy,
    "retrieved_paths": retrieved_paths[:24],
    "fused_anchor_count": len(trace.get("fused_anchor_ids") or []),
    "packet_span_count": len(trace.get("packet_spans") or []),
    "ask_trace_steps": trace.get("steps"),
}}
print(json.dumps(result))
db.close()
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "scripts")]
    )
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run([str(VENV_PYTHON), "-c", code], env=env, cwd=PROJECT_ROOT, capture_output=True, text=True)
    if proc.returncode != 0 and not proc.stdout.strip():
        return {"ok": False, "harness_error": proc.stderr, "subprocess_returncode": proc.returncode}
    try:
        row = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {
            "ok": False,
            "harness_error": (proc.stdout + proc.stderr)[-4000:],
            "subprocess_returncode": proc.returncode,
        }
    row["subprocess_returncode"] = proc.returncode
    return row


def evidence_paths(probe: dict) -> list[str]:
    paths = list(probe.get("retrieved_paths") or [])
    for span in probe.get("packet_spans") or []:
        if isinstance(span, dict):
            path = span.get("relative_path") or ""
            if path and path not in paths:
                paths.append(path)
    return paths


def paths_recall(retrieved_paths: list[str], expect_paths: list[str]) -> dict:
    if not expect_paths:
        return {"hit": True, "recall_at_k": 1.0, "matched": []}
    matched = [token for token in expect_paths if any(token in path for path in retrieved_paths)]
    return {
        "hit": bool(matched),
        "recall_at_k": len(matched) / len(expect_paths),
        "matched": matched,
    }


def paths_full_recall(retrieved_paths: list[str], expect_paths: list[str]) -> dict:
    if not expect_paths:
        return {"full_recall": True, "recall_at_k": 1.0, "matched": []}
    matched = [token for token in expect_paths if any(token in path for path in retrieved_paths)]
    full = len(matched) == len(expect_paths)
    return {
        "full_recall": full,
        "recall_at_k": len(matched) / len(expect_paths),
        "matched": matched,
    }


def _lazy_embed_evidence(probe: dict) -> bool:
    lazy = probe.get("lazy_query_embed") or {}
    return (
        (probe.get("vectors_added") or 0) > 0
        or (lazy.get("lazy_points_upserted") or 0) > 0
        or ((lazy.get("embed_model_ms") or 0) > 0 and (lazy.get("embed_cache_misses") or 0) > 0)
    )


def score_lazy_case(case: dict, probe: dict, *, is_repeat: bool = False) -> dict:
    paths = evidence_paths(probe)
    any_paths = case.get("expect_paths_any") or []
    all_paths = case.get("expect_paths_all") or []
    path_any = paths_recall(paths, any_paths) if any_paths else {"hit": True, "recall_at_k": 1.0, "matched": []}
    path_all = paths_full_recall(paths, all_paths)
    path_ok = (not any_paths or path_any["hit"]) and path_all["full_recall"]

    lanes = probe.get("lanes_requested") or []
    expect_lanes = case.get("expect_lanes_any") or []
    lane_ok = not expect_lanes or any(lane in lanes for lane in expect_lanes)

    executed = probe.get("lanes_executed") or {}
    expect_executed = case.get("expect_lanes_executed") or []
    lane_exec_ok = not expect_executed or all(executed.get(lane) for lane in expect_executed)

    lazy = probe.get("lazy_query_embed") or {}
    embed_error = lazy.get("embed_error")
    provider_outage = bool(lazy.get("embed_provider_unavailable"))
    internal_embed_error = bool(embed_error) and not provider_outage

    if probe.get("embed_fail_mode"):
        vector_ran = bool((executed or {}).get("vector"))
        embed_ok = probe.get("ok") and vector_ran and provider_outage and not internal_embed_error and path_ok
    elif case.get("require_no_embed_error", True):
        embed_ok = not embed_error
    else:
        embed_ok = True

    lazy_embed_ok = True
    if case.get("require_lazy_embed") and not is_repeat:
        lazy_embed_ok = _lazy_embed_evidence(probe)
    if case.get("require_lazy_embed") and is_repeat:
        lazy_embed_ok = (probe.get("vectors_added") or 0) == 0

    materialisation_ok = True
    if case.get("expect_materialisation"):
        materialisation_ok = (probe.get("fused_anchor_count") or 0) > 0 or bool(probe.get("evidence_fallback"))

    passed = (
        probe.get("ok")
        and path_ok
        and lane_ok
        and lane_exec_ok
        and embed_ok
        and lazy_embed_ok
        and materialisation_ok
    )
    return {
        "path_any": path_any,
        "path_all": path_all,
        "lane_expectation_met": lane_ok,
        "lane_execution_met": lane_exec_ok,
        "materialisation_ok": materialisation_ok,
        "embed_internal_error": internal_embed_error,
        "embed_provider_outage": provider_outage,
        "embed_fallback_ok": embed_ok,
        "lazy_embed_evidence": lazy_embed_ok if case.get("require_lazy_embed") else _lazy_embed_evidence(probe),
        "passed": passed,
    }
