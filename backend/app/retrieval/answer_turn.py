"""Multi-turn answer protocol: final claims or a bounded request for more evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.config import settings
from app.retrieval.model_call import (
    REPAIR_INSTRUCTION,
    REPAIR_NOTE,
    ClaimFailureNote,
    _count_call,
    _keep_raw,
    _one_claim,
    extract_json_object,
    parse_claims,
)
from app.retrieval.prompt_budget import resolve_prompt_budget

Status = Literal["final", "need_evidence"]


@dataclass(frozen=True)
class AnswerTurn:
    status: Status
    claims: list[dict]
    more_evidence: dict | None


def parse_answer_turn(raw: str) -> AnswerTurn | None:
    """Parse one answer turn. Claims beyond LLM_MAX_CLAIMS are dropped from the end, not rejected."""
    if len(raw.encode("utf-8")) > settings.llm_max_response_bytes:
        return None
    body = extract_json_object(raw)
    if not isinstance(body, dict):
        return None
    status = body.get("status")
    if status not in {"final", "need_evidence"}:
        legacy = body.get("claims")
        if isinstance(legacy, list):
            legacy = legacy[: settings.llm_max_claims]
            claims = [c for c in (_one_claim(item) for item in legacy) if c is not None]
            if claims or legacy == []:
                return AnswerTurn("final", claims, None)
        return None
    if status == "need_evidence":
        more = body.get("more_evidence")
        if not isinstance(more, dict):
            return None
        return AnswerTurn("need_evidence", [], _normalize_more_evidence(more))
    claims = body.get("claims")
    if not isinstance(claims, list):
        return None
    claims = claims[: settings.llm_max_claims]
    kept = [claim for claim in (_one_claim(item) for item in claims) if claim is not None]
    if not kept and claims:
        return None
    return AnswerTurn("final", kept, None)


def _normalize_more_evidence(payload: dict) -> dict:
    lexical: list[str] = []
    for item in payload.get("lexical_queries") or []:
        if isinstance(item, str) and item.strip():
            lexical.append(item.strip()[:200])
    paths: list[str] = []
    for item in payload.get("path_contains") or []:
        if isinstance(item, str) and item.strip():
            paths.append(item.strip()[:120])
    note = str(payload.get("note") or "").strip()[:500]
    return {
        "note": note,
        "lexical_queries": lexical[: settings.answer_more_evidence_max_queries],
        "path_contains": paths[: settings.answer_more_evidence_max_paths],
    }


def _turn_from_legacy_claims(raw: str) -> AnswerTurn | None:
    parsed = parse_claims(raw)
    if not parsed.accepted or not parsed.claims:
        return None
    return AnswerTurn("final", parsed.claims, None)


def collect_answer_turn(
    system_message: str,
    user_message: str,
    max_output_tokens: int,
    complete,
    renew,
    *,
    allow_more_evidence: bool,
    record: ClaimFailureNote | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> AnswerTurn | None:
    renew()
    _count_call(record)
    raw = complete(system_message, user_message, max_output_tokens)
    turn = parse_answer_turn(raw)
    if turn is None:
        turn = _turn_from_legacy_claims(raw)
    if turn is not None:
        _keep_raw(record, raw)
        if turn.status == "need_evidence" and not allow_more_evidence:
            legacy = _turn_from_legacy_claims(raw)
            if legacy is not None:
                return legacy
            return AnswerTurn("final", [], None)
        return turn
    repair_user = user_message + REPAIR_NOTE + raw + REPAIR_INSTRUCTION
    if _repair_exceeds_context(
        system_message,
        repair_user,
        max_output_tokens,
        provider=provider,
        model=model,
    ):
        return None
    renew()
    _count_call(record)
    repaired = complete(system_message, repair_user, max_output_tokens)
    turn = parse_answer_turn(repaired)
    if turn is None:
        turn = _turn_from_legacy_claims(repaired)
    if turn is not None:
        _keep_raw(record, repaired)
    return turn


def _repair_exceeds_context(
    system_message: str,
    repair_user: str,
    max_output_tokens: int,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> bool:
    return resolve_prompt_budget(provider, model).repair_exceeds(
        system_message,
        repair_user,
        max_output_tokens,
    )


MORE_EVIDENCE_SUFFIX = (
    '\nYou may respond with {"status":"need_evidence","more_evidence":{"note":"...",'
    '"lexical_queries":["..."],"path_contains":["..."]}} when the repository excerpts below '
    "are not enough to answer well. Use need_evidence at most once; prefer final when you can answer."
)

FINAL_ONLY_SUFFIX = (
    '\nRespond with one JSON object and nothing else: {"status":"final","claims":[{"kind":"observed",'
    '"text":"...","evidence_ids":["E1"]}]}. '
    f"Use at most {settings.llm_max_claims} claims; merge related points into one claim rather than "
    "dropping them. Each text is one paragraph shown to the user and may use Markdown, including a short "
    "fenced code quote. Put excerpt ids only in evidence_ids."
)

# Only used in a round that may still request evidence (ANSWER_EVIDENCE_ROUNDS > 0).
JSON_FORMAT_SUFFIX = (
    '\nRespond with one JSON object and nothing else: either {"status":"final","claims":[...]} '
    f"(at most {settings.llm_max_claims} claims) "
    'or {"status":"need_evidence","more_evidence":{"note":"...","lexical_queries":[],"path_contains":[]}}.'
)
