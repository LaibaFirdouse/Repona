"""Whether a question may read the active generation, and whether one refresh may start."""

from __future__ import annotations

from app.ingestion.versions import (
    CHUNKING_VERSION,
    INCLUSION_RULES_VERSION,
    LEXICAL_INDEX_VERSION,
    PARSER_VERSION,
)


def versions_match(generation, fingerprint: str) -> bool:
    if generation is None:
        return False
    return (
        generation.status == "ready"
        and generation.gc_claimed_at is None
        and generation.parser_version == PARSER_VERSION
        and generation.chunking_version == CHUNKING_VERSION
        and generation.lexical_index_version == LEXICAL_INDEX_VERSION
        and generation.inclusion_rules_version == INCLUSION_RULES_VERSION
        and generation.embedding_config_fingerprint == fingerprint
    )


def question_plan(
    upstream: str,
    remote_sha: str | None,
    active_sha: str | None,
    active_ready: bool,
    versions_match: bool,
    source_valid: bool,
    refresh_state: str,
    lease_held: bool,
) -> tuple[str, str]:
    """Return `(answer, build)`.

    `answer` is serve, wait, revoke, or unavailable.
    `build` is none, start, or adopt.
    A matching commit is served from the active generation, including during a rebuild.
    A different commit is not served from the previous generation.
    """
    if upstream == "unauthorized":
        return "revoke", "none"
    if upstream != "authorized" or not remote_sha:
        return "unavailable", "none"
    same_commit = bool(active_ready and source_valid and active_sha == remote_sha)
    current = same_commit and versions_match
    answer = "serve" if same_commit else "wait"
    if current:
        return answer, "none"
    if refresh_state == "running" and lease_held:
        return answer, "none"
    if refresh_state == "running":
        return answer, "adopt"
    return answer, "start"
