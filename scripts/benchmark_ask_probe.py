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

result = {{
    "ok": error is None,
    "error": error,
    "elapsed_ms": elapsed_ms,
    "vectors_before": vectors_before,
    "vectors_after": vectors_after,
    "vectors_added": vectors_after - vectors_before,
    "vectors_ready": trace.get("vectors_ready"),
    "lanes_requested": lane_trace.get("lanes_requested") or router.get("lanes"),
    "lane_hit_counts": lane_trace.get("lane_hit_counts"),
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


def paths_recall(retrieved_paths: list[str], expect_paths: list[str]) -> dict:
    if not expect_paths:
        return {"hit": True, "recall_at_k": 1.0, "matched": []}
    matched = [token for token in expect_paths if any(token in path for path in retrieved_paths)]
    return {
        "hit": bool(matched),
        "recall_at_k": len(matched) / len(expect_paths),
        "matched": matched,
    }
