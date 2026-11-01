"""Code-aware tokenization shared by the full-text index and full-text queries.

PostgreSQL's default text parser treats `self.signer.sign` as a single host-like token and
`deriveKey` as a single word, so a question about "sign" or "key" never matches them. The index
side appends the parts of compound identifiers to the text it feeds `to_tsvector`; the query
side splits the question the same way and ORs the words together, so a natural-language question
matches chunks that contain any of its distinctive words.
"""

from __future__ import annotations

import re

_IDENTIFIER_CHAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_CAMEL_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+[0-9]*|[A-Z]+[0-9]*|[0-9]+")
# camelCase / PascalCase humps, including acronym runs such as HTTPServer.
_CAMEL_BOUNDARY = re.compile(r"[a-z0-9][A-Z]|[A-Z][A-Z][a-z]")
# Upper bound on appended words per chunk; keeps tsvectors far below PostgreSQL's 1 MB limit.
_MAX_EXTRA_WORDS = 4000

# Words that carry no retrieval signal in a question about a codebase.
_QUERY_STOPWORDS = frozenset(
    """
    a an the is are was were be been being am
    what where when how why which who whom whose
    does do did done doing can could should would will shall may might must
    this that these those there here then than
    in on at to for of and or not no nor with without from into onto about over under by as if else
    it its they them their we our you your me my he she his her
    explain describe show find tell give list walk
    code codebase repo repository project
    file files function functions method methods class classes
    work works working happen happens happened
    get gets got use used uses using make makes made sure
    way ways also just some any all each every other more most much many such only own same
    so too very like really actually exactly
    implemented implement implementation handle handled handles
    part parts thing things step steps
    user assistant asked answered follow question
    """.split()
)
# Identifier parts too generic to help even when they come from a compound name.
_IDENTIFIER_NOISE = frozenset({"self", "cls", "this", "the"})


def identifier_parts(identifier: str) -> list[str]:
    """Lowercase words of a compound identifier.

    `self.signerKey.derive_key` -> self, signer, key, signerkey, derive, key.
    A camelCase piece also keeps its joined form so an exact `signerKey` search still matches.
    """
    parts: list[str] = []
    for piece in re.split(r"[._]+", identifier):
        if not piece:
            continue
        words = [word.lower() for word in _CAMEL_PART.findall(piece) if word]
        parts.extend(words)
        if len(words) > 1:
            parts.append(piece.lower())
    return parts


def is_compound_identifier(identifier: str) -> bool:
    core = identifier.strip("_")
    return "." in core or "_" in core or bool(_CAMEL_BOUNDARY.search(core))


def code_search_text(text: str) -> str:
    """Text for `to_tsvector`: the original text plus the parts of its compound identifiers.

    Only the full-text index uses this. The stored chunk, its byte range and its embedding
    input are unchanged.
    """
    if not text:
        return text
    extra: list[str] = []
    seen: set[str] = set()
    for match in _IDENTIFIER_CHAIN.finditer(text):
        token = match.group(0)
        if not is_compound_identifier(token):
            continue
        for part in identifier_parts(token):
            if len(part) < 2 or part in seen:
                continue
            seen.add(part)
            extra.append(part)
        if len(extra) >= _MAX_EXTRA_WORDS:
            break
    if not extra:
        return text
    return text + "\n" + " ".join(extra[:_MAX_EXTRA_WORDS])


def lexical_query_words(queries: list[str] | str, *, limit: int = 12) -> list[str]:
    """Distinct search words from free-text queries, split the same way as the index.

    Stopwords are dropped from plain words. Parts of compound identifiers named in the question
    (`handle_request`, `Signer.sign`) are kept even when they look generic.
    """
    if isinstance(queries, str):
        queries = [queries]
    words: list[str] = []
    for query in queries:
        for match in _IDENTIFIER_CHAIN.finditer(query or ""):
            token = match.group(0)
            compound = is_compound_identifier(token)
            candidates = identifier_parts(token) if compound else [token.strip("_").lower()]
            for word in candidates:
                if len(word) < 3 or word.isdigit() or word in words:
                    continue
                if word in _IDENTIFIER_NOISE:
                    continue
                if not compound and word in _QUERY_STOPWORDS:
                    continue
                words.append(word)
                if len(words) >= limit:
                    return words
    return words


def tsquery_term(word: str) -> str:
    """One `to_tsquery('simple', ...)` operand for a search word.

    The 'simple' configuration does not stem, so words of four or more letters become prefix
    matches on a lightly stemmed form: `indexed` -> `index:*` (index, indexes, indexing),
    `parsed` -> `pars:*` (parse, parser, parsing). Three-letter words stay exact (`key`, `api`).
    Words contain only [a-z0-9], so the operand is always valid tsquery syntax.
    """
    if len(word) < 4:
        return word
    stem = word
    for suffix in ("ing", "ed", "es", "s"):
        if stem.endswith(suffix) and len(stem) - len(suffix) >= 4:
            stem = stem[: -len(suffix)]
            break
    return f"{stem}:*"
