"""Force a full index of a large repo with per-step ingestion + ask traces (separate output files)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.core.config import PROJECT_ROOT, settings
from app.db.session import SessionLocal, create_database_tables
from app.ingestion.benchmark_trace import ingestion_benchmark_trace, record_step
from app.ingestion.pipeline import ingest_commit
from app.ingestion.upstream import fetch_branch, ls_remote_head
from app.models.repository import Repository
from app.services.client_session import ensure_client_user
from app.services.refresh_service import EMBEDDING_MODEL, MODEL_REVISION, RefreshService, VECTOR_DIMENSIONS, _absolute, _embed
from app.storage.identity import normalize_repository_url

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from benchmark_detailed import detailed_repository  # noqa: E402
from benchmark_suite import DEFAULT_CLIENT_ID  # noqa: E402

DEFAULT_REPO = "https://github.com/LaibaFirdouse/Repona"
DEFAULT_QUESTION = "How does AnswerService retrieve evidence for a question?"


def _step(steps: list, name: str, started: float, **extra) -> None:
    row = {"step": name, "ms": round((perf_counter() - started) * 1000, 2)}
    row.update(extra)
    steps.append(row)


def _markdown(report: dict) -> str:
    lines = [
        "# Large-repo index + ask (detailed steps)",
        "",
        f"Generated: {report['generated_at']}",
        f"Repository: `{report['repo_url']}`",
        f"Commit: `{report.get('commit_sha', '')[:12]}…`" if report.get("commit_sha") else "",
        "",
        "## Ingestion / fetch (timed steps)",
        "",
    ]
    for step in report.get("ingestion_steps") or []:
        extra = ", ".join(f"{k}={v}" for k, v in step.items() if k not in {"step", "ms"})
        suffix = f" ({extra})" if extra else ""
        lines.append(f"- `{step['step']}`: **{step['ms']}** ms{suffix}")
    queryable = next((s for s in report.get("ingestion_steps") or [] if s["step"] == "ingest.time_to_queryable"), None)
    lines.extend(["", f"**Ingestion total (ingest.ingest_commit_total):** {report.get('ingestion_total_ms')} ms"])
    if queryable:
        lines.append(f"**Time to queryable (ingest.time_to_queryable):** {queryable['ms']} ms")
    lines.append("")
    detail = report.get("ask_detail") or {}
    if not detail.get("ok"):
        lines.append(f"Ask failed: {detail.get('error')}")
        return "\n".join(lines) + "\n"
    stats = detail.get("stats") or {}
    lines.extend(
        [
            "## Post-index catalog",
            "",
            f"- Files: **{stats.get('files')}** · Chunks: **{stats.get('chunks')}** · Symbol chunks: **{stats.get('symbol_chunks')}**",
            "",
            "## Ask pipeline (same breakdown as benchmark-detailed)",
            "",
        ]
    )
    trace = detail.get("ask_trace") or {}
    lines.append("| Step | ms |")
    lines.append("|------|-----|")
    for step in trace.get("steps") or []:
        lines.append(f"| `{step['step']}` | {step['ms']} |")
    lines.append("")
    if trace.get("stage_seconds_note"):
        lines.append(f"*{trace['stage_seconds_note']}*")
        lines.append("")
    lines.append("**Retrieval substage wall times (seconds):**")
    for name, ms in (trace.get("stage_seconds_ms") or {}).items():
        lines.append(f"- {name}: **{ms}** ms")
    lane = trace.get("lane_trace") or {}
    if lane:
        lines.append("")
        lines.append(f"**Lanes requested:** `{lane.get('lanes_requested')}` · hit counts: `{lane.get('lane_hit_counts')}`")
    return "\n".join(lines) + "\n"


def run_force_index(repo_url: str, user_id: str) -> dict:
    normalized = normalize_repository_url(repo_url)
    service = RefreshService()
    db = SessionLocal()
    ingest_steps: list[dict] = []
    report: dict = {"repo_url": repo_url, "ingestion_steps": ingest_steps}
    try:
        ensure_client_user(db, user_id)
        t = perf_counter()
        probe = ls_remote_head(normalized, "")
        _step(ingest_steps, "preflight.ls_remote_head", t, upstream=probe.upstream, branch=probe.branch)
        if probe.upstream != "authorized" or not probe.sha or not probe.branch:
            report["error"] = f"probe failed: {probe.upstream}"
            return report
        report["commit_sha"] = probe.sha
        report["branch"] = probe.branch

        # Ensure repository row + membership (no-op when already indexed at this commit).
        t = perf_counter()
        service.index(db, repo_url, probe.branch, user_id, None)
        _step(ingest_steps, "preflight.refresh_index_join_or_reuse", t)
        db.expire_all()
        repository = db.query(Repository).filter(Repository.normalized_url == normalized).one()
        git_dir = _absolute(repository.git_dir)

        with ingestion_benchmark_trace() as traced:
            t = perf_counter()
            fetched_sha = fetch_branch(git_dir, normalized, probe.branch, "")
            record_step("ingest.fetch_branch_total", t, sha=fetched_sha[:12])
            if fetched_sha != probe.sha:
                report["error"] = "fetched sha does not match remote head"
                ingest_steps.extend(traced)
                return report

            def run_graph(session) -> None:
                ingest_commit(
                    db,
                    repository_id=repository.id,
                    git_dir=git_dir,
                    commit_sha=fetched_sha,
                    owner=user_id,
                    embed=_embed,
                    embedding_model=EMBEDDING_MODEL,
                    vector_dimensions=VECTOR_DIMENSIONS,
                    snapshot_root=PROJECT_ROOT / "data" / "snapshots",
                    model_revision=MODEL_REVISION,
                    qdrant_url=service.qdrant_url,
                    graph_session=session,
                )

            t = perf_counter()
            service._with_graph(run_graph)
            record_step("ingest.pipeline_with_neo4j_session", t)
            ingest_steps.extend(traced)

        total = next((s for s in ingest_steps if s["step"] == "ingest.ingest_commit_total"), None)
        report["ingestion_total_ms"] = total["ms"] if total else None
        report["generation_id"] = repository.active_generation_id
        db.refresh(repository)
        report["generation_id"] = repository.active_generation_id
        return report
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", default=DEFAULT_REPO)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--json", default="evaluation/benchmark-large-index-latest.json")
    parser.add_argument("--markdown", default="evaluation/benchmark-large-index-report.md")
    args = parser.parse_args()

    create_database_tables()
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_url": args.repo_url,
        "question": args.question,
        "note": "Prior benchmark-detailed-* files are unchanged; this run uses separate outputs.",
    }
    indexed = run_force_index(args.repo_url, args.client_id)
    report.update(indexed)
    if not indexed.get("error"):
        db = SessionLocal()
        try:
            report["ask_detail"] = detailed_repository(db, args.repo_url, args.question, args.client_id)
        finally:
            db.close()

    Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md = _markdown(report)
    Path(args.markdown).write_text(md, encoding="utf-8")
    print(md)
    if report.get("error"):
        print(report["error"], file=sys.stderr)
        return 1
    detail = report.get("ask_detail") or {}
    if not detail.get("ok"):
        if detail.get("error"):
            print(detail["error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
