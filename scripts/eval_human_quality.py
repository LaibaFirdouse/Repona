#!/usr/bin/env python3
"""Cold index + warm re-ask with human-style questions and real LLM; score sourcing from trace."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal, create_database_tables  # noqa: E402
from app.models.catalog import IndexGeneration  # noqa: E402
from app.services.client_session import ensure_client_user  # noqa: E402
from app.services.refresh_service import RefreshService  # noqa: E402
from app.retrieval.answer import AnswerService  # noqa: E402
from benchmark_index_large import run_force_index  # noqa: E402
from benchmark_suite import DEFAULT_CLIENT_ID, ensure_benchmark_repository_access, _generation_stats  # noqa: E402

_META_PATH = re.compile(
    r"(^readme\.md$|/readme\.md$|makefile$|cmakelists\.txt$|\.md$|package\.json$|\.github/)",
    re.I,
)

# Benchmark repos for human-style questions (eval harness only—not retrieval routing).
HUMAN_EVAL_REPO_TIERS: list[tuple[str, str]] = [
    ("small", "https://github.com/pallets/itsdangerous"),
    ("medium", "https://github.com/pallets/flask"),
    ("large", "https://github.com/LaibaFirdouse/Repona"),
    ("systems", "https://github.com/shuraih775/pool-memalloc"),
]


def _human_questions(tier: str) -> list[str]:
    bank = {
        "small": [
            "How does the library make sure a signed message was not tampered with?",
            "What happens when a time-limited signature is no longer valid?",
        ],
        "medium": [
            "When a client sends an HTTP request, how does the application pick which code runs?",
            "How are errors during request handling surfaced back to the caller?",
        ],
        "large": [
            "When a user asks a question about a repository, what happens before the model receives any source text?",
            "How does the backend combine different search methods to gather evidence for an answer?",
            "Explain the end-to-end flow from indexing a GitHub repo to answering a question about it.",
        ],
        "systems": [
            "Explain the core path from building the project to running the throughput measurement on fixed CPU cores.",
            "How exactly does the queue behave when it is full and the producer keeps trying to add work?",
        ],
    }
    return bank.get(tier, bank["medium"])


def _retrieval_summary(trace: dict) -> dict:
    spans = trace.get("spans_before_budget") or trace.get("packet_spans") or []
    paths = [str(s.get("relative_path") or "") for s in spans if isinstance(s, dict)]
    impl = [p for p in paths if p and not _META_PATH.search(p.replace("\\", "/"))]
    router = trace.get("retrieval_router") or {}
    return {
        "intent": router.get("intent") if isinstance(router, dict) else router,
        "paths_in_pack": paths[:12],
        "implementation_paths": impl[:12],
        "prefer_implementation": trace.get("prefer_implementation"),
        "vectors_ready": trace.get("vectors_ready"),
        "lexical_hits": len(trace.get("text_anchor_ids") or []),
        "cold_retrieval_ms": trace.get("total_ms"),
    }


def _score_answer(
    *,
    question: str,
    answer: str,
    citations: list[dict],
    trace: dict,
    tier: str,
) -> dict:
    paths = [str(c.get("path") or "") for c in citations]
    impl_paths = [p for p in paths if p and not _META_PATH.search(p.replace("\\", "/"))]
    meta_paths = [p for p in paths if p and p not in impl_paths]
    packet = trace.get("packet_spans") or []
    packet_paths = [str(p.get("relative_path") or "") for p in packet if isinstance(p, dict)]
    text = (answer or "").strip()
    words = len(text.split())
    has_citations = len(paths) > 0
    clarity = "ok"
    if words < 40:
        clarity = "thin"
    elif words > 800:
        clarity = "verbose"
    if "excerpt" in text.lower() or "E1" in text:
        clarity = "meta_leak"
    accuracy_heuristic = "unknown"
    if not text:
        accuracy_heuristic = "empty"
    elif not impl_paths and meta_paths:
        accuracy_heuristic = "docs_only"
    elif impl_paths:
        accuracy_heuristic = "code_sourced"
    elif has_citations:
        accuracy_heuristic = "cited_minimal"
    return {
        "citation_paths": paths,
        "implementation_paths": impl_paths,
        "documentation_paths": meta_paths,
        "packet_paths": packet_paths[:12],
        "intent": (trace.get("retrieval_router") or {}).get("intent") or trace.get("retrieval_router"),
        "catalog_path_filters": trace.get("catalog_path_filters"),
        "answer_word_count": words,
        "clarity": clarity,
        "accuracy_heuristic": accuracy_heuristic,
        "citations_valid": trace.get("citations_valid"),
    }


def _run_ask(db, repo_url: str, client_id: str, question: str) -> dict:
    refresh = RefreshService()
    repository = ensure_benchmark_repository_access(db, repo_url, client_id)
    outcome = refresh.ask(db, repository.id, client_id)
    if outcome.action != "serve":
        return {"ok": False, "error": outcome.message or outcome.action, "question": question}
    trace: dict = {"record_steps": True}
    started = perf_counter()
    error = None
    answered = None
    try:
        answered = AnswerService().answer(db, question, outcome, trace=trace)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "error": error,
            "question": question,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "trace": trace,
            "retrieval_only": _retrieval_summary(trace),
        }
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    if error or answered is None:
        return {"ok": False, "error": error or "no answer", "question": question, "latency_ms": elapsed_ms, "trace": trace}
    score = _score_answer(
        question=question,
        answer=answered.answer,
        citations=list(answered.citations),
        trace=trace,
        tier="",
    )
    return {
        "ok": True,
        "question": question,
        "latency_ms": elapsed_ms,
        "retrieval_ms": trace.get("total_ms"),
        "answer_preview": (answered.answer or "")[:1200],
        "answer_full_length": len(answered.answer or ""),
        "citation_count": len(answered.citations),
        "score": score,
        "trace_summary": {
            "intent": score.get("intent"),
            "vectors_ready": trace.get("vectors_ready"),
            "packet_span_count": len(trace.get("packet_spans") or []),
            "prefer_implementation": trace.get("prefer_implementation"),
            "code_first": trace.get("code_first"),
        },
    }


def _run_repo(
    *,
    repo_url: str,
    tier: str,
    client_id: str,
    index_mode: str,
    do_index: bool,
    env_overrides: dict[str, str],
) -> dict:
    for key, value in env_overrides.items():
        os.environ[key] = value
    row: dict = {
        "repo_url": repo_url,
        "tier": tier,
        "index_mode": index_mode,
        "env": env_overrides,
        "questions": _human_questions(tier),
    }
    if do_index:
        t0 = perf_counter()
        row["index"] = run_force_index(repo_url, client_id)
        row["index_wall_ms"] = round((perf_counter() - t0) * 1000, 2)
    create_database_tables()
    db = SessionLocal()
    try:
        ensure_client_user(db, client_id)
        repository = ensure_benchmark_repository_access(db, repo_url, client_id)
        gen = db.get(IndexGeneration, repository.active_generation_id)
        row["catalog"] = _generation_stats(db, gen.id) if gen else {}
    finally:
        db.close()

    runs: list[dict] = []
    for question in row["questions"]:
        db = SessionLocal()
        try:
            cold = _run_ask(db, repo_url, client_id, question)
            cold["phase"] = "cold_ask"
            warm = _run_ask(db, repo_url, client_id, question)
            warm["phase"] = "warm_ask"
        finally:
            db.close()
        runs.append({"question": question, "cold": cold, "warm": warm})
    row["asks"] = runs
    row["ok"] = all(
        item["cold"].get("ok") and item["warm"].get("ok") for item in runs
    )
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--json", default="evaluation/eval-human-quality-latest.json")
    parser.add_argument("--markdown", default="evaluation/eval-human-quality-report.md")
    parser.add_argument("--skip-index", action="store_true")
    args = parser.parse_args()

    lazy_env = {
        "PROGRESSIVE_INDEXING": "true",
        "EMBED_CATALOGUE_BEFORE_ACTIVATE": "false",
        "PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST": "false",
    }
    full_env = {
        "PROGRESSIVE_INDEXING": "false",
        "EMBED_CATALOGUE_BEFORE_ACTIVATE": "true",
        "PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST": "true",
    }

    plan = [(tier, url, lazy_env, "lazy_cold") for tier, url in HUMAN_EVAL_REPO_TIERS]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": os.popen(f"git -C {PROJECT_ROOT} rev-parse HEAD").read().strip(),
        "llm_provider": settings.llm_provider,
        "repos": [],
    }

    for tier, repo_url, env, mode in plan:
        print(f"=== {tier} {repo_url} ({mode}) ===", flush=True)
        try:
            block = _run_repo(
                repo_url=repo_url,
                tier=tier,
                client_id=args.client_id,
                index_mode=mode,
                do_index=not args.skip_index,
                env_overrides=env,
            )
        except Exception as exc:
            block = {"ok": False, "repo_url": repo_url, "tier": tier, "error": f"{type(exc).__name__}: {exc}"}
        report["repos"].append(block)
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if not args.skip_index:
        print("=== medium flask full_cold re-index ===", flush=True)
        try:
            block = _run_repo(
                repo_url="https://github.com/pallets/flask",
                tier="medium",
                client_id=args.client_id,
                index_mode="full_cold",
                do_index=True,
                env_overrides=full_env,
            )
            block["note"] = "second pass full embed index on flask only"
            report["repos"].append(block)
        except Exception as exc:
            report["repos"].append(
                {"ok": False, "tier": "medium", "index_mode": "full_cold", "error": str(exc)}
            )
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    md = _markdown(report)
    Path(args.markdown).write_text(md, encoding="utf-8")
    print(md)
    failed = [r for r in report["repos"] if not r.get("ok")]
    return 1 if failed else 0


def _markdown(report: dict) -> str:
    lines = [
        "# Human-style ask evaluation",
        "",
        f"Generated: {report['generated_at']}",
        f"LLM: **{report.get('llm_provider')}** · commit `{report.get('git_head', '')[:12]}`",
        "",
    ]
    for repo in report.get("repos") or []:
        lines.append(f"## {repo.get('tier', '?')} — `{repo.get('repo_url', '')}` ({repo.get('index_mode', '')})")
        if repo.get("error"):
            lines.append(f"**Failed:** {repo['error']}")
            lines.append("")
            continue
        cat = repo.get("catalog") or {}
        lines.append(f"Files **{cat.get('files')}** · chunks **{cat.get('chunks')}** · index wall **{repo.get('index_wall_ms')}** ms")
        lines.append("")
        for block in repo.get("asks") or []:
            lines.append(f"### Q: {block['question']}")
            for phase in ("cold", "warm"):
                ask = block.get(phase) or {}
                if not ask.get("ok"):
                    lines.append(f"- **{phase}:** FAIL — {ask.get('error')}")
                    continue
                sc = ask.get("score") or {}
                lines.append(
                    f"- **{phase}:** {ask.get('latency_ms')} ms · {sc.get('accuracy_heuristic')} · "
                    f"clarity **{sc.get('clarity')}** · impl paths: `{', '.join(sc.get('implementation_paths') or [])[:5]}`"
                )
            lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
