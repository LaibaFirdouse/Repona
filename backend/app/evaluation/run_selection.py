"""Record method selection with behavior cues, then the same questions without them.

The model is not called. Fusion ranking stays as it is. Cues are disabled only
inside this process, for the second pass.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import app.retrieval.answer as answer_module
from app.db.session import SessionLocal
from app.evaluation.selection import SELECTION
from app.ingestion.claims import insert_reader_pin
from app.ingestion.coordination import lease_deadline
from app.models.catalog import IndexGeneration, SourceRevision
from app.models.repository import Repository
from app.retrieval.answer import AnswerService
from app.retrieval.packet import INSUFFICIENT_EVIDENCE, NO_CITED_CLAIM
from app.services.refresh_service import RefreshOutcome


def main() -> None:
    db = SessionLocal()
    calls = {"n": 0}

    def stub(system_message: str, user_message: str, max_output_tokens: int) -> str:
        calls["n"] += 1
        return '{"claims":[]}'

    service = AnswerService(complete=stub)
    repos = {repo.normalized_url: repo for repo in db.query(Repository).all()}
    warmup = next(case for case in SELECTION if case["id"] == "cue_dispatch")
    cold = _ask(db, service, repos, warmup, calls)
    cold["pass"] = "cold"
    with_cues = _pass(db, service, repos, calls, "with_cues")
    answer_module.behavior_concepts = lambda question: []
    without_cues = _pass(db, service, repos, calls, "without_cues")
    payload = {
        "model": "stub; ollama was not called",
        "cold_warmup": cold,
        "with_cues": with_cues,
        "without_cues": without_cues,
        "summary": {
            "with_cues": _summary(with_cues),
            "without_cues": _summary(without_cues),
        },
    }
    print(json.dumps(payload, indent=2))
    db.close()


def _pass(db, service, repos, calls, label: str) -> list[dict]:
    records = []
    for case in SELECTION:
        calls["n"] = 0
        record = _ask(db, service, repos, case, calls)
        record["pass"] = label
        records.append(record)
    return records


def _ask(db, service: AnswerService, repos: dict, case: dict, calls: dict) -> dict:
    repo = repos[case["repository"]]
    generation = db.get(IndexGeneration, repo.active_generation_id)
    now = datetime.utcnow()
    deadline = lease_deadline(now)
    pin = insert_reader_pin(
        db,
        repository_id=repo.id,
        generation_id=generation.id,
        source_revision_id_value=generation.source_revision_id,
        expires_at=deadline,
        hard_deadline=deadline + timedelta(minutes=5),
    )
    db.commit()
    revision = db.get(SourceRevision, generation.source_revision_id)
    outcome = RefreshOutcome(
        "serve",
        repo.id,
        repo.normalized_url,
        repo.tracked_branch,
        revision.commit_sha,
        generation.id,
        "selection",
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
    packet = trace.get("packet_spans") or []
    packet_names = [span.get("qualified_name") for span in packet]
    citations = [] if answered is None else answered.citations
    answer = "" if answered is None else answered.answer
    cited_names = _cited_names(citations, packet)
    expected = list(case["expected"])
    unsupported = list(case["unsupported"])
    technologies = [item.get("name") for item in trace.get("packet_technologies") or []]
    strategy = trace.get("answer_strategy")
    abstained = strategy == "abstain" or answer in {NO_CITED_CLAIM, INSUFFICIENT_EVIDENCE}
    return {
        "id": case["id"],
        "kind": case["kind"],
        "question": case["question"],
        "repository": case["repository"],
        "note": case["note"],
        "expected": expected,
        "unsupported": unsupported,
        "error": error,
        "strategy": strategy,
        "model_calls": model_calls,
        "retrieval_seconds": trace.get("retrieval_seconds"),
        "packet": packet_names,
        "selected": cited_names,
        "expected_in_packet": {name: name in packet_names for name in expected},
        "expected_selected": {name: name in cited_names for name in expected},
        "unsupported_selected": {name: name in cited_names for name in unsupported},
        "citations": citations,
        "citation_lines_match": _citation_hits(citations, case.get("lines") or {}, packet),
        "technologies": technologies,
        "expected_technology_in_packet": _technology_present(case, technologies),
        "abstained": abstained,
        "early_abstain": strategy == "abstain" and model_calls == 0,
        "desired_abstain": bool(case["abstain"]),
        "answer": answer,
    }


def _cited_names(citations: list[dict], packet: list[dict]) -> list[str]:
    by_id = {span.get("chunk_id"): span.get("qualified_name") for span in packet}
    names = []
    for citation in citations:
        name = by_id.get(citation.get("chunk_id"))
        if name and name not in names:
            names.append(name)
    return names


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


def _technology_present(case: dict, technologies: list[str]) -> bool | None:
    expected = case.get("expected_technology")
    if not expected:
        return None
    return any(name == expected for name in technologies)


def _summary(records: list[dict]) -> dict:
    expected_hits = 0
    expected_total = 0
    unsupported_hits = 0
    abstain_hits = 0
    abstain_total = 0
    model_calls = 0
    for record in records:
        for present in record["expected_selected"].values():
            expected_total += 1
            expected_hits += int(bool(present))
        unsupported_hits += sum(bool(present) for present in record["unsupported_selected"].values())
        if record["desired_abstain"]:
            abstain_total += 1
            abstain_hits += int(record["early_abstain"])
        model_calls += int(record["model_calls"] or 0)
    return {
        "expected_symbols_selected": expected_hits,
        "expected_symbols": expected_total,
        "unsupported_symbols_selected": unsupported_hits,
        "early_abstain_when_desired": abstain_hits,
        "desired_abstain": abstain_total,
        "model_calls": model_calls,
    }


if __name__ == "__main__":
    main()
