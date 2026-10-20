"""Compare deterministic-only, semantic-always, and intelligent router retrieval quality."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import sys

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from benchmark_ask_probe import paths_recall, run_ask_probe  # noqa: E402
from benchmark_suite import DEFAULT_CLIENT_ID  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = PROJECT_ROOT / "evaluation" / "router-correctness-cases.json"
DEFAULT_REPO = "https://github.com/LaibaFirdouse/Repona"

MODE_ENV = {
    "deterministic_only": {"LAZY_EMBED_ON_QUERY": "false"},
    "semantic_enabled": {},
    "intelligent_router": {},
}

MODE_SETUP = {
    "deterministic_only": """
import app.retrieval.query_router as query_router
query_router.expand_vector_lane = lambda request, **kwargs: request
""",
    "semantic_enabled": """
import dataclasses
import app.retrieval.query_router as query_router
_orig_route = query_router.route_question
def _force_vector(question, **kwargs):
    request = _orig_route(question, **kwargs)
    lanes = list(request.lanes)
    if "vector" not in lanes:
        lanes.append("vector")
    return dataclasses.replace(request, lanes=lanes)
query_router.route_question = _force_vector
""",
    "intelligent_router": "",
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
    parser.add_argument("--output", default="evaluation/benchmark-router-correctness-latest.json")
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    modes = ("deterministic_only", "semantic_enabled", "intelligent_router")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **_git_provenance(),
        "repo_url": args.repo_url,
        "modes": {},
    }
    exit_code = 0
    for mode in modes:
        mode_rows = []
        recalls: list[float] = []
        for case in cases:
            probe = run_ask_probe(
                repo_url=args.repo_url,
                client_id=args.client_id,
                question=case["question"],
                setup_code=MODE_SETUP[mode],
                env_overrides=MODE_ENV.get(mode) or None,
            )
            recall = paths_recall(probe.get("retrieved_paths") or [], case.get("expect_paths_any") or [])
            lanes = probe.get("lanes_requested") or []
            expect_lanes = case.get("expect_lanes_any") or []
            lane_ok = True if not expect_lanes else any(item in lanes for item in expect_lanes)
            passed = probe.get("ok") and recall["hit"] and lane_ok
            if not passed:
                exit_code = 1
            if probe.get("subprocess_returncode", 0) not in (0, None):
                exit_code = 2
            recalls.append(recall["recall_at_k"])
            mode_rows.append(
                {
                    "case_id": case["id"],
                    "passed": passed,
                    "elapsed_ms": probe.get("elapsed_ms"),
                    "recall_at_k": recall["recall_at_k"],
                    "lanes_requested": lanes,
                    "lane_hit_counts": probe.get("lane_hit_counts"),
                    "vector_lane_expanded": probe.get("vector_lane_expanded"),
                    "matched_paths": recall["matched"],
                    "error": probe.get("error") or probe.get("harness_error"),
                }
            )
        report["modes"][mode] = {
            "mean_recall_at_k": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
            "cases": mode_rows,
        }

    out = PROJECT_ROOT / args.output
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {mode: data["mean_recall_at_k"] for mode, data in report["modes"].items()},
            indent=2,
        )
    )
    return exit_code if exit_code else (0 if all(row["passed"] for mode in report["modes"].values() for row in mode["cases"]) else 1)


if __name__ == "__main__":
    raise SystemExit(main())
