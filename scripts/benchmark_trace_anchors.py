#!/usr/bin/env python3
"""Retrieval-only probes for trace-anchor and entry-point path recall (stub LLM)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from benchmark_ask_probe import evidence_paths, paths_recall, run_ask_probe  # noqa: E402
from benchmark_suite import DEFAULT_CLIENT_ID  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PROJECT_ROOT / "evaluation" / "trace-anchor-cases.json"


def _git_head() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--output", default="evaluation/benchmark-trace-anchors-latest.json")
    parser.add_argument("--skip-optional", action="store_true")
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    rows: list[dict] = []
    exit_code = 0
    for case in cases:
        if args.skip_optional and case.get("optional"):
            rows.append({**case, "skipped": True, "ok": True})
            continue
        probe = run_ask_probe(
            repo_url=case["repo_url"],
            client_id=args.client_id,
            question=case["question"],
            env_overrides={"LAZY_EMBED_ON_QUERY": "true", "PROGRESSIVE_INDEXING": "true"},
        )
        paths = evidence_paths(probe)
        recall = paths_recall(paths, case.get("expect_paths_any") or [])
        min_substantive = int(case.get("min_substantive_spans") or 0)
        substantive = int(probe.get("substantive_span_count") or 0)
        substantive_ok = substantive >= min_substantive if min_substantive else True
        trace_chunks = int(probe.get("trace_anchor_chunks") or 0)
        expect_trace = case.get("expect_trace_anchors")
        trace_ok = trace_chunks >= 1 if expect_trace else True
        row = {
            "id": case["id"],
            "repo_url": case["repo_url"],
            "question": case["question"],
            "ok": probe.get("ok") and recall.get("hit") and substantive_ok and trace_ok,
            "probe_ok": probe.get("ok"),
            "path_recall": recall,
            "substantive_span_count": substantive,
            "min_substantive_spans": min_substantive,
            "substantive_ok": substantive_ok,
            "trace_anchor_chunks": probe.get("trace_anchor_chunks"),
            "trace_anchor_graph_hops": probe.get("trace_anchor_graph_hops"),
            "trace_anchor_out_degree_added": probe.get("trace_anchor_out_degree_added"),
            "trace_anchors_ok": trace_ok,
            "span_byte_samples": probe.get("span_byte_samples") or [],
            "retrieved_paths": paths[:16],
            "elapsed_ms": probe.get("elapsed_ms"),
            "error": probe.get("error") or probe.get("harness_error"),
        }
        if case.get("optional") and not row["ok"]:
            row["ok"] = True
            row["warn"] = "optional case missed path recall (repo may be unindexed)"
        elif not row["ok"]:
            exit_code = 1
        rows.append(row)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
        "cases": rows,
    }
    out = Path(args.output)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        status = "ok" if row.get("ok") else "FAIL"
        print(f"{status} {row['id']}: {row.get('path_recall', {}).get('matched')}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
