"""Three reproducible cold-start benchmark modes with isolated env per run."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_SCRIPTS))
from benchmark_suite import DEFAULT_CLIENT_ID  # noqa: E402

DEFAULT_REPO = "https://github.com/LaibaFirdouse/Repona"
DEFAULT_QUESTION = "How does AnswerService retrieve evidence for a question?"
LAZY_QUESTION = "Explain the implementation of AnswerService.answer step by step with citations."

MODE_ENV = {
    "full_cold": {
        "PROGRESSIVE_INDEXING": "false",
        "EMBED_CATALOGUE_BEFORE_ACTIVATE": "true",
        "PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST": "true",
    },
    "progressive_cold": {
        "PROGRESSIVE_INDEXING": "true",
        "EMBED_CATALOGUE_BEFORE_ACTIVATE": "true",
        "PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST": "true",
    },
    "lazy_cold": {
        "PROGRESSIVE_INDEXING": "true",
        "EMBED_CATALOGUE_BEFORE_ACTIVATE": "false",
        "PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST": "false",
    },
}


def _git_provenance() -> dict:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True).strip()
        return {"git_head": commit, "working_tree_dirty": bool(dirty), "working_tree_diff_lines": len(dirty.splitlines()) if dirty else 0}
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"git_head": None, "working_tree_dirty": None}


def _clear_qdrant(repo_url: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "backend")
    code = (
        "from app.db.session import SessionLocal;"
        "from app.ingestion.vector_store import delete_points_for_repository;"
        "from app.models.repository import Repository;"
        "from app.services.refresh_service import EMBEDDING_MODEL, VECTOR_DIMENSIONS;"
        "from app.storage.identity import collection_name, normalize_repository_url;"
        "from app.core.config import settings;"
        "db=SessionLocal();"
        "n=normalize_repository_url(%r);"
        "r=db.query(Repository).filter(Repository.normalized_url==n).one_or_none();"
        "import sys;"
        "if r is None: print('skip'); sys.exit(0);"
        "c=collection_name(EMBEDDING_MODEL, VECTOR_DIMENSIONS);"
        "delete_points_for_repository(settings.qdrant_url, c, r.id);"
        "print('cleared', r.id, c)"
    ) % repo_url
    proc = subprocess.run([str(VENV_PYTHON), "-c", code], env=env, cwd=PROJECT_ROOT, capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


def _run_index(mode: str, repo_url: str, client_id: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "backend")
    env.update(MODE_ENV[mode])
    json_out = PROJECT_ROOT / "evaluation" / f"benchmark-controlled-{mode}-index.json"
    proc = subprocess.run(
        [
            str(VENV_PYTHON),
            str(PROJECT_ROOT / "scripts" / "benchmark_index_large.py"),
            "--repo-url",
            repo_url,
            "--client-id",
            client_id,
            "--json",
            str(json_out),
            "--markdown",
            str(PROJECT_ROOT / "evaluation" / f"benchmark-controlled-{mode}-report.md"),
        ],
        env=env,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    payload = {}
    if json_out.is_file():
        payload = json.loads(json_out.read_text(encoding="utf-8"))
    payload["_subprocess"] = {"returncode": proc.returncode, "stderr_tail": proc.stderr[-2000:] if proc.stderr else ""}
    payload["_mode_env"] = MODE_ENV[mode]
    return payload


def _run_lazy_asks(repo_url: str, client_id: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "backend")
    env.update(MODE_ENV["lazy_cold"])
    code = f"""
import json, sys
from pathlib import Path
root = Path({str(PROJECT_ROOT)!r})
sys.path.insert(0, str(root / "backend"))
sys.path.insert(0, str(root / "scripts"))
from app.db.session import SessionLocal, create_database_tables
from benchmark_detailed import detailed_repository

create_database_tables()
db = SessionLocal()
try:
    first = detailed_repository(db, {repo_url!r}, {LAZY_QUESTION!r}, {client_id!r})
    second = detailed_repository(db, {repo_url!r}, {LAZY_QUESTION!r}, {client_id!r})
finally:
    db.close()
print(json.dumps({{"ask_first": first, "ask_repeat": second}}))
"""
    proc = subprocess.run([str(VENV_PYTHON), "-c", code], env=env, cwd=PROJECT_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr or proc.stdout, "subprocess_returncode": proc.returncode}
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": proc.stdout + proc.stderr, "subprocess_returncode": proc.returncode}
    payload["subprocess_returncode"] = proc.returncode
    first = payload.get("ask_first") or {}
    repeat = payload.get("ask_repeat") or {}
    payload["ok"] = first.get("ok", True) and repeat.get("ok", True)
    if not payload["ok"]:
        payload["error"] = first.get("error") or repeat.get("error")
    return payload


def _ingestion_summary(steps: list[dict]) -> dict:
    def _find(name: str):
        row = next((item for item in steps if item.get("step") == name), None)
        return None if row is None else row.get("ms")

    return {
        "ingest_commit_total_ms": _find("ingest.ingest_commit_total"),
        "time_to_queryable_ms": _find("ingest.time_to_queryable"),
        "write_draft_postgres_ms": _find("ingest.write_draft_postgres"),
        "write_graph_neo4j_ms": _find("ingest.write_graph_neo4j"),
        "qdrant_assert_points_ms": _find("ingest.qdrant_assert_points"),
        "catalog_embed_model_ms": _find("ingest.qdrant_catalog_embed_and_upsert"),
        "catalog_embed_skipped": _find("ingest.qdrant_catalog_embed_skipped") is not None,
    }


def run_mode(mode: str, repo_url: str, client_id: str) -> dict:
    report = {"mode": mode, "repo_url": repo_url, **_git_provenance(), "cache_action": _clear_qdrant(repo_url)}
    report["index"] = _run_index(mode, repo_url, client_id)
    steps = report["index"].get("ingestion_steps") or []
    report["ingestion_summary"] = _ingestion_summary(steps)
    queryable = (report.get("ingestion_summary") or {}).get("time_to_queryable_ms")
    if mode == "lazy_cold" and queryable is not None:
        report["lazy_asks"] = _run_lazy_asks(repo_url, client_id)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("full_cold", "progressive_cold", "lazy_cold", "all"),
        default="all",
    )
    parser.add_argument("--repo-url", default=DEFAULT_REPO)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    args = parser.parse_args()
    modes = ("full_cold", "progressive_cold", "lazy_cold") if args.mode == "all" else (args.mode,)
    started = perf_counter()
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "modes": {}}
    for mode in modes:
        payload["modes"][mode] = run_mode(mode, args.repo_url, args.client_id)
    payload["total_wall_ms"] = round((perf_counter() - started) * 1000, 2)
    output = PROJECT_ROOT / "evaluation" / f"benchmark-controlled-{args.mode}-latest.json"
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({mode: data.get("ingestion_summary") for mode, data in payload["modes"].items()}, indent=2))
    exit_code = 0
    for mode, data in payload["modes"].items():
        index_rc = (data.get("index") or {}).get("_subprocess", {}).get("returncode")
        if index_rc not in (0, None):
            exit_code = max(exit_code, int(index_rc))
        asks = data.get("lazy_asks") or {}
        if asks.get("subprocess_returncode") not in (0, None):
            exit_code = max(exit_code, int(asks["subprocess_returncode"]))
        if asks.get("ok") is False:
            exit_code = max(exit_code, 1)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
