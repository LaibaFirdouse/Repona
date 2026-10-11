"""Source-byte checks and the generation ready checklist."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.ingestion.assemble import SourceBlob
from app.ingestion.inclusion import is_included
from app.storage.identity import content_hash, point_id


class ChecklistError(RuntimeError):
    pass


def validate_worktree_bytes(git_dir: Path, commit_sha: str, work_tree: Path, blobs: list[SourceBlob]) -> None:
    head = _git_text(work_tree, "rev-parse", "HEAD")
    if head != commit_sha:
        raise ChecklistError(f"work tree HEAD is {head}, expected {commit_sha}")
    common = Path(_git_text(work_tree, "rev-parse", "--git-common-dir")).resolve()
    if common != git_dir.resolve():
        raise ChecklistError("work tree does not use the repository git dir")
    included = [blob for blob in blobs if is_included(blob.relative_path, blob.mode)]
    for blob in included:
        digest = content_hash(blob.payload)
        on_disk = work_tree / blob.relative_path
        if not on_disk.is_file():
            raise ChecklistError(f"missing work tree file {blob.relative_path}")
        disk_bytes = on_disk.read_bytes()
        if content_hash(disk_bytes) != digest or len(disk_bytes) != len(blob.payload):
            raise ChecklistError(f"work tree bytes differ for {blob.relative_path}")
        if not blob.git_blob_id:
            raise ChecklistError(f"missing git blob id for {blob.relative_path}")


def point_is_valid(
    payload: dict,
    *,
    chunk_id_value: str,
    repository_id: str,
    file_id_value: str,
    parser_version: str,
    chunking_version: str,
    span_hash: str,
    embedding_input_hash: str,
    fingerprint: str,
    point_identifier: str,
    collection_size: int,
    collection_distance: str,
    expected_size: int,
) -> bool:
    if collection_size != expected_size or collection_distance != "Cosine":
        return False
    if payload.get("chunk_id") != chunk_id_value:
        return False
    if any(key in payload for key in ("index_generation_id", "lexical_index_version", "inclusion_rules_version", "build_attempt")):
        return False
    if point_identifier != str(point_id(chunk_id_value, embedding_input_hash, fingerprint)):
        return False
    checks = (
        payload.get("repository_id") == repository_id
        and payload.get("file_id") == file_id_value
        and payload.get("parser_version") == parser_version
        and payload.get("chunking_version") == chunking_version
        and payload.get("span_hash") == span_hash
        and payload.get("embedding_input_hash") == embedding_input_hash
        and payload.get("embedding_config_fingerprint") == fingerprint
    )
    return checks


def generation_link_is_valid(
    *,
    link_exists: bool,
    link_fingerprint: str | None,
    link_input_hash: str | None,
    payload_fingerprint: str,
    payload_input_hash: str,
    generation_fingerprint: str,
    file_linked: bool,
) -> bool:
    return (
        link_exists
        and file_linked
        and link_fingerprint == payload_fingerprint == generation_fingerprint
        and link_input_hash == payload_input_hash
    )


def edge_set_matches(present_edge_ids: set[str], expected_edge_ids: set[str]) -> bool:
    return present_edge_ids == expected_edge_ids


def _git_text(work_tree: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(work_tree), *args],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ChecklistError(completed.stderr.decode("utf-8", errors="replace").strip())
    return completed.stdout.decode("utf-8").strip()
