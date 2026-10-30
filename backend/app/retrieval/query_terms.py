"""Deterministic question normalization and lexical query variants."""

from __future__ import annotations

import re


def question_qualified_names(question: str) -> list[str]:
    found: list[str] = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", question):
        if token not in found:
            found.append(token)
    return found


def question_identifiers(question: str) -> list[str]:
    found: list[str] = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question):
        if len(token) < 3 or token in found:
            continue
        found.append(token)
    return found


_QUESTION_PATH_EXTENSIONS = frozenset({
    "py", "pyi", "js", "jsx", "ts", "tsx", "mjs", "cjs",
    "go", "rs", "java", "kt", "kts", "scala", "cs",
    "c", "h", "cc", "cpp", "hpp", "php", "rb", "swift",
    "sql", "html", "htm", "css", "scss", "sass", "less",
    "json", "jsonc", "yaml", "yml", "toml", "ini", "cfg",
    "conf", "md", "markdown", "rst", "txt", "sh", "bash",
    "xml", "gradle", "properties", "proto", "graphql",
    "tf", "hcl", "dart", "lua", "ex", "exs", "vue",
    "svelte", "astro", "ipynb", "lock",
})


def question_paths(question: str) -> list[str]:
    # Preserve existing support for directory paths, e.g. src/utils.
    directory_paths = re.findall(
        r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+",
        question,
    )

    # Also recognise standalone filenames, e.g. styles.css.
    filename_candidates = re.findall(
        r"(?<![A-Za-z0-9_./-])[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]{1,12}(?![A-Za-z0-9_./-])",
        question,
    )

    bare_filenames = [
        token
        for token in filename_candidates
        if token.rsplit(".", 1)[-1].lower() in _QUESTION_PATH_EXTENSIONS
    ]

    # Preserve their order in the original question and remove duplicates.
    candidates = directory_paths + bare_filenames
    candidates.sort(key=question.find)

    result = []
    for token in candidates:
        normalized = token.strip("./")
        if normalized and normalized not in result:
            result.append(normalized)

    return result

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "what",
        "where",
        "when",
        "how",
        "why",
        "does",
        "do",
        "did",
        "can",
        "could",
        "should",
        "would",
        "this",
        "that",
        "these",
        "those",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "and",
        "or",
        "with",
        "from",
        "into",
        "about",
        "explain",
        "describe",
        "show",
        "find",
        "tell",
        "me",
        "code",
        "file",
        "function",
        "method",
        "class",
        "work",
        "works",
    }
)

_QUOTED = re.compile(r"""['"]([^'"]{2,120})['"]""")
_CAMEL = re.compile(r"[a-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+")
_SNAKE = re.compile(r"_?[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
_ERROR = re.compile(r"[A-Z][a-zA-Z0-9_]*Error|[A-Z][a-zA-Z0-9_]*Exception")


def normalize_question(question: str) -> str:
    return " ".join(question.strip().split())


def extract_search_terms(question: str) -> list[str]:
    """Identifiers, paths, quoted strings, and distinctive tokens from the question."""
    normalized = normalize_question(question)
    found: list[str] = []
    for token in question_qualified_names(normalized):
        _append(found, token)
    for token in question_paths(normalized):
        _append(found, token)
    for match in _QUOTED.findall(normalized):
        _append(found, match.strip())
    for match in _CAMEL.findall(normalized):
        _append(found, match)
    for match in _SNAKE.findall(normalized):
        _append(found, match)
    for match in _ERROR.findall(normalized):
        _append(found, match)
    for token in question_identifiers(normalized):
        if token.lower() not in _STOPWORDS:
            _append(found, token)
    return found[:24]


def build_lexical_queries(question: str) -> list[str]:
    """A small set of complementary plain-text queries for PostgreSQL full-text search."""
    normalized = normalize_question(question)
    terms = extract_search_terms(question)
    queries: list[str] = []
    if normalized:
        queries.append(normalized)
    if terms:
        queries.append(" ".join(terms[:8]))
        if len(terms) > 1:
            queries.append(" ".join(terms[:4]))
    deduped: list[str] = []
    for item in queries:
        key = item.strip().lower()
        if key and key not in {existing.strip().lower() for existing in deduped}:
            deduped.append(item.strip())
    return deduped[:4] or [normalized or question.strip()]


def _append(found: list[str], token: str) -> None:
    token = token.strip()
    if not token or token in found:
        return
    found.append(token)
