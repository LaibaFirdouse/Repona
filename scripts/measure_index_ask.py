"""One repo: optional full index with step trace, then one ask with pipeline step trace."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.db.session import SessionLocal, create_database_tables
from app.ingestion.lazy_vectors import count_vector_ready
from app.models.catalog import GenerationChunk, IndexGeneration, SourceRevision
from app.models.repository import Repository
from app.storage.identity import normalize_repository_url

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from benchmark_detailed import detailed_repository  # noqa: E402
from benchmark_index_large import run_force_index  # noqa: E402
from benchmark_suite import DEFAULT_CLIENT_ID, _generation_stats  # noqa: E402


def _catalog_snapshot(db, generation_id: str) -> dict:
    ready = count_vector_ready(db, generation_id)
    pending = (
        db.query(GenerationChunk)
        .filter(
            GenerationChunk.index_generation_id == generation_id,
            GenerationChunk.vector_status == "pending",
        )
        .count()
    )
    return {"vectors_ready": ready, "vectors_pending": pending}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--index", action="store_true", help="Force full re-index with ingestion step trace")
    parser.add_argument("--json", default="evaluation/measure-index-ask-latest.json")
    parser.add_argument("--markdown", default="evaluation/measure-index-ask-report.md")
    args = parser.parse_args()

    create_database_tables()
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_url": args.repo_url,
        "question": args.question,
        "index_requested": args.index,
    }

    if args.index:
        report["index"] = run_force_index(args.repo_url, args.client_id)

    db = SessionLocal()
    try:
        normalized = normalize_repository_url(args.repo_url)
        repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
        if repository is None or not repository.active_generation_id:
            report["error"] = "no active generation; pass --index or index via UI first"
            Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            return 1
        generation = db.get(IndexGeneration, repository.active_generation_id)
        revision = db.get(SourceRevision, generation.source_revision_id) if generation else None
        report["catalog"] = {
            "generation_id": generation.id,
            "status": generation.status,
            "commit_sha": revision.commit_sha if revision else None,
            "stats": _generation_stats(db, generation.id),
            "vectors": _catalog_snapshot(db, generation.id),
        }
    finally:
        db.close()

    db = SessionLocal()
    try:
        report["ask"] = detailed_repository(db, args.repo_url, args.question, args.client_id)
    finally:
        db.close()

    Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md = _markdown(report)
    Path(args.markdown).write_text(md, encoding="utf-8")
    print(md)
    if report.get("index", {}).get("error"):
        return 1
    if not (report.get("ask") or {}).get("ok"):
        return 1
    return 0


def _markdown(report: dict) -> str:
    lines = [
        "# Index + ask stage report",
        "",
        f"**Repo:** `{report['repo_url']}`",
        f"**Question:** {report['question']}",
        f"**Generated:** {report['generated_at']}",
        "",
    ]
    catalog = report.get("catalog") or {}
    if catalog:
        stats = catalog.get("stats") or {}
        vectors = catalog.get("vectors") or {}
        lines.extend(
            [
                "## Current index (catalog snapshot)",
                "",
                f"- Generation: `{catalog.get('generation_id', '')[:12]}…` · status **{catalog.get('status')}**",
                f"- Commit: `{str(catalog.get('commit_sha', ''))[:12]}…`",
                f"- Files **{stats.get('files')}** · chunks **{stats.get('chunks')}** · symbol chunks **{stats.get('symbol_chunks')}**",
                f"- Vectors ready **{vectors.get('vectors_ready')}** · pending **{vectors.get('vectors_pending')}**",
                "",
            ]
        )
    index = report.get("index")
    if index:
        lines.append("## Ingestion steps (this run only)")
        lines.append("")
        if index.get("error"):
            lines.append(f"- **Failed:** {index['error']}")
        else:
            for step in index.get("ingestion_steps") or []:
                extra = ", ".join(f"{k}={v}" for k, v in step.items() if k not in ("step", "ms"))
                suffix = f" ({extra})" if extra else ""
                lines.append(f"- `{step['step']}`: **{step['ms']}** ms{suffix}")
            if index.get("ingestion_total_ms") is not None:
                lines.append(f"- **ingest.ingest_commit_total:** **{index['ingestion_total_ms']}** ms")
        lines.append("")
    ask = report.get("ask") or {}
    if not ask.get("ok"):
        lines.append(f"## Ask failed\n\n{ask.get('error')}\n")
        return "\n".join(lines) + "\n"
    lines.append("## Ask pipeline")
    lines.append("")
    for step in ask.get("steps") or []:
        extra = ", ".join(f"{k}={v}" for k, v in step.items() if k not in ("step", "ms"))
        suffix = f" ({extra})" if extra else ""
        lines.append(f"- `{step['step']}`: **{step['ms']}** ms{suffix}")
    trace = ask.get("ask_trace") or {}
    lines.extend(["", "### AnswerService steps (trace)", "", "| Step | ms |", "|------|-----|"])
    for step in trace.get("steps") or []:
        lines.append(f"| `{step['step']}` | {step['ms']} |")
    lines.extend(["", "### Retrieval substages (wall time)", ""])
    for name, ms in (trace.get("stage_seconds_ms") or {}).items():
        lines.append(f"- **{name}:** {ms} ms")
    lines.append(f"- **Total ask (trace):** {trace.get('total_ms')} ms")
    lane = ask.get("lane_sequential") or {}
    if lane:
        lines.extend(["", "### Lanes (sequential probe)", ""])
        for name, ms in lane.items():
            if name.endswith("_ms"):
                lines.append(f"- {name}: **{ms}** ms")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
