"""Validate lazy retrieval: materialisation, on-demand embed, lanes, citations, repeat cache."""

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
CASES_PATH = PROJECT_ROOT / "evaluation" / "lazy-retrieval-cases.json"
DEFAULT_REPO = "https://github.com/LaibaFirdouse/Repona"

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


def _score_probe(case: dict, probe: dict) -> dict:
    expect = case.get("expect_paths_any") or []
    recall = paths_recall(probe.get("retrieved_paths") or [], expect)
    lanes = probe.get("lanes_requested") or []
    expect_lanes = case.get("expect_lanes_any") or []
    lane_ok = True if not expect_lanes else any(lane in lanes for lane in expect_lanes)
    embed_unavail_ok = True
    if probe.get("embed_fail_mode"):
        embed_unavail_ok = probe.get("ok") and (
            (probe.get("fused_anchor_count") or 0) > 0
            or (probe.get("packet_span_count") or 0) > 0
            or recall["hit"]
        )
    return {
        **recall,
        "lane_expectation_met": lane_ok,
        "embed_fallback_ok": embed_unavail_ok,
        "passed": probe.get("ok") and recall["hit"] and lane_ok and embed_unavail_ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", default=DEFAULT_REPO)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--output", default="evaluation/benchmark-lazy-validation-latest.json")
    parser.add_argument("--skip-repeat", action="store_true")
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **_git_provenance(),
        "repo_url": args.repo_url,
        "env": LAZY_COLD_ENV,
        "cases": [],
    }
    exit_code = 0
    for case in cases:
        first = run_ask_probe(
            repo_url=args.repo_url,
            client_id=args.client_id,
            question=case["question"],
            env_overrides=LAZY_COLD_ENV,
        )
        first_score = _score_probe(case, first)
        repeat = None
        repeat_score = None
        if not args.skip_repeat:
            repeat = run_ask_probe(
                repo_url=args.repo_url,
                client_id=args.client_id,
                question=case["question"],
                env_overrides=LAZY_COLD_ENV,
            )
            repeat_score = _score_probe(case, repeat)
        embed_unavail = None
        embed_score = None
        if case.get("run_embed_unavailable"):
            embed_unavail = run_ask_probe(
                repo_url=args.repo_url,
                client_id=args.client_id,
                question=case["question"],
                env_overrides=LAZY_COLD_ENV,
                embed_fail=True,
            )
            embed_unavail["embed_fail_mode"] = True
            embed_score = _score_probe(case, embed_unavail)
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
            "passed": item["first_query"]["score"]["passed"],
            "first_ms": item["first_query"].get("elapsed_ms"),
            "repeat_ms": None if item["repeat_query"] is None else item["repeat_query"].get("elapsed_ms"),
            "vectors_added_first": item["first_query"].get("vectors_added"),
            "vectors_added_repeat": None if item["repeat_query"] is None else item["repeat_query"].get("vectors_added"),
        }
        for item in report["cases"]
    ]
    print(json.dumps(summary, indent=2))
    if exit_code:
        return exit_code
    return 0 if all(item["first_query"]["score"]["passed"] for item in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
