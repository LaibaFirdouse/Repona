"""Bounded execution-trace investigation: plan stages, follow calls, organise evidence.

Seeds and stages are derived from the question and the indexed catalogue. Call and HTTP
relationships are recorded only when source evidence establishes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.catalog import File, GenerationFile, IndexGeneration, Symbol
from app.retrieval.chunk_selection import is_test_path, pick_best_symbol_chunk, pick_symbol_implementation_chunks
from app.retrieval.fusion import ChunkChoice
from app.retrieval.query_plan import QueryIntent
from app.retrieval.query_terms import (
    extract_search_terms,
    question_identifiers,
    question_paths,
    question_qualified_names,
)
from app.retrieval.capability_profile import (
    CapabilityProfile,
    CapabilityState,
    load_capability_profile,
    unknown_profile,
)
from app.retrieval.route_adapters import (
    catalog_route_seed_choices,
    extract_framework_routes_from_text,
    files_for_http_path,
    handler_symbols_for_route,
    is_next_route_file,
)
from app.retrieval.structure import ObservedCall, symbol_structure

# Stages that require frontend UI capability when the question asks for them.
_FRONTEND_STAGES = frozenset({"event_handler", "frontend_update", "http_request"})
# Stages that rely on HTTP route discovery adapters.
_HTTP_STAGES = frozenset({"api_handler", "http_request"})

# Catalogue of possible stages. Only question/evidence-selected stages are "applicable".
STAGE_ORDER = (
    "entry_point",
    "event_handler",
    "http_request",
    "api_handler",
    "authentication",
    "validation",
    "business_logic",
    "ai_or_rules",
    "database",
    "response",
    "frontend_update",
)

# Generic vocabulary for classifying already-retrieved spans — not used to invent seeds.
_STAGE_EVIDENCE: dict[str, tuple[str, ...]] = {
    "entry_point": ("main", "cli", "entrypoint", "page.", "layout.", "handler", "onclick"),
    "event_handler": ("onclick", "onsubmit", "addEventListener", "handle", "click", "submit"),
    "http_request": ("fetch(", "axios.", "requests.", "httpx.", "client.get", "client.post", "method:"),
    "api_handler": ("route.ts", "route.js", "apirouter", "@app.", "@router.", "fastapi", "express", "endpoint"),
    "authentication": ("auth", "session", "login", "middleware", "authorize", "bearer", "jwt", "oauth"),
    "validation": ("validate", "schema", "pydantic", "zod", "safeparse", "basemodel"),
    "business_logic": ("service", "usecase", "use_case", "controller", "orchestrat"),
    "ai_or_rules": ("openai", "completion", "prompt", "llm", "anthropic", "chat.completions"),
    "database": (
        "sqlalchemy",
        "session.execute",
        "session.add",
        "mongoose",
        "prisma",
        "insert",
        "update",
        "delete",
        "save(",
        "commit(",
        "find_one",
        "findone",
        "insert_many",
        "insertmany",
        "repository",
        "query(",
    ),
    "response": ("jsonresponse", "nextresponse", "response(", "return", "res.", "httpresponse"),
    "frontend_update": ("setstate", "usestate", "router.push", "navigate(", "location", "dispatch("),
}

_QUESTION_STAGE_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("entry_point", (r"\bentry(?:\s+point)?\b", r"\bcli\b", r"\bmain\b", r"\bstartup\b")),
    ("event_handler", (r"\bclick\b", r"\bsubmit\b", r"\bbutton\b", r"\bevent\s+handler\b")),
    ("http_request", (r"\bhttp\b", r"\brequest\b", r"\bfetch\b", r"\bapi\s+call\b")),
    ("api_handler", (r"\bapi\b", r"\broute\b", r"\bendpoint\b", r"\bhandler\b", r"\bfastapi\b", r"\bexpress\b")),
    ("authentication", (r"\bauth(?:entication|orization)?\b", r"\bsession\b", r"\blogin\b")),
    ("validation", (r"\bvalidat", r"\bschema\b", r"\bpydantic\b")),
    ("business_logic", (r"\bservice\b", r"\bbusiness\b", r"\bworkflow\b", r"\bgenerat", r"\bprocess\b")),
    ("ai_or_rules", (r"\bai\b", r"\bllm\b", r"\bopenai\b", r"\bprompt\b", r"\brule\s*engine\b")),
    ("database", (r"\bdb\b", r"\bdatabase\b", r"\bpersist", r"\bmongo", r"\bsql\b", r"\borm\b", r"\bsave\b", r"\binsert\b")),
    ("response", (r"\bresponse\b", r"\breturn\b")),
    ("frontend_update", (r"\bfrontend\b", r"\bui\b", r"\bstate\b", r"\breact\b")),
)

_TRACE_CUES = re.compile(
    r"\b("
    r"request\s*flow|execution\s*(?:path|flow|trace)|call\s*(?:flow|sequence|chain)|"
    r"end[- ]to[- ]end|step by step|walk through|what happens when|"
    r"from\s+frontend|to\s+(?:database|db|api)|how\s+does\s+(?:auth|authentication)|"
    r"call(?:ed|s)?\s+from|trace"
    r")\b",
    re.I,
)

_HTTP_FETCH = re.compile(
    r"""(?:fetch|axios\.(?:get|post|put|patch|delete)|\$\.(?:get|post|ajax)|"""
    r"""requests\.(?:get|post|put|patch|delete)|httpx\.(?:get|post|put|patch|delete))"""
    r"""\s*\(\s*[`'"]([^`'"]+)[`'"]""",
    re.I,
)
_HTTP_METHOD = re.compile(r"""method\s*:\s*[`'"](GET|POST|PUT|PATCH|DELETE)[`'"]""", re.I)
_AXIOS_VERB = re.compile(r"axios\.(get|post|put|patch|delete)\s*\(", re.I)
_REQUESTS_VERB = re.compile(r"(?:requests|httpx)\.(get|post|put|patch|delete)\s*\(", re.I)

_STOP_FEATURE = frozenset(
    {
        "exact",
        "request",
        "flow",
        "explain",
        "trace",
        "complete",
        "when",
        "user",
        "about",
        "this",
        "that",
        "the",
        "and",
        "for",
        "from",
        "into",
        "with",
        "new",
        "repo",
        "repository",
        "code",
        "path",
        "through",
        "start",
        "follow",
        "actual",
        "happens",
        "what",
        "how",
        "does",
        "will",
        "their",
        "your",
    }
)

_BUILTIN_CALLEES = frozenset(
    {
        "fetch",
        "json",
        "stringify",
        "parse",
        "log",
        "error",
        "warn",
        "map",
        "filter",
        "reduce",
        "push",
        "pop",
        "then",
        "catch",
        "finally",
        "settimeout",
        "setinterval",
        "require",
        "encodeuricomponent",
        "decodeuricomponent",
        "print",
        "len",
        "str",
        "int",
        "list",
        "dict",
        "range",
        "isinstance",
        "getattr",
        "setattr",
        "usestate",
        "useeffect",
        "userouter",
        "usesession",
        "signin",
        "signout",
    }
)
_MAX_INVESTIGATION_PASSES = 3
_MAX_FOLLOWUP_SYMBOLS = 12
_MAX_FOLLOWUP_CHOICES = 16


@dataclass(frozen=True)
class TraceStage:
    key: str
    label: str
    target_terms: tuple[str, ...] = ()


@dataclass
class VerifiedRelationship:
    kind: str  # "calls_resolved" | "calls_observed" | "http_route" | "unresolved"
    caller_path: str
    caller_symbol: str
    callee_name: str
    callee_path: str = ""
    callee_symbol: str = ""
    evidence_chunk_id: str = ""
    note: str = ""


@dataclass
class InvestigationPlan:
    intent: QueryIntent
    feature_terms: list[str] = field(default_factory=list)
    lexical_queries: list[str] = field(default_factory=list)
    path_hints: list[str] = field(default_factory=list)
    stages: list[TraceStage] = field(default_factory=list)
    not_applicable_stages: list[str] = field(default_factory=list)
    blocked_stages: list[str] = field(default_factory=list)
    strategies: list[str] = field(default_factory=list)
    stage_status: dict[str, str] = field(default_factory=dict)
    capability_generation_id: str = ""
    entry_action: str = ""
    stop_reason: str = ""
    pass_count: int = 0
    verified: list[VerifiedRelationship] = field(default_factory=list)
    missing_stages: list[str] = field(default_factory=list)
    target_symbols: list[str] = field(default_factory=list)
    grounded: bool = False
    capability_notes: list[str] = field(default_factory=list)

    def as_trace_dict(self) -> dict:
        return {
            "feature_terms": self.feature_terms[:12],
            "lexical_queries": self.lexical_queries[:8],
            "path_hints": self.path_hints[:8],
            "stages": [stage.key for stage in self.stages],
            "not_applicable_stages": self.not_applicable_stages[:12],
            "blocked_stages": self.blocked_stages[:12],
            "strategies": self.strategies[:12],
            "stage_status": dict(list(self.stage_status.items())[:16]),
            "capability_generation_id": self.capability_generation_id,
            "capability_notes": self.capability_notes[:8],
            "entry_action": self.entry_action,
            "pass_count": self.pass_count,
            "stop_reason": self.stop_reason,
            "grounded": self.grounded,
            "verified": [
                {
                    "kind": item.kind,
                    "caller": f"{item.caller_path}:{item.caller_symbol}",
                    "callee": item.callee_name,
                    "resolved_to": f"{item.callee_path}:{item.callee_symbol}" if item.callee_symbol else "",
                    "note": item.note,
                }
                for item in self.verified[:24]
            ],
            "missing_stages": self.missing_stages[:12],
            "target_symbols": self.target_symbols[:16],
        }


def wants_execution_trace_plan(question: str, intent: QueryIntent) -> bool:
    del question
    return intent == "execution_trace"


def build_investigation_plan(
    question: str,
    history: list | None = None,
    *,
    profile: CapabilityProfile | None = None,
) -> InvestigationPlan:
    """Question-derived plan, refined by repository capability when provided."""
    text = question.strip()
    blob_parts = [text]
    if history:
        for turn in history[-3:]:
            blob_parts.append(getattr(turn, "question", "") or "")
            answer = getattr(turn, "answer", "") or ""
            if answer:
                blob_parts.append(answer[:600])
    blob = " ".join(part for part in blob_parts if part)
    terms = [term for term in extract_search_terms(blob) if len(term) >= 3][:16]
    feature = _feature_terms(blob, terms)
    stages, not_applicable = _select_stages(blob)
    path_hints = _path_hints_from_question(blob, feature)
    lexical = list(dict.fromkeys(feature + terms + question_qualified_names(blob)))[:8]
    entry = _entry_action(blob)
    plan = InvestigationPlan(
        intent="execution_trace",
        feature_terms=feature,
        lexical_queries=lexical,
        path_hints=path_hints,
        stages=stages,
        not_applicable_stages=not_applicable,
        entry_action=entry,
    )
    if profile is not None:
        apply_capability_profile(plan, profile, question_blob=blob)
    else:
        plan.strategies = ["lexical_catalogue_search", "source_span_retrieval"]
    return plan


def apply_capability_profile(
    plan: InvestigationPlan,
    profile: CapabilityProfile,
    *,
    question_blob: str = "",
) -> InvestigationPlan:
    """Adjust applicable/blocked stages and strategies from generation capabilities."""
    plan.capability_generation_id = profile.generation_id
    plan.capability_notes = list(profile.notes[:6])
    question_wanted = {stage.key for stage in plan.stages}
    lower = question_blob.lower()

    # Drop stages that the repository cannot support and the question did not require.
    not_applicable = set(plan.not_applicable_stages)
    blocked: list[str] = []
    kept: list[TraceStage] = []

    frontend_state = profile.state("frontend_ui")
    http_state = profile.state("http_route_discovery")
    calls_state = profile.state("calls_resolution")
    tracing_state = profile.state("execution_tracing")

    for stage in plan.stages:
        key = stage.key
        if key in _FRONTEND_STAGES and frontend_state == CapabilityState.NOT_APPLICABLE:
            if key in question_wanted and _question_requires_frontend(lower, key):
                blocked.append(key)
                plan.stage_status[key] = "blocked_unsupported"
            else:
                not_applicable.add(key)
                plan.stage_status[key] = "not_applicable"
            continue
        if key in _HTTP_STAGES and http_state == CapabilityState.NOT_APPLICABLE:
            # Backend-only Python without route adapters: API stage may still apply via source.
            if key == "api_handler" and profile.frameworks:
                kept.append(stage)
                plan.stage_status[key] = "applicable"
                continue
            if key in question_wanted and re.search(r"\b(api|route|endpoint|http)\b", lower):
                if http_state == CapabilityState.NOT_APPLICABLE and not profile.frameworks:
                    # Still allow lexical investigation of handlers when question asks.
                    kept.append(stage)
                    plan.stage_status[key] = "applicable_lexical"
                    continue
            if key == "http_request" and frontend_state == CapabilityState.NOT_APPLICABLE:
                if not re.search(r"\b(fetch|http\s+request|client)\b", lower):
                    not_applicable.add(key)
                    plan.stage_status[key] = "not_applicable"
                    continue
        if key == "database" and profile.state("database_orm") == CapabilityState.UNSUPPORTED:
            if key in question_wanted:
                blocked.append(key)
                plan.stage_status[key] = "blocked_unsupported"
            else:
                not_applicable.add(key)
                plan.stage_status[key] = "not_applicable"
            continue
        kept.append(stage)
        plan.stage_status[key] = "applicable"

    # Backend-only: ensure frontend stages stay not applicable when question never asked.
    if frontend_state == CapabilityState.NOT_APPLICABLE:
        for key in _FRONTEND_STAGES:
            if key not in {stage.key for stage in kept} and key not in blocked:
                not_applicable.add(key)
                plan.stage_status.setdefault(key, "not_applicable")

    # Unsupported semantic depth: keep lexical stages but note limitation.
    if tracing_state in {CapabilityState.UNSUPPORTED, CapabilityState.UNKNOWN} and calls_state == CapabilityState.UNSUPPORTED:
        plan.capability_notes.append("semantic_calls_unavailable")
        for stage in kept:
            if plan.stage_status.get(stage.key) == "applicable":
                plan.stage_status[stage.key] = "applicable_lexical"

    plan.stages = [stage for key in STAGE_ORDER for stage in kept if stage.key == key]
    plan.blocked_stages = list(dict.fromkeys(blocked))
    plan.not_applicable_stages = [key for key in STAGE_ORDER if key in not_applicable and key not in {s.key for s in plan.stages}]
    plan.strategies = _strategies_for_profile(profile, plan)
    return plan


def _question_requires_frontend(lower: str, stage_key: str) -> bool:
    if stage_key == "frontend_update":
        return bool(re.search(r"\b(frontend|ui|react|vue|angular)\b", lower))
    if stage_key == "event_handler":
        return bool(re.search(r"\b(click|submit|button|event\s+handler)\b", lower))
    if stage_key == "http_request":
        return bool(re.search(r"\b(fetch|from\s+frontend|client\s+request)\b", lower))
    return False


def _strategies_for_profile(profile: CapabilityProfile, plan: InvestigationPlan) -> list[str]:
    strategies: list[str] = []
    calls = profile.state("calls_resolution")
    http = profile.state("http_route_discovery")
    protocol = profile.state("protocol_links")
    imports = profile.state("import_resolution")

    strategies.append("source_span_retrieval")
    strategies.append("lexical_catalogue_search")
    if calls == CapabilityState.SUPPORTED:
        strategies.append("follow_verified_calls")
    elif calls == CapabilityState.PARTIAL:
        strategies.extend(["follow_verified_calls", "inspect_source_call_sites"])
    elif calls == CapabilityState.UNKNOWN:
        strategies.append("inspect_source_call_sites")
    # UNSUPPORTED → no call-following strategy

    if http in {CapabilityState.SUPPORTED, CapabilityState.PARTIAL}:
        strategies.append("discover_http_routes")
    elif any(stage.key in _HTTP_STAGES for stage in plan.stages):
        strategies.append("lexical_route_inspection")

    if protocol in {CapabilityState.SUPPORTED, CapabilityState.PARTIAL} and profile.is_usable("multi_language"):
        strategies.append("follow_protocol_links")

    if imports in {CapabilityState.UNSUPPORTED, CapabilityState.UNKNOWN, CapabilityState.PARTIAL}:
        if "lexical_catalogue_search" not in strategies:
            strategies.append("lexical_catalogue_search")

    # Fully unsupported semantic depth collapses to lexical/source only.
    if calls == CapabilityState.UNSUPPORTED and profile.max_language_level <= 0:
        strategies = ["lexical_catalogue_search", "source_span_retrieval"]

    return list(dict.fromkeys(strategies))


def ground_investigation_plan(
    db: Session,
    generation: IndexGeneration,
    plan: InvestigationPlan,
    *,
    profile: CapabilityProfile | None = None,
) -> InvestigationPlan:
    """Retain only seeds that match the indexed catalogue; apply generation capabilities."""
    from app.retrieval.catalog_context import generation_has_path, _files_matching_terms

    if profile is None:
        profile = load_capability_profile(db, generation)
    if profile.generation_id != generation.id:
        # Never continue with another generation's capability decisions.
        profile = unknown_profile(generation.id, reason="stale_capability_generation")
        plan.capability_notes.append("rejected_foreign_capability_profile")
    apply_capability_profile(plan, profile)

    grounded_paths: list[str] = []
    seen: set[str] = set()

    def add_path(raw: str) -> None:
        token = raw.strip().strip("/")
        key = token.lower()
        if not token or key in seen:
            return
        if not generation_has_path(db, generation.id, token):
            return
        seen.add(key)
        grounded_paths.append(token)

    for hint in plan.path_hints:
        add_path(hint)
    terms = list(dict.fromkeys(plan.feature_terms + plan.lexical_queries))[:12]
    files = _files_matching_terms(db, generation.id, terms, limit=24) if terms else []
    for file in files:
        path = file.relative_path.replace("\\", "/")
        if is_test_path(path):
            continue
        add_path(path)
        stem = path.rsplit("/", 1)[-1]
        if "." in stem:
            stem = stem.rsplit(".", 1)[0]
        if len(stem) >= 4:
            add_path(stem)

    symbol_terms = _catalog_symbol_names(db, generation, terms)
    feature = list(dict.fromkeys(plan.feature_terms + symbol_terms))[:12]
    lexical = list(dict.fromkeys(plan.lexical_queries + symbol_terms))[:8]
    plan.path_hints = grounded_paths[:8]
    plan.feature_terms = feature
    plan.lexical_queries = lexical
    plan.grounded = True
    plan.capability_generation_id = generation.id
    return plan


def _catalog_symbol_names(db: Session, generation: IndexGeneration, terms: list[str]) -> list[str]:
    """Return symbol names from the catalogue that match question terms (case-insensitive)."""
    if not terms:
        return []
    lowered = {term.lower() for term in terms if len(term) >= 3}
    rows = (
        db.query(Symbol.name, Symbol.qualified_name, File.relative_path)
        .join(File, File.id == Symbol.file_id)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(
            GenerationFile.index_generation_id == generation.id,
            Symbol.repository_id == generation.repository_id,
            Symbol.parser_version == generation.parser_version,
            Symbol.kind.in_(("function", "method", "class")),
        )
        .order_by(Symbol.qualified_name, Symbol.id)
        .limit(400)
        .all()
    )
    hits: list[str] = []
    seen: set[str] = set()
    for name, qualified, path in rows:
        if is_test_path(str(path)):
            continue
        name_l = (name or "").lower()
        qual_l = (qualified or "").lower()
        if name_l not in lowered and qual_l not in lowered and not any(term in name_l for term in lowered if len(term) >= 5):
            continue
        token = name or qualified
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        hits.append(token)
        if len(hits) >= 8:
            break
    return hits


def _feature_terms(blob: str, terms: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        token = raw.strip()
        key = token.lower()
        if len(token) < 3 or key in seen or key in _STOP_FEATURE:
            return
        seen.add(key)
        ordered.append(token)

    for match in _TRACE_CUES.finditer(blob):
        add(match.group(0))
    for token in question_qualified_names(blob):
        add(token)
    for token in question_identifiers(blob):
        add(token)
    for term in terms:
        add(term)
    return ordered[:12]


def _select_stages(blob: str) -> tuple[list[TraceStage], list[str]]:
    """Applicable stages from question cues only; everything else is not applicable."""
    lower = blob.lower()
    wanted: set[str] = set()
    for key, patterns in _QUESTION_STAGE_CUES:
        if any(re.search(pattern, lower) for pattern in patterns):
            wanted.add(key)
    # Minimal default for vague "request/execution flow" without domain nouns.
    if not wanted and _TRACE_CUES.search(blob):
        wanted.update({"business_logic", "response"})
        if re.search(r"\brequest|http|api\b", lower):
            wanted.update({"http_request", "api_handler"})
    stages = [
        TraceStage(key=key, label=key.replace("_", " "), target_terms=_STAGE_EVIDENCE.get(key, ()))
        for key in STAGE_ORDER
        if key in wanted
    ]
    not_applicable = [key for key in STAGE_ORDER if key not in wanted]
    return stages, not_applicable


def _path_hints_from_question(blob: str, feature: list[str]) -> list[str]:
    """Only explicit path-like tokens from the question/history — never invented layout paths."""
    hints: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        token = raw.strip().strip("/")
        key = token.lower()
        if not token or key in seen:
            return
        seen.add(key)
        hints.append(token)

    for path in question_paths(blob):
        add(path)
    for term in feature:
        if "/" in term:
            add(term)
    return hints[:8]


def _entry_action(blob: str) -> str:
    if re.search(r"\bclick|submit|button\b", blob, re.I):
        return "user_action"
    if re.search(r"\brequest|http|api\b", blob, re.I):
        return "http_request"
    if re.search(r"\bcli|main|entrypoint\b", blob, re.I):
        return "entry_point"
    return ""


def investigation_seed_choices(
    db: Session,
    generation: IndexGeneration,
    plan: InvestigationPlan,
    *,
    limit: int = 12,
    profile: CapabilityProfile | None = None,
) -> list[ChunkChoice]:
    """Catalogue chunks for grounded path/symbol terms; route seeds via adapters."""
    from app.retrieval.catalog_context import chunk_choices_for_files, _files_matching_terms
    from app.retrieval.workflow_boost import lexical_workflow_chunk_choices

    if not plan.grounded:
        ground_investigation_plan(db, generation, plan, profile=profile)

    choices: list[ChunkChoice] = []
    seen: set[str] = set()

    def add(choice: ChunkChoice | None) -> None:
        if choice is None or choice.chunk_id in seen or len(choices) >= limit:
            return
        seen.add(choice.chunk_id)
        choices.append(choice)

    terms = list(dict.fromkeys(plan.path_hints + plan.feature_terms + plan.lexical_queries))[:12]
    files = _files_matching_terms(db, generation.id, terms, limit=limit * 2) if terms else []
    if files:
        for choice in chunk_choices_for_files(db, generation.id, files, limit=limit, bodies_per_file=2):
            add(choice)
    if plan.lexical_queries or "lexical_catalogue_search" in plan.strategies:
        queries = plan.lexical_queries or plan.feature_terms
        if queries:
            for choice in lexical_workflow_chunk_choices(db, generation, queries, limit=limit):
                add(choice)
    use_routes = "discover_http_routes" in plan.strategies or (
        "lexical_route_inspection" in plan.strategies
        and any(stage.key in {"api_handler", "http_request"} for stage in plan.stages)
    )
    if use_routes and any(stage.key in {"api_handler", "http_request"} for stage in plan.stages):
        for choice in catalog_route_seed_choices(db, generation, terms=terms, limit=max(4, limit // 2)):
            add(choice)
    return choices[:limit]


def expand_execution_trace_evidence(
    db: Session,
    generation: IndexGeneration,
    loaded: list[dict],
    edges: list,
    plan: InvestigationPlan,
    *,
    limit: int = _MAX_FOLLOWUP_CHOICES,
    profile: CapabilityProfile | None = None,
) -> tuple[list[ChunkChoice], InvestigationPlan]:
    """Follow observed calls and HTTP routes from already-loaded spans (bounded)."""
    if plan.capability_generation_id and plan.capability_generation_id != generation.id:
        plan.stop_reason = "capability_generation_mismatch"
        return [], plan

    strategies = set(plan.strategies) or {"lexical_catalogue_search", "source_span_retrieval", "inspect_source_call_sites"}
    follow_calls = "follow_verified_calls" in strategies
    inspect_calls = "inspect_source_call_sites" in strategies or follow_calls
    follow_http = "discover_http_routes" in strategies or "follow_protocol_links" in strategies
    lexical_http = "lexical_route_inspection" in strategies

    choices: list[ChunkChoice] = []
    seen_chunks: set[str] = {str(span.get("chunk_id") or "") for span in loaded}
    seen_symbols: set[str] = {str(span.get("symbol_id") or "") for span in loaded if span.get("symbol_id")}
    plan.pass_count += 1
    seen_verified = {
        (item.kind, item.caller_path, item.caller_symbol, item.callee_name, item.callee_symbol)
        for item in plan.verified
    }

    def remember(rel: VerifiedRelationship) -> None:
        key = (rel.kind, rel.caller_path, rel.caller_symbol, rel.callee_name, rel.callee_symbol)
        if key in seen_verified:
            return
        seen_verified.add(key)
        plan.verified.append(rel)

    def add_symbol(symbol_id: str) -> None:
        if not symbol_id or symbol_id in seen_symbols or len(choices) >= limit:
            return
        for choice in pick_symbol_implementation_chunks(db, generation.id, symbol_id) or []:
            if choice.chunk_id in seen_chunks or len(choices) >= limit:
                continue
            seen_chunks.add(choice.chunk_id)
            choices.append(choice)
        best = pick_best_symbol_chunk(db, generation.id, symbol_id)
        if best is not None and best.chunk_id not in seen_chunks and len(choices) < limit:
            seen_chunks.add(best.chunk_id)
            choices.append(best)
        seen_symbols.add(symbol_id)
        if symbol_id not in plan.target_symbols:
            plan.target_symbols.append(symbol_id)

    if follow_calls:
        for edge in edges:
            if getattr(edge, "relationship_type", "") != "CALLS":
                continue
            if getattr(edge, "resolution", "") != "resolved":
                continue
            target = getattr(edge, "other_id", "") if getattr(edge, "outgoing", False) else getattr(edge, "anchor_id", "")
            source = getattr(edge, "anchor_id", "") if getattr(edge, "outgoing", False) else getattr(edge, "other_id", "")
            if target:
                add_symbol(target)
                remember(
                    VerifiedRelationship(
                        kind="calls_resolved",
                        caller_path="",
                        caller_symbol=str(source),
                        callee_name=str(getattr(edge, "mentioned_name", "") or ""),
                        callee_symbol=str(target),
                        note="index CALLS edge",
                    )
                )

    # Consume verified protocol edges from the graph (not text heuristics alone).
    if "follow_protocol_links" in strategies or "discover_http_routes" in strategies:
        for edge in edges:
            if getattr(edge, "relationship_type", "") != "ROUTES_TO":
                continue
            if getattr(edge, "resolution", "") != "protocol":
                continue
            target = getattr(edge, "other_id", "") if getattr(edge, "outgoing", False) else getattr(edge, "anchor_id", "")
            source = getattr(edge, "anchor_id", "") if getattr(edge, "outgoing", False) else getattr(edge, "other_id", "")
            if not target:
                continue
            add_symbol(target)
            remember(
                VerifiedRelationship(
                    kind="http_route",
                    caller_path="",
                    caller_symbol=str(source),
                    callee_name=str(getattr(edge, "mentioned_name", "") or ""),
                    callee_symbol=str(target),
                    note="index ROUTES_TO protocol edge; not a CALLS edge",
                )
            )

    for span in loaded:
        if str(span.get("chunk_kind") or "") not in {"symbol_body", "symbol_part"}:
            continue
        text = str(span.get("text") or "")
        path = str(span.get("relative_path") or "")
        qualified = str(span.get("qualified_name") or "")
        chunk_id = str(span.get("chunk_id") or "")
        if not text:
            continue
        if follow_http or lexical_http:
            for route_path, method in _http_routes_in_text(text):
                for file in files_for_http_path(db, generation.id, route_path)[:2]:
                    for symbol_id in handler_symbols_for_route(db, generation, file, method):
                        add_symbol(symbol_id)
                        remember(
                            VerifiedRelationship(
                                kind="http_route",
                                caller_path=path,
                                caller_symbol=qualified,
                                callee_name=f"{method} {route_path}",
                                callee_path=file.relative_path,
                                callee_symbol=symbol_id,
                                evidence_chunk_id=chunk_id,
                                note=(
                                    "HTTP protocol correspondence to route implementation; not a CALLS edge"
                                    if "follow_protocol_links" in strategies
                                    else "HTTP path string binds to route implementation; not a CALLS edge"
                                ),
                            )
                        )
            for method, route_path in extract_framework_routes_from_text(text):
                for file in files_for_http_path(db, generation.id, route_path)[:1]:
                    for symbol_id in handler_symbols_for_route(db, generation, file, method):
                        add_symbol(symbol_id)
        if not inspect_calls:
            continue
        language = _language_for_path(path)
        if language is None:
            continue
        try:
            built = symbol_structure(
                text,
                language,
                qualified or path,
                symbol_id=str(span.get("symbol_id") or ""),
                span_hash=str(span.get("span_hash") or ""),
                source_start_byte=int(span.get("source_start_byte") or 0),
                source_end_byte=span.get("source_end_byte"),
            )
        except Exception:
            continue
        for call in built.calls:
            leaf = _callee_leaf(call)
            if not leaf or leaf.lower() in _BUILTIN_CALLEES:
                continue
            resolved = _unique_symbol_by_name(db, generation, leaf)
            if resolved is None:
                remember(
                    VerifiedRelationship(
                        kind="unresolved",
                        caller_path=path,
                        caller_symbol=qualified,
                        callee_name=call.callee,
                        evidence_chunk_id=chunk_id,
                        note="call observed in source; definition not uniquely resolved",
                    )
                )
                continue
            symbol_id, callee_path = resolved
            add_symbol(symbol_id)
            remember(
                VerifiedRelationship(
                    kind="calls_observed",
                    caller_path=path,
                    caller_symbol=qualified,
                    callee_name=call.callee,
                    callee_path=callee_path,
                    callee_symbol=symbol_id,
                    evidence_chunk_id=chunk_id,
                    note="observed call site; unique catalog definition",
                )
            )
            if len(plan.target_symbols) >= _MAX_FOLLOWUP_SYMBOLS:
                break
        if len(plan.target_symbols) >= _MAX_FOLLOWUP_SYMBOLS:
            break

    _refine_stages_from_evidence(plan, loaded)
    # Blocked stages are never "missing evidence" — they cannot be investigated.
    plan.missing_stages = [
        key for key in _missing_stages(plan, loaded) if key not in plan.blocked_stages
    ]
    present = {_stage_for_span(span, plan) for span in loaded}
    for stage in plan.stages:
        if stage.key in plan.blocked_stages:
            plan.stage_status[stage.key] = "blocked_unsupported"
        elif stage.key in present:
            plan.stage_status[stage.key] = "verified"
        elif stage.key in plan.missing_stages:
            plan.stage_status[stage.key] = "unresolved"
    for key in plan.blocked_stages:
        plan.stage_status[key] = "blocked_unsupported"

    if profile is not None and profile.generation_id != generation.id:
        plan.capability_notes.append("ignored_foreign_profile_during_expand")

    if not choices:
        plan.stop_reason = plan.stop_reason or "no_followup_targets"
    elif plan.pass_count >= _MAX_INVESTIGATION_PASSES:
        plan.stop_reason = "pass_budget"
    elif not plan.missing_stages and not plan.blocked_stages:
        plan.stop_reason = "stages_covered"
    elif not plan.missing_stages and plan.blocked_stages:
        plan.stop_reason = "partial_capability_limit"
    else:
        plan.stop_reason = "continue"
    return choices[:limit], plan


def _refine_stages_from_evidence(plan: InvestigationPlan, loaded: list[dict]) -> None:
    """Promote stages that evidence supports; never invent unsupported applicable stages from app names."""
    present = {_stage_for_span(span, plan) for span in loaded}
    present.discard("other")
    applicable = {stage.key for stage in plan.stages}
    for key in present:
        if key in applicable:
            continue
        # Evidence can reveal a stage the question did not name (e.g. auth inside an API handler).
        if key in plan.not_applicable_stages:
            plan.not_applicable_stages = [item for item in plan.not_applicable_stages if item != key]
            plan.stages.append(
                TraceStage(key=key, label=key.replace("_", " "), target_terms=_STAGE_EVIDENCE.get(key, ()))
            )
            applicable.add(key)
    plan.stages = [stage for key in STAGE_ORDER for stage in plan.stages if stage.key == key]


def order_spans_by_execution_stage(spans: list[dict], plan: InvestigationPlan) -> list[dict]:
    if not spans:
        return []
    stage_keys = [stage.key for stage in plan.stages] or ["business_logic"]
    buckets: dict[str, list[dict]] = {key: [] for key in stage_keys}
    buckets["other"] = []
    for span in spans:
        key = _stage_for_span(span, plan)
        if key not in buckets:
            key = "other"
        span = dict(span)
        span["execution_stage"] = key
        buckets[key].append(span)
    ordered: list[dict] = []
    for key in stage_keys:
        ordered.extend(buckets.get(key) or [])
    ordered.extend(buckets.get("other") or [])
    return ordered


def _stage_for_span(span: dict, plan: InvestigationPlan) -> str:
    path = str(span.get("relative_path") or "").replace("\\", "/").lower()
    qname = str(span.get("qualified_name") or "").lower()
    text = str(span.get("text") or "")[:1200].lower()
    blob = f"{path} {qname} {text}"
    question_terms = {term.lower() for term in plan.feature_terms + plan.lexical_queries if len(term) >= 4}
    scores: list[tuple[int, str]] = []
    candidates = plan.stages or [TraceStage(key=k, label=k) for k in STAGE_ORDER]
    for stage in candidates:
        hints = _STAGE_EVIDENCE.get(stage.key, ())
        evidence_score = sum(1 for hint in hints if hint.lower() in blob)
        if stage.key == "api_handler" and (is_next_route_file(path) or "/routers/" in path or path.endswith(("router.py", "routes.py"))):
            evidence_score += 3
        if stage.key == "http_request" and any(token in blob for token in ("fetch(", "axios.", "requests.", "httpx.")):
            evidence_score += 3
        if stage.key == "authentication" and any(token in blob for token in ("auth", "session", "jwt", "oauth", "login")):
            evidence_score += 2
        if stage.key == "database" and any(
            token in blob
            for token in (
                "sqlalchemy",
                "session.add",
                "mongoose",
                "prisma",
                "insert",
                "commit(",
                "execute(",
                "persist_",
                "repository",
            )
        ):
            evidence_score += 3
        if stage.key == "ai_or_rules" and any(token in blob for token in ("openai", "completion", "prompt", "llm")):
            evidence_score += 3
        if stage.key == "entry_point" and any(token in path for token in ("main.py", "cli", "page.tsx", "page.ts", "page.jsx")):
            evidence_score += 2
        # Question identifiers may break ties only when the span already looks like that stage.
        if evidence_score <= 0:
            continue
        question_boost = sum(1 for term in question_terms if term in blob)
        scores.append((evidence_score * 10 + question_boost, stage.key))
    if not scores:
        return "other"
    scores.sort(key=lambda item: (-item[0], stage_keys_index(item[1])))
    return scores[0][1]


def stage_keys_index(key: str) -> int:
    try:
        return STAGE_ORDER.index(key)
    except ValueError:
        return len(STAGE_ORDER)


def _missing_stages(plan: InvestigationPlan, loaded: list[dict]) -> list[str]:
    present = {_stage_for_span(span, plan) for span in loaded}
    return [stage.key for stage in plan.stages if stage.key not in present]


def execution_trace_prompt_suffix(plan: InvestigationPlan) -> str:
    stage_list = ", ".join(stage.label for stage in plan.stages) or "observed control flow"
    covered = [stage.key for stage in plan.stages if stage.key not in plan.missing_stages]
    missing = plan.missing_stages
    lines = [
        "\nAnswer as an ordered execution trace using only the excerpts below.",
        f"Applicable stages for this question: {stage_list}.",
        "For each step: name the file path and qualified symbol, cite the evidence id that contains the source lines,",
        "and state the verified caller→callee or HTTP-route binding when the excerpts establish it.",
        "Treat HTTP protocol correspondence separately from direct function CALLS.",
        "Do not claim a call, database write, or state update unless an excerpt shows it.",
        "If an applicable stage lacks evidence, say so explicitly and continue with the partial trace.",
        "Do not treat stages marked not applicable as missing evidence.",
        "Do not invent relationships for stages blocked by missing repository capabilities.",
    ]
    if plan.strategies:
        lines.append(f"Investigation strategies for this repository: {', '.join(plan.strategies[:8])}.")
    if plan.not_applicable_stages:
        lines.append(f"Stages not applicable to this question: {', '.join(plan.not_applicable_stages[:8])}.")
    if plan.blocked_stages:
        lines.append(
            "Stages requested but unsupported by indexed capabilities: "
            + ", ".join(plan.blocked_stages[:8])
            + "."
        )
    if covered:
        lines.append(f"Stages with retrieved evidence so far: {', '.join(covered)}.")
    if missing:
        lines.append(f"Applicable stages still incomplete: {', '.join(missing)}.")
    if plan.verified:
        verified = [
            f"{item.kind}:{item.caller_symbol}->{item.callee_name}"
            for item in plan.verified
            if item.kind != "unresolved"
        ][:8]
        if verified:
            lines.append("Verified relationships from retrieval: " + "; ".join(verified) + ".")
    return "\n" + "\n".join(lines)


def partial_trace_claims(plan: InvestigationPlan, spans: list[dict]) -> list[dict]:
    """Deterministic observed claims when the model returns nothing usable."""
    by_id = {str(span.get("chunk_id")): span for span in spans if span.get("chunk_id")}
    claims: list[dict] = []
    for item in plan.verified:
        if item.kind == "unresolved":
            continue
        chunk_id = item.evidence_chunk_id
        if not chunk_id or chunk_id not in by_id:
            chunk_id = _span_id_for_path(spans, item.callee_path) or _span_id_for_path(spans, item.caller_path)
        if not chunk_id or chunk_id not in by_id:
            continue
        span = by_id[chunk_id]
        path = span.get("relative_path") or item.caller_path
        symbol = span.get("qualified_name") or item.caller_symbol or path
        if item.kind == "http_route":
            text = (
                f"{symbol} in `{path}` issues an HTTP call to `{item.callee_name}`; "
                f"the matching route implementation is `{item.callee_path}`."
            )
        elif item.kind in {"calls_resolved", "calls_observed"}:
            target = item.callee_symbol or item.callee_name
            text = f"{symbol} in `{path}` calls `{item.callee_name}` (resolved to `{target}`)."
        else:
            continue
        claims.append({"kind": "observed", "text": text, "evidence_ids": [chunk_id]})
        if len(claims) >= 8:
            break
    if plan.missing_stages and claims:
        last = claims[-1]
        missing = ", ".join(plan.missing_stages[:6])
        claims[-1] = {
            **last,
            "text": last["text"] + f" Retrieved evidence does not yet cover applicable stages: {missing}.",
        }
    return claims


def _span_id_for_path(spans: list[dict], path: str) -> str:
    if not path:
        return ""
    needle = path.replace("\\", "/").lower()
    for span in spans:
        if needle and needle in str(span.get("relative_path") or "").replace("\\", "/").lower():
            return str(span.get("chunk_id") or "")
    return ""


def _http_routes_in_text(text: str) -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    for match in _HTTP_FETCH.finditer(text):
        raw = match.group(1).strip()
        if not raw.startswith("/"):
            continue
        window = text[max(0, match.start() - 40) : match.start() + 220]
        method_match = _HTTP_METHOD.search(window) or _AXIOS_VERB.search(window) or _REQUESTS_VERB.search(window)
        if method_match:
            method = method_match.group(1).upper()
        else:
            method = "GET"
        routes.append((raw.split("?")[0], method))
    return routes


def _unique_symbol_by_name(db: Session, generation: IndexGeneration, name: str) -> tuple[str, str] | None:
    if not name or name.lower() in _BUILTIN_CALLEES:
        return None
    rows = (
        db.query(Symbol, File.relative_path)
        .join(File, File.id == Symbol.file_id)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(
            GenerationFile.index_generation_id == generation.id,
            Symbol.repository_id == generation.repository_id,
            Symbol.parser_version == generation.parser_version,
            Symbol.name == name,
            Symbol.kind.in_(("function", "method")),
        )
        .order_by(File.relative_path, Symbol.qualified_name, Symbol.id)
        .limit(8)
        .all()
    )
    kept: list[tuple[str, str]] = []
    for symbol, path in rows:
        if is_test_path(str(path)):
            continue
        kept.append((symbol.id, str(path)))
    if len(kept) != 1:
        return None
    return kept[0]


def _callee_leaf(call: ObservedCall) -> str:
    if call.leaf:
        return call.leaf
    text = (call.callee or "").strip()
    if not text:
        return ""
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text if text.isidentifier() else ""


def _language_for_path(path: str) -> str | None:
    from app.ingestion.parse import language_for_path

    return language_for_path(path)
