"""Validate lazy retrieval: materialisation, on-demand embed, lanes, citations, repeat cache."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from benchmark_ask_probe import run_ask_probe, score_lazy_case  # noqa: E402
from benchmark_suite import DEFAULT_CLIENT_ID  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
CASES_PATH = PROJECT_ROOT / "evaluation" / "lazy-retrieval-cases.json"
DEFAULT_REPO = "https://github.com/LaibaFirdouse/Repona"

def _reset_lazy_vector_state(repo_url: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "scripts")])
    code = f"""
import json, sys
from pathlib import Path
root = Path({str(PROJECT_ROOT)!r})
sys.path.insert(0, str(root / "backend"))
from app.db.session import SessionLocal, create_database_tables
from app.ingestion.vector_store import delete_points_for_repository
from app.models.catalog import GenerationChunk, IndexGeneration
from app.models.repository import Repository
from app.services.refresh_service import EMBEDDING_MODEL, VECTOR_DIMENSIONS
from app.storage.identity import collection_name, normalize_repository_url
from app.core.config import settings

create_database_tables()
normalized = normalize_repository_url({repo_url!r})
db = SessionLocal()
repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
if repository is None or not repository.active_generation_id:
    print(json.dumps({{"ok": False, "error": "repository not indexed"}}))
else:
    generation = db.get(IndexGeneration, repository.active_generation_id)
    collection = collection_name(EMBEDDING_MODEL, VECTOR_DIMENSIONS)
    delete_points_for_repository(settings.qdrant_url, collection, repository.id)
    pending = (
        db.query(GenerationChunk)
        .filter(GenerationChunk.index_generation_id == generation.id)
        .update({{GenerationChunk.vector_status: "pending"}}, synchronize_session=False)
    )
    db.commit()
    print(json.dumps({{
        "ok": True,
        "generation_id": generation.id,
        "chunks_reset_pending": pending,
        "collection": collection,
    }}))
db.close()
"""
    proc = subprocess.run([str(VENV_PYTHON), "-c", code], env=env, cwd=PROJECT_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr or proc.stdout, "subprocess_returncode": proc.returncode}
    try:
        row = json.loads(proc.stdout.strip().splitlines()[-1])
        row["subprocess_returncode"] = proc.returncode
        return row
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": proc.stdout + proc.stderr, "subprocess_returncode": proc.returncode}


LAZY_COLD_ENV = {
    "PROGRESSIVE_INDEXING": "true",
    "EMBED_CATALOGUE_BEFORE_ACTIVATE": "false",
    "PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST": "false",
    "LAZY_EMBED_ON_QUERY": "true",
}


def _git_provenance() -> dict:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True).strip()
        return {"git_head": commit, "working_tree_dirty": bool(dirty)}
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"git_head": None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", default=DEFAULT_REPO)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--output", default="evaluation/benchmark-lazy-validation-latest.json")
    parser.add_argument("--skip-repeat", action="store_true")
    parser.add_argument("--no-cold-vectors", action="store_true", help="Do not clear Qdrant / reset vector_status before cases")
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    vector_reset = None if args.no_cold_vectors else _reset_lazy_vector_state(args.repo_url)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **_git_provenance(),
        "repo_url": args.repo_url,
        "env": LAZY_COLD_ENV,
        "vector_cold_reset": vector_reset,
        "cases": [],
    }
    if vector_reset is not None and not vector_reset.get("ok"):
        print(json.dumps({"vector_cold_reset_failed": vector_reset}, indent=2))
        return 2
    exit_code = 0
    for case in cases:
        if not args.no_cold_vectors and (
            case.get("require_lazy_embed") or case.get("category") == "lazy_embed_required"
        ):
            per_case_reset = _reset_lazy_vector_state(args.repo_url)
            if not per_case_reset.get("ok"):
                print(json.dumps({"vector_cold_reset_failed": per_case_reset, "case": case.get("id")}, indent=2))
                return 2
        first = run_ask_probe(
            repo_url=args.repo_url,
            client_id=args.client_id,
            question=case["question"],
            env_overrides=LAZY_COLD_ENV,
        )
        first_score = score_lazy_case(case, first, is_repeat=False)
        repeat = None
        repeat_score = None
        if not args.skip_repeat:
            repeat = run_ask_probe(
                repo_url=args.repo_url,
                client_id=args.client_id,
                question=case["question"],
                env_overrides=LAZY_COLD_ENV,
            )
            repeat_score = score_lazy_case(case, repeat, is_repeat=True)
        embed_unavail = None
        embed_score = None
        if case.get("run_embed_unavailable"):
            embed_unavail = run_ask_probe(
                repo_url=args.repo_url,
                client_id=args.client_id,
                question=case.get("embed_outage_question") or case["question"],
                env_overrides=LAZY_COLD_ENV,
                embed_fail=True,
            )
            embed_unavail["embed_fail_mode"] = True
            embed_score = score_lazy_case(case, embed_unavail, is_repeat=False)
        row = {
            "case": case,
            "first_query": {**first, "score": first_score},
            "repeat_query": None if repeat is None else {**repeat, "score": repeat_score},
            "embed_unavailable": None if embed_unavail is None else {**embed_unavail, "score": embed_score},
        }
        report["cases"].append(row)
        for score in (first_score, repeat_score, embed_score):
            if score and not score.get("passed"):
                exit_code = 1
        for part in (first, repeat, embed_unavail):
            if part and part.get("subprocess_returncode", 0) not in (0, None):
                exit_code = 2

    out = PROJECT_ROOT / args.output
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = [
        {
            "id": item["case"]["id"],
            "passed_first": item["first_query"]["score"]["passed"],
            "passed_repeat": None if item["repeat_query"] is None else item["repeat_query"]["score"]["passed"],
            "passed_embed_outage": None
            if item["embed_unavailable"] is None
            else item["embed_unavailable"]["score"]["passed"],
            "first_ms": item["first_query"].get("elapsed_ms"),
            "repeat_ms": None if item["repeat_query"] is None else item["repeat_query"].get("elapsed_ms"),
            "vectors_added_first": item["first_query"].get("vectors_added"),
            "vectors_added_repeat": None if item["repeat_query"] is None else item["repeat_query"].get("vectors_added"),
            "lazy_embed_first": item["first_query"]["score"].get("lazy_embed_evidence"),
            "embed_internal_error_first": item["first_query"]["score"].get("embed_internal_error"),
        }
        for item in report["cases"]
    ]
    print(json.dumps(summary, indent=2))
    failed_first = any(not item["first_query"]["score"]["passed"] for item in report["cases"])
    if exit_code:
        return exit_code
    if failed_first:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
