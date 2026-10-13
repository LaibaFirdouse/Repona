"""Record discovery, packet membership, citations, and explanations. The model is not called."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta

from app.db.session import SessionLocal
from app.evaluation.baseline import CAMEL, CAMEL_SYMBOLS, JS_SOURCE, LIVE
from app.ingestion.claims import insert_reader_pin
from app.ingestion.coordination import lease_deadline
from app.models.catalog import Chunk, GenerationChunk, IndexGeneration, SourceRevision, Symbol
from app.models.repository import Repository
from app.retrieval.answer import AnswerService
from app.retrieval.expansion import choose_expansion_symbols
from app.retrieval.implementation import explain_implementation
from app.retrieval.packet import INSUFFICIENT_EVIDENCE, NO_CITED_CLAIM
from app.services.refresh_service import RefreshOutcome

_QUOTE = re.compile(r"`([^`]+)`")


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "before-camel"
    db = SessionLocal()
    calls = {"n": 0}

    def stub(system_message: str, user_message: str, max_output_tokens: int) -> str:
        calls["n"] += 1
        return '{"claims":[]}'

    service = AnswerService(complete=stub)
    repos = {repo.normalized_url: repo for repo in db.query(Repository).all()}
    records = []
    warmup = next(case for case in LIVE if case["id"] == "flask_exact")
    cold = _ask(db, service, repos, warmup, calls)
    cold["pass"] = "cold"
    for case in LIVE:
        calls["n"] = 0
        record = _ask(db, service, repos, case, calls)
        record["pass"] = "warm"
        records.append(record)
    payload = {
        "label": label,
        "model": "stub; ollama was not called",
        "cold_warmup": cold,
        "cases": records,
        "javascript": _javascript(),
        "camel_case": _camel(),
    }
    print(json.dumps(payload, indent=2))
    db.close()


def _ask(db, service: AnswerService, repos: dict, case: dict, calls: dict) -> dict:
    repo = repos[case["repository"]]
    generation = db.get(IndexGeneration, repo.active_generation_id)
    revision_id = generation.source_revision_id
    now = datetime.utcnow()
    deadline = lease_deadline(now)
    pin = insert_reader_pin(
        db,
        repository_id=repo.id,
        generation_id=generation.id,
        source_revision_id_value=revision_id,
        expires_at=deadline,
        hard_deadline=deadline + timedelta(minutes=5),
    )
    db.commit()
    revision = db.get(SourceRevision, revision_id)
    outcome = RefreshOutcome(
        "serve",
        repo.id,
        repo.normalized_url,
        repo.tracked_branch,
        revision.commit_sha,
        generation.id,
        "baseline",
        None if pin is None else pin.id,
    )
    trace: dict = {}
    error = None
    answered = None
    try:
        if pin is None:
            raise RuntimeError("reader pin was not created")
        answered = service.answer(db, case["question"], outcome, trace)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return _record(db, generation.id, case, trace, answered, calls["n"], error)


def _record(db, generation_id: str, case: dict, trace: dict, answered, model_calls: int, error: str | None) -> dict:
    lane_ids = trace.get("candidate_chunk_ids") or []
    selected = trace.get("spans_before_budget") or []
    packet = trace.get("packet_spans") or []
    lane_names = _qualified_names(db, generation_id, lane_ids)
    selected_names = [span.get("qualified_name") for span in selected]
    packet_names = [span.get("qualified_name") for span in packet]
    citations = [] if answered is None else answered.citations
    answer = "" if answered is None else answered.answer
    expected = list(case["expected"])
    discovered = {name: name in lane_names or name in selected_names for name in expected}
    in_packet = {name: name in packet_names for name in expected}
    cited = _citation_hits(citations, case.get("lines") or {}, packet)
    return {
        "id": case["id"],
        "question": case["question"],
        "repository": case["repository"],
        "expected": expected,
        "known_miss": bool(case.get("known_miss")),
        "abstain": bool(case.get("abstain")),
        "error": error,
        "strategy": trace.get("answer_strategy"),
        "model_calls": model_calls,
        "retrieval_seconds": trace.get("retrieval_seconds"),
        "stage_seconds": trace.get("stage_seconds"),
        "lane_candidates": lane_names,
        "selection_set": selected_names,
        "packet": packet_names,
        "discovered": discovered,
        "in_packet": in_packet,
        "citations": citations,
        "citation_lines_match": cited,
        "quotes_in_cited_source": _quotes_supported(answer, citations, packet),
        "no_claim_line": answer in {NO_CITED_CLAIM, INSUFFICIENT_EVIDENCE},
        "abstained": trace.get("answer_strategy") == "abstain",
        "answer": answer,
    }


def _qualified_names(db, generation_id: str, chunk_ids: list[str]) -> list[str]:
    if not chunk_ids:
        return []
    rows = (
        db.query(Chunk, Symbol.qualified_name)
        .outerjoin(Symbol, Symbol.id == Chunk.symbol_id)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(GenerationChunk.index_generation_id == generation_id, Chunk.id.in_(chunk_ids))
        .all()
    )
    found = {chunk.id: qualified or "" for chunk, qualified in rows}
    return [found.get(chunk_id, "") for chunk_id in chunk_ids]


def _citation_hits(citations: list[dict], lines: dict, packet: list[dict]) -> dict:
    by_name = {}
    for span in packet:
        by_name.setdefault(span.get("qualified_name"), []).append(span.get("chunk_id"))
    hits = {}
    for name, expected in lines.items():
        matched = False
        for citation in citations:
            if citation.get("chunk_id") not in by_name.get(name, []):
                continue
            start = int(citation.get("start_line") or 0)
            end = int(citation.get("end_line") or 0)
            matched = start <= expected[1] and end >= expected[0]
        hits[name] = matched
    return hits


def _quotes_supported(answer: str, citations: list[dict], packet: list[dict]) -> bool | None:
    if not answer or answer in {NO_CITED_CLAIM, INSUFFICIENT_EVIDENCE}:
        return None
    cited_ids = {item.get("chunk_id") for item in citations}
    text = "\n".join(span.get("text") or "" for span in packet if span.get("chunk_id") in cited_ids)
    quotes = _QUOTE.findall(answer)
    if not quotes:
        return False
    folded = "".join(text.split())
    return all("".join(quote.split()) in folded for quote in quotes)


def _javascript() -> dict:
    steps = explain_implementation(JS_SOURCE, "javascript", "build")
    text = " ".join(steps)
    return {
        "id": "javascript_fixture",
        "question": "What does build return when name is missing, and what does it call?",
        "expected_quotes": ["prefix.concat", "!name"],
        "steps": steps,
        "quotes_present": "prefix.concat" in text and "!name" in text,
        "model_calls": 0,
    }


def _camel() -> list[dict]:
    rows = []
    for case in CAMEL:
        chosen = [item["qualified_name"] for item in choose_expansion_symbols(case["question"], list(CAMEL_SYMBOLS))]
        rows.append(
            {
                "question": case["question"],
                "expected": case["expected"],
                "chosen": chosen,
                "discovered": case["expected"] in chosen,
            }
        )
    return rows


if __name__ == "__main__":
    main()
