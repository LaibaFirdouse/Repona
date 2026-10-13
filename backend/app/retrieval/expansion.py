"""Turn question words into symbol names that already exist. Fusion scores stay unchanged."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOP = frozenset(
    {
        "how",
        "does",
        "the",
        "and",
        "that",
        "with",
        "from",
        "this",
        "what",
        "when",
        "where",
        "which",
        "into",
        "for",
        "are",
        "was",
        "not",
        "can",
        "any",
        "via",
        "its",
        "than",
        "then",
        "them",
        "they",
        "you",
        "your",
        "who",
        "why",
        "about",
        "over",
        "also",
        "just",
        "have",
        "has",
        "had",
        "been",
        "being",
        "will",
        "would",
        "should",
        "could",
        "each",
        "some",
        "such",
        "only",
        "other",
        "using",
        "used",
        "use",
        "while",
        "during",
        "before",
        "after",
        "onto",
        "upon",
        "across",
        "through",
        "between",
        "against",
        "under",
        "above",
        "below",
        "into",
        "from",
    }
)
MAX_EXPANSION_SYMBOLS = 4
MAX_BEHAVIOR_SYMBOLS = 3

# Question cues select search concepts. They do not name a repository's methods.
BEHAVIORS = (
    {
        "id": "view_dispatch",
        "groups": (
            ("match", "request", "view"),
            ("choose", "view"),
            ("route", "view"),
            ("incoming", "request", "view"),
        ),
        "concepts": ("dispatch", "endpoint"),
    },
    {
        "id": "exception_handling",
        "groups": (
            ("exception", "view"),
            ("error", "view"),
            ("raises", "view"),
            ("raise", "error"),
            ("errors", "view"),
            ("fails", "request"),
        ),
        "concepts": ("exception", "handle"),
    },
    {
        "id": "response_creation",
        "groups": (
            ("convert", "response"),
            ("turn", "response"),
            ("build", "response"),
        ),
        "concepts": ("response",),
        "prefer": ("make",),
    },
    {
        "id": "response_processing",
        "groups": (
            ("after", "response"),
            ("process", "response"),
        ),
        "concepts": ("process",),
    },
    {
        "id": "response_finalizing",
        "groups": (("finalize", "response"),),
        "concepts": ("finalize",),
    },
    {
        "id": "request_preprocessing",
        "groups": (
            ("before", "view"),
            ("before", "request"),
            ("prepare", "request"),
            ("setup", "request"),
        ),
        "concepts": ("preprocess", "before"),
    },
)


def content_terms(question: str) -> list[str]:
    """Keep technical words in the order they appear."""
    terms: list[str] = []
    for token in _TOKEN.findall(question):
        word = token.lower()
        if len(word) < 3 or word in _STOP or word in terms:
            continue
        terms.append(word)
    return terms


def expansion_names(question: str) -> list[str]:
    """Join consecutive question words into symbol-shaped names.

    Snake case covers dispatch_request. A lowercased camel form covers getSignature.
    The caller still keeps only a few real symbols and does not rescore fusion.
    """
    terms = content_terms(question)
    names: list[str] = []
    for size in (2, 3):
        for index in range(len(terms) - size + 1):
            window = terms[index : index + size]
            snake = "_".join(window)
            camel = window[0] + "".join(part.capitalize() for part in window[1:])
            for name in (snake, camel.lower()):
                if name not in names:
                    names.append(name)
    return names


def qualifier_terms(question: str) -> set[str]:
    """Capitalized words such as Flask, not ordinary words such as view."""
    found: set[str] = set()
    for token in _TOKEN.findall(question):
        word = token.lower()
        if token[:1].isupper() and len(word) >= 3 and word not in _STOP:
            found.add(word)
    return found


def choose_expansion_symbols(question: str, symbols: list[dict]) -> list[dict]:
    """Keep a few real symbols whose names the question words spell. Tests lose to implementation files."""
    wanted = expansion_names(question)
    if not wanted:
        return []
    qualifiers = qualifier_terms(question)
    by_name: dict[str, list[dict]] = {}
    for symbol in symbols:
        name = str(symbol.get("name") or "").lower()
        if name in wanted:
            by_name.setdefault(name, []).append(symbol)
    chosen: list[dict] = []
    seen: set[str] = set()
    for name in wanted:
        group = by_name.get(name, [])
        if not group:
            continue
        implementation = [symbol for symbol in group if not _is_test(str(symbol.get("path") or ""))]
        pool = implementation or group
        qualified = [symbol for symbol in pool if _head(str(symbol.get("qualified_name") or "")) in qualifiers]
        pool = qualified or pool
        pool = sorted(pool, key=lambda symbol: (str(symbol.get("path") or ""), str(symbol.get("qualified_name") or "")))
        for symbol in pool:
            qualified_name = str(symbol.get("qualified_name") or "")
            if qualified_name in seen:
                continue
            seen.add(qualified_name)
            chosen.append(symbol)
            if len(chosen) >= MAX_EXPANSION_SYMBOLS:
                return chosen
    return chosen


def question_tokens(question: str) -> set[str]:
    return {token.lower() for token in _TOKEN.findall(question) if len(token) >= 3}


def fired_behaviors(question: str) -> list[dict]:
    """Return behaviors whose cue words all appear in the question."""
    tokens = question_tokens(question)
    return [
        behavior
        for behavior in BEHAVIORS
        if any(all(_cue_hit(tokens, word) for word in group) for group in behavior["groups"])
    ]


def _cue_hit(tokens: set[str], word: str) -> bool:
    if word in tokens:
        return True
    if len(word) < 5:
        return False
    return any(token.startswith(word) for token in tokens)


def behavior_concepts(question: str) -> list[str]:
    concepts: list[str] = []
    for behavior in fired_behaviors(question):
        for concept in behavior["concepts"]:
            if concept not in concepts:
                concepts.append(concept)
    return concepts


def choose_behavior_symbols(question: str, symbols: list[dict]) -> list[dict]:
    """Rank real symbols by concept-word overlap. Fusion order is not involved."""
    concepts = behavior_concepts(question)
    if not concepts:
        return []
    concept_set = set(concepts)
    primaries = {behavior["concepts"][0] for behavior in fired_behaviors(question)}
    preferred = _preferred_terms(question)
    qualifiers = qualifier_terms(question)
    scored = []
    for symbol in symbols:
        if _is_test(str(symbol.get("path") or "")):
            continue
        overlap = _name_parts(str(symbol.get("name") or "")) & concept_set
        if overlap:
            scored.append(symbol)
    if qualifiers and any(_head(str(symbol.get("qualified_name") or "")) in qualifiers for symbol in scored):
        scored = [symbol for symbol in scored if _head(str(symbol.get("qualified_name") or "")) in qualifiers]
    scored.sort(key=lambda symbol: _behavior_rank(symbol, concept_set, primaries, preferred))
    chosen: list[dict] = []
    seen: set[str] = set()
    for symbol in scored:
        qualified_name = str(symbol.get("qualified_name") or "")
        if qualified_name in seen:
            continue
        seen.add(qualified_name)
        chosen.append(symbol)
        if len(chosen) >= MAX_BEHAVIOR_SYMBOLS:
            break
    return chosen


def tied_behavior_symbols(question: str, symbols: list[dict]) -> list[dict]:
    """Symbols that share the best rank. A tie stays a tie instead of dropping a method."""
    chosen = choose_behavior_symbols(question, symbols)
    if not chosen:
        return []
    concepts = set(behavior_concepts(question))
    primaries = {behavior["concepts"][0] for behavior in fired_behaviors(question)}
    preferred = _preferred_terms(question)
    best = _behavior_rank(chosen[0], concepts, primaries, preferred)[:5]
    return [
        symbol
        for symbol in chosen
        if _behavior_rank(symbol, concepts, primaries, preferred)[:5] == best
    ]


def leading_behavior_symbol(question: str, symbols: list[dict]) -> dict | None:
    """Return the one symbol that outranks the others, when the source can be explained on its own."""
    tied = tied_behavior_symbols(question, symbols)
    if len(tied) == 1:
        return tied[0]
    return None


def _name_parts(name: str) -> set[str]:
    return {part for part in name.lower().split("_") if part}


def _preferred_terms(question: str) -> set[str]:
    found: set[str] = set()
    for behavior in fired_behaviors(question):
        for word in behavior.get("prefer", ()):
            found.add(word)
    return found


def _behavior_rank(symbol: dict, concepts: set[str], primaries: set[str], preferred: set[str] | None = None) -> tuple:
    parts = _name_parts(str(symbol.get("name") or ""))
    bare_concept = int(bool(parts) and parts <= concepts and len(parts) == 1)
    return (
        -len(parts & concepts),
        -int(bool(parts & (preferred or set()))),
        -int(bool(parts & primaries)),
        bare_concept,
        len(parts),
        str(symbol.get("path") or ""),
        str(symbol.get("qualified_name") or ""),
    )


def _head(qualified_name: str) -> str:
    return qualified_name.split(".", 1)[0].lower()


def _is_test(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return "/tests/" in f"/{normalized}" or name.startswith("test_")
